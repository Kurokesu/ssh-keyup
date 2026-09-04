# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for ssh_keyup argument handling and helpers."""

import contextlib
import socket
import sys

import pytest

import ssh_keyup


def parse(argv):
    sys.argv = ["ssh-keyup"] + argv
    return ssh_keyup.parse_args()


class TestSplitTarget:
    def test_user_and_host(self):
        assert ssh_keyup.split_target("pi@192.168.1.23") == \
            ("pi", "192.168.1.23")

    def test_host_only(self):
        assert ssh_keyup.split_target("rpi-5.local") == (None, "rpi-5.local")

    def test_empty_user(self):
        assert ssh_keyup.split_target("@host") == (None, "host")

    def test_user_with_at_sign(self):
        assert ssh_keyup.split_target("a@b@host") == ("a@b", "host")


class TestParseArgs:
    def test_positional_target_and_alias(self):
        args = parse(["pi@192.168.1.23", "mypi"])
        assert args.host == "192.168.1.23"
        assert args.user == "pi"
        assert args.alias == "mypi"

    def test_host_only_target(self):
        args = parse(["rpi-5.local"])
        assert args.host == "rpi-5.local"
        assert args.user is None
        assert args.alias is None

    def test_flags_still_work(self):
        args = parse(["--host", "rpi-5", "--user", "pi", "--alias", "mypi"])
        assert (args.host, args.user, args.alias) == ("rpi-5", "pi", "mypi")

    @pytest.mark.parametrize("argv", [
        ["1.2.3.4", "--host", "5.6.7.8"],
        ["pi@1.2.3.4", "--user", "root"],
        ["1.2.3.4", "myalias", "--alias", "other"],
    ])
    def test_flag_positional_conflict(self, argv):
        with pytest.raises(SystemExit) as exc:
            parse(argv)
        assert exc.value.code == 2


class TestSanitizeAlias:
    @pytest.mark.parametrize("raw,expected", [
        ("mypi", "mypi"),
        ("rpi 5", "rpi-5"),
        ("rpi.local", "rpi-local"),
        ("under_score", "under_score"),
        ("", "host"),
    ])
    def test_sanitize(self, raw, expected):
        assert ssh_keyup.sanitize_alias(raw, quiet=True) == expected


class TestIsIp:
    @pytest.mark.parametrize("value,expected", [
        ("192.168.1.1", True),
        ("::1", True),
        ("rpi-5", False),
        ("999.1.1.1", False),
    ])
    def test_is_ip(self, value, expected):
        assert ssh_keyup.is_ip(value) is expected


SAMPLE_CONFIG = """# hand-written entry
Host manual
    HostName 10.0.0.1

#ssh-keyup:begin mypi 2026-07-20
Host mypi
    HostName 192.168.1.23
    User pi
    IdentityFile ~/.ssh/id_ed25519_mypi
#ssh-keyup:end mypi

#ssh-keyup:begin jet 2026-07-21
Host jet
    HostName 192.168.1.30
    User nvidia
    IdentityFile ~/.ssh/id_ed25519_jet
#ssh-keyup:end jet
"""


class TestCollectEntries:
    def test_parses_managed_blocks(self):
        entries = ssh_keyup.SSHConfig.collect_entries(SAMPLE_CONFIG)
        assert [e["alias"] for e in entries] == ["mypi", "jet"]
        assert entries[0]["host"] == "192.168.1.23"
        assert entries[0]["user"] == "pi"
        assert entries[0]["date"] == "2026-07-20"
        assert entries[1]["key"] == "~/.ssh/id_ed25519_jet"

    def test_ignores_unmanaged(self):
        text = "Host manual\n    HostName 10.0.0.1\n"
        assert ssh_keyup.SSHConfig.collect_entries(text) == []

    def test_empty_text(self):
        assert ssh_keyup.SSHConfig.collect_entries("") == []


class TestCheckExisting:
    def test_overwrite_splices_block_keeps_disk(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config"
        cfg.write_text(SAMPLE_CONFIG)
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: True)
        base, overwriting = ssh_keyup.SSHConfig.check_existing(cfg, "mypi")
        assert overwriting is True
        assert "#ssh-keyup:begin mypi" not in base
        assert "#ssh-keyup:begin jet" in base
        assert cfg.read_text() == SAMPLE_CONFIG

    def test_no_managed_entry(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("Host manual\n    HostName 10.0.0.1\n")
        base, overwriting = ssh_keyup.SSHConfig.check_existing(cfg, "mypi")
        assert overwriting is False
        assert base == cfg.read_text()

    def test_decline_overwrite_keeps_config(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config"
        cfg.write_text(SAMPLE_CONFIG)
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: False)
        with pytest.raises(SystemExit) as exc:
            ssh_keyup.SSHConfig.check_existing(cfg, "mypi")
        assert exc.value.code == 0
        assert cfg.read_text() == SAMPLE_CONFIG

    def test_remove_stale_writes_base(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config"
        cfg.write_text(SAMPLE_CONFIG)
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: True)
        base, _ = ssh_keyup.SSHConfig.check_existing(cfg, "mypi")
        ssh_keyup.SSHConfig.remove_stale(cfg, base)
        text = cfg.read_text()
        assert "#ssh-keyup:begin mypi" not in text
        assert "#ssh-keyup:begin jet" in text
        assert text == base


class TestRemoveEntry:
    def test_removes_only_target_block(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        cfg = tmp_path / "config"
        cfg.write_text(SAMPLE_CONFIG)
        ssh_keyup.SSHConfig.remove_entry(cfg, "mypi")
        text = cfg.read_text()
        assert "mypi" not in text
        assert "#ssh-keyup:begin jet" in text
        assert "Host manual" in text

    def test_deletes_key_pair(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        cfg = ssh_dir / "config"
        cfg.write_text(SAMPLE_CONFIG)
        key = ssh_dir / "id_ed25519_mypi"
        pub = ssh_dir / "id_ed25519_mypi.pub"
        key.write_text("private")
        pub.write_text("public")
        ssh_keyup.SSHConfig.remove_entry(cfg, "mypi")
        assert not key.exists()
        assert not pub.exists()

    def test_missing_alias_exits(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(SAMPLE_CONFIG)
        with pytest.raises(SystemExit) as exc:
            ssh_keyup.SSHConfig.remove_entry(cfg, "ghost")
        assert exc.value.code == 1


class TestManageArgs:
    def test_list_remove_conflict(self):
        with pytest.raises(SystemExit) as exc:
            parse(["--list", "--remove", "x"])
        assert exc.value.code == 2

    def test_list_with_setup_args(self):
        with pytest.raises(SystemExit) as exc:
            parse(["pi@1.2.3.4", "--list"])
        assert exc.value.code == 2


class FakeRunner:
    """Stand-in for Runner, returns canned output and records commands."""

    def __init__(self, rc=0, out=""):
        self.rc = rc
        self.out = out
        self.cmds = []

    def run_stdout(self, cmd, **kwargs):
        self.cmds.append(cmd)
        return self.rc, self.out

    def run_capture(self, cmd, **kwargs):
        self.cmds.append(cmd)
        return self.rc, self.out


SSH_G_OUTPUT = """\
host raspberrypi.local
hostname 192.168.1.23
user pi
port 2222
"""


class TestResolveSSHTarget:
    def test_reads_hostname_and_port(self):
        runner = FakeRunner(out=SSH_G_OUTPUT)
        assert ssh_keyup.resolve_ssh_target(runner, "mypi") == \
            ("192.168.1.23", 2222)

    def test_defaults_port_when_absent(self):
        runner = FakeRunner(out="hostname 10.0.0.5\n")
        assert ssh_keyup.resolve_ssh_target(runner, "mypi") == \
            ("10.0.0.5", 22)

    def test_ignores_unparsable_port(self):
        runner = FakeRunner(out="hostname 10.0.0.5\nport auto\n")
        assert ssh_keyup.resolve_ssh_target(runner, "mypi") == \
            ("10.0.0.5", 22)

    def test_none_when_ssh_fails(self):
        runner = FakeRunner(rc=255)
        assert ssh_keyup.resolve_ssh_target(runner, "mypi") is None

    def test_none_without_hostname(self):
        runner = FakeRunner(out="user pi\nport 22\n")
        assert ssh_keyup.resolve_ssh_target(runner, "mypi") is None


class TestResolveHost:
    def test_substitutes_alias_target(self):
        runner = FakeRunner(out=SSH_G_OUTPUT)
        assert ssh_keyup.resolve_host(runner, "mypi") == \
            ("192.168.1.23", 2222)

    def test_falls_back_to_typed_host(self):
        runner = FakeRunner(rc=255)
        assert ssh_keyup.resolve_host(runner, "rpi-5.local") == \
            ("rpi-5.local", 22)

    def test_hints_when_alias_differs(self, capsys):
        runner = FakeRunner(out=SSH_G_OUTPUT)
        ssh_keyup.resolve_host(runner, "mypi")
        assert "resolves to 192.168.1.23" in capsys.readouterr().out

    def test_quiet_when_host_matches(self, capsys):
        runner = FakeRunner(out="hostname RPI-5\n")
        assert ssh_keyup.resolve_host(runner, "rpi-5") == ("RPI-5", 22)
        assert capsys.readouterr().out == ""


class TestBuildBlock:
    def test_omits_port_line_on_default(self):
        block = ssh_keyup.SSHConfig._build_block(
            "mypi", "10.0.0.5", "pi", "mypi")
        assert "Port" not in block
        assert "    HostName 10.0.0.5\n" in block

    def test_writes_port_line_when_custom(self):
        block = ssh_keyup.SSHConfig._build_block(
            "mypi", "10.0.0.5", "pi", "mypi", 2222)
        assert "    Port 2222\n" in block


class TestSSHCommand:
    def test_omits_port_flag_on_default(self):
        runner = FakeRunner()
        ssh_keyup.Deployer._ssh_cmd(runner, "pi@h", "cmd", "key")
        assert "-p" not in runner.cmds[0]

    def test_passes_port_flag_when_custom(self):
        runner = FakeRunner()
        ssh_keyup.Deployer._ssh_cmd(runner, "pi@h", "cmd", "key",
                                    port=2222)
        cmd = runner.cmds[0]
        assert cmd[cmd.index("-p") + 1] == "2222"

    @pytest.mark.parametrize("accept_new", [False, True])
    def test_always_verbose(self, accept_new):
        runner = FakeRunner()
        ssh_keyup.Deployer._ssh_cmd(runner, "pi@h", "cmd", "key",
                                    accept_new=accept_new)
        assert "-v" in runner.cmds[0]


# Verbose stderr after a successful login and a failing remote command
AUTH_OK_STDERR = """\
OpenSSH_9.5p2, LibreSSL 3.8.2
debug1: Connecting to router [192.168.188.1] port 22.
Authenticated to router ([192.168.188.1]:22) using "password".
debug1: Sending command: mkdir -p ~/.ssh && chmod 700 ~/.ssh
bad command name mkdir (line 1 column 1)
Transferred: sent 2204, received 2776 bytes, in 0.1 seconds
Bytes per second: sent 31485.7, received 39657.2
debug1: Exit status 1
"""

# Verbose stderr after a rejected password
AUTH_FAIL_STDERR = """\
OpenSSH_9.5p2, LibreSSL 3.8.2
debug1: Connecting to pi [10.0.0.5] port 22.
Permission denied, please try again.
Permission denied, please try again.
pi@10.0.0.5: Permission denied (publickey,password).
"""


class TestReportFailure:
    def test_detects_login(self):
        assert ssh_keyup.Deployer._is_authenticated(AUTH_OK_STDERR)
        assert not ssh_keyup.Deployer._is_authenticated(AUTH_FAIL_STDERR)

    def test_error_lines_keep_remote_output_only(self):
        lines = ssh_keyup.Deployer._error_lines(AUTH_OK_STDERR)
        assert lines == ["bad command name mkdir (line 1 column 1)"]

    def test_error_lines_dedupe_ssh_errors(self):
        lines = ssh_keyup.Deployer._error_lines(AUTH_FAIL_STDERR)
        assert lines == [
            "Permission denied, please try again.",
            "pi@10.0.0.5: Permission denied (publickey,password).",
        ]

    def test_remote_failure_is_not_a_login_failure(self, capsys):
        ssh_keyup.Deployer._report_failure(AUTH_OK_STDERR)
        out = capsys.readouterr().out
        assert "install command failed" in out
        assert "bad command name mkdir" in out
        assert "credentials" not in out

    def test_login_failure_names_credentials(self, capsys):
        ssh_keyup.Deployer._report_failure(AUTH_FAIL_STDERR)
        out = capsys.readouterr().out
        assert "Check host and credentials" in out
        assert "Permission denied (publickey,password)" in out
        assert "OpenSSH_" not in out


class TestDeploy:
    def test_remote_failure_reported_after_login(self, tmp_path, capsys):
        pub = tmp_path / "id_ed25519_r.pub"
        pub.write_text("ssh-ed25519 AAAA test\n")
        runner = FakeRunner(rc=1, out=AUTH_OK_STDERR)
        assert ssh_keyup.Deployer.deploy(runner, "admin", "router", pub) \
            is False
        out = capsys.readouterr().out
        assert "install command failed" in out
        assert "credentials" not in out
        assert len(runner.cmds) == 1


class TestAtomicWrite:
    @pytest.mark.parametrize("text,expected", [
        ("Host a\n\n\n", "Host a\n"),
        ("Host a", "Host a\n"),
        ("", ""),
    ])
    def test_normalizes_trailing_newline(self, tmp_path, text, expected):
        cfg = tmp_path / "config"
        ssh_keyup.SSHConfig._atomic_write(cfg, text)
        assert cfg.read_text() == expected

    def test_leaves_no_temp_file_behind(self, tmp_path):
        cfg = tmp_path / "config"
        ssh_keyup.SSHConfig._atomic_write(cfg, "Host a\n")
        assert [p.name for p in tmp_path.iterdir()] == ["config"]


class TestProbePort:
    def test_none_when_connectable(self, monkeypatch):
        monkeypatch.setattr(ssh_keyup.socket, "create_connection",
                            lambda *a, **k: contextlib.nullcontext())
        assert ssh_keyup.probe_port("rpi", 22) is None

    @pytest.mark.parametrize("exc,fragment", [
        (socket.gaierror(), "Could not resolve hostname"),
        (ConnectionRefusedError(), "Connection refused"),
        (socket.timeout(), "No response from"),
        (OSError("network is down"), "Cannot reach"),
    ])
    def test_failure_reasons(self, monkeypatch, exc, fragment):
        def boom(*a, **k):
            raise exc
        monkeypatch.setattr(ssh_keyup.socket, "create_connection", boom)
        result = ssh_keyup.probe_port("rpi", 22)
        assert result is not None
        assert fragment in result[0]


class TestCheckReachable:
    def test_reports_ok_when_reachable(self, monkeypatch, capsys):
        monkeypatch.setattr(ssh_keyup, "probe_port", lambda h, p: None)
        ssh_keyup.check_reachable("rpi")
        assert "ok" in capsys.readouterr().out

    def test_continues_when_user_accepts(self, monkeypatch):
        monkeypatch.setattr(ssh_keyup, "probe_port",
                            lambda h, p: ("no route", "detail"))
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: True)
        ssh_keyup.check_reachable("rpi")

    def test_exits_when_user_declines(self, monkeypatch):
        monkeypatch.setattr(ssh_keyup, "probe_port",
                            lambda h, p: ("no route", "detail"))
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: False)
        with pytest.raises(SystemExit) as exc:
            ssh_keyup.check_reachable("rpi")
        assert exc.value.code == 0
