# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for ssh_keyup argument handling and helpers."""

import signal
import socket
import sys
import threading
import time
from pathlib import Path

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


class TestSplitHostPort:
    @pytest.mark.parametrize("value, expected", [
        ("rpi-5.local", ("rpi-5.local", None)),
        ("192.168.1.23:2222", ("192.168.1.23", 2222)),
        ("fe80::1", ("fe80::1", None)),
        ("[fe80::1]", ("fe80::1", None)),
        ("[fe80::1]:2222", ("fe80::1", 2222)),
    ])
    def test_split(self, value, expected):
        assert ssh_keyup.split_host_port(value) == expected

    @pytest.mark.parametrize("value", [
        "host:", "host:ssh", "host:0", "host:65536", "[fe80::1", "[fe80::1]x",
    ])
    def test_rejects_bad_input(self, value):
        with pytest.raises(ValueError):
            ssh_keyup.split_host_port(value)


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

    def test_port_flag(self):
        assert parse(["rpi-5", "--port", "2222"]).port == 2222
        assert parse(["rpi-5"]).port is None

    def test_key_type_defaults_to_ed25519(self):
        assert parse(["rpi-5"]).key_type == "ed25519"
        assert parse(["rpi-5", "--key-type", "rsa"]).key_type == "rsa"

    def test_rejects_unknown_key_type(self):
        with pytest.raises(SystemExit):
            parse(["rpi-5", "--key-type", "dsa"])

    @pytest.mark.parametrize("argv", [
        ["rpi-5", "--port", "0"],
        ["rpi-5", "--port", "70000"],
        ["--list", "--port", "2222"],
    ])
    def test_port_errors(self, argv):
        with pytest.raises(SystemExit) as exc:
            parse(argv)
        assert exc.value.code == 2

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


class FakeStdin:
    def fileno(self):
        return 0

    def isatty(self):
        return True


class TestPending:
    @staticmethod
    def _tty(monkeypatch):
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        monkeypatch.setattr(ssh_keyup, "DOT_PERIOD", 0.01)

    def test_static_without_terminal(self, capsys):
        assert ssh_keyup.cli.pending("Checking", lambda: 42) == 42
        assert capsys.readouterr().out == "Checking ... "

    def test_passes_arguments(self, capsys):
        assert ssh_keyup.cli.pending("Checking", max, 3, 7) == 7

    def test_animates_then_leaves_static_line(self, monkeypatch, capsys):
        self._tty(monkeypatch)
        threads = threading.active_count()
        assert ssh_keyup.cli.pending("Checking", time.sleep, 0.1) is None
        out = capsys.readouterr().out
        assert out.startswith("Checking ")
        assert out.count("...\b\b\b") > 2
        assert out.endswith("... ")
        assert threading.active_count() == threads

    def test_error_in_work_propagates(self, monkeypatch, capsys):
        self._tty(monkeypatch)

        def boom():
            raise UnicodeError("label too long")

        with pytest.raises(UnicodeError):
            ssh_keyup.cli.pending("Checking", boom)
        assert capsys.readouterr().out.endswith("... ")

    def test_interrupt_stops_waiting(self, monkeypatch, capsys):
        self._tty(monkeypatch)
        joins = []

        def interrupted_join(self, timeout=None):
            joins.append(timeout)
            raise KeyboardInterrupt

        monkeypatch.setattr(threading.Thread, "join", interrupted_join)
        with pytest.raises(KeyboardInterrupt):
            ssh_keyup.cli.pending("Checking", time.sleep, 1)
        assert joins == [0.01]
        assert capsys.readouterr().out.endswith("Checking ... ")


class TestAskYn:
    @pytest.mark.parametrize("reads, default, expected", [
        ([["enter"]], True, True),
        ([["enter"]], False, False),
        ([["left"], ["enter"]], False, True),
        ([["right"], ["right"], ["enter"]], True, True),
        ([["y", "enter"]], False, True),
        ([["n"], ["enter"]], True, False),
        ([["esc"]], True, False),
        ([["x", ""], ["enter"]], True, True),
    ])
    def test_answer(self, monkeypatch, reads, default, expected):
        monkeypatch.setattr(sys, "stdin", FakeStdin())
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        chunks = iter(reads)
        monkeypatch.setattr(ssh_keyup.CLI, "_read_keys", lambda: next(chunks))
        assert ssh_keyup.CLI.ask_yn("Q", default) is expected

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows console")
    def test_drops_keys_typed_before_prompt(self, monkeypatch):
        buffered = ["\x1b", "n"]
        monkeypatch.setattr(ssh_keyup.msvcrt, "kbhit", lambda: bool(buffered))
        monkeypatch.setattr(ssh_keyup.msvcrt, "getwch", buffered.pop)
        monkeypatch.setattr(sys, "stdin", FakeStdin())
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        monkeypatch.setattr(ssh_keyup.CLI, "_read_keys", lambda: ["enter"])
        assert ssh_keyup.CLI.ask_yn("Q", default=True) is True
        assert buffered == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal only")
class TestReadKey:
    @pytest.fixture(autouse=True)
    def raw_tty(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", FakeStdin())
        monkeypatch.setattr(ssh_keyup.termios, "tcgetattr", lambda fd: [])
        monkeypatch.setattr(ssh_keyup.termios, "tcsetattr", lambda *a: None)
        monkeypatch.setattr(ssh_keyup.tty, "setraw", lambda fd: None)

    def _feed(self, monkeypatch, data):
        reads = []

        def fake_read(fd, n):
            reads.append(n)
            return data

        monkeypatch.setattr(ssh_keyup.os, "read", fake_read)
        return reads

    @pytest.mark.parametrize("data, keys", [
        (b"\x1b", ["esc"]),
        (b"\x1b[D", ["left"]),
        (b"\x1b[C", ["right"]),
        (b"\r", ["enter"]),
        (b"y", ["y"]),
        (b"n\r", ["n", "enter"]),
        (b"\x1b[D\r", ["left", "enter"]),
        (b"\x1by", ["esc", "y"]),
    ])
    def test_one_read_splits_keys(self, monkeypatch, data, keys):
        reads = self._feed(monkeypatch, data)
        assert ssh_keyup.CLI._read_keys() == keys
        assert len(reads) == 1

    @pytest.mark.parametrize("data", [b"\x03", b"y\x03"])
    def test_ctrl_c_interrupts(self, monkeypatch, data):
        self._feed(monkeypatch, data)
        with pytest.raises(KeyboardInterrupt):
            ssh_keyup.CLI._read_keys()


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
        base = ssh_keyup.SSHConfig.check_existing(cfg, "mypi")
        assert "#ssh-keyup:begin mypi" not in base
        assert "#ssh-keyup:begin jet" in base
        assert cfg.read_text() == SAMPLE_CONFIG

    def test_no_managed_entry(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("Host manual\n    HostName 10.0.0.1\n")
        base = ssh_keyup.SSHConfig.check_existing(cfg, "mypi")
        assert base == cfg.read_text()

    def test_decline_overwrite_keeps_config(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config"
        cfg.write_text(SAMPLE_CONFIG)
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: False)
        with pytest.raises(SystemExit) as exc:
            ssh_keyup.SSHConfig.check_existing(cfg, "mypi")
        assert exc.value.code == 0
        assert cfg.read_text() == SAMPLE_CONFIG

    def test_missing_config(self, tmp_path):
        cfg = tmp_path / "config"
        assert ssh_keyup.SSHConfig.check_existing(cfg, "mypi") == ""

    def test_writing_base_drops_entry(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config"
        cfg.write_text(SAMPLE_CONFIG)
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: True)
        base = ssh_keyup.SSHConfig.check_existing(cfg, "mypi")
        ssh_keyup.SSHConfig.write(cfg, base)
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
    """Stand-in for Runner, returns canned output and records commands.

    Pass `results` as a list of (rc, out) to answer successive calls
    differently, the last entry repeats. An exception entry is raised.
    """

    def __init__(self, rc=0, out="", results=None, mode="native"):
        self.results = list(results) if results else [(rc, out)]
        self.cmds = []
        self.inputs = []
        self.mode = mode

    def check(self):
        pass

    def _next(self, cmd, kwargs):
        self.cmds.append(cmd)
        self.inputs.append(kwargs.get("input"))
        result = (self.results.pop(0) if len(self.results) > 1
                  else self.results[0])
        if isinstance(result, BaseException):
            raise result
        return result

    def run(self, cmd, **kwargs):
        return self._next(cmd, kwargs)[0]

    def run_stdout(self, cmd, **kwargs):
        return self._next(cmd, kwargs)

    def run_capture(self, cmd, **kwargs):
        return self._next(cmd, kwargs)


SSH_G_OUTPUT = """\
host raspberrypi.local
hostname 192.168.1.23
user pi
port 2222
"""


class TestResolveHost:
    def test_reads_hostname_and_port(self):
        runner = FakeRunner(out=SSH_G_OUTPUT)
        assert ssh_keyup.resolve_host(runner, "mypi") == \
            ("192.168.1.23", 2222)

    def test_defaults_port_when_absent(self):
        runner = FakeRunner(out="hostname 10.0.0.5\n")
        assert ssh_keyup.resolve_host(runner, "mypi") == ("10.0.0.5", 22)

    def test_ignores_unparsable_port(self):
        runner = FakeRunner(out="hostname 10.0.0.5\nport auto\n")
        assert ssh_keyup.resolve_host(runner, "mypi") == ("10.0.0.5", 22)

    def test_passes_explicit_port_to_ssh(self):
        runner = FakeRunner(out="hostname 10.0.0.5\nport 2200\n")
        assert ssh_keyup.resolve_host(runner, "mypi", 2200) == \
            ("10.0.0.5", 2200)
        cmd = runner.cmds[0]
        assert cmd[cmd.index("-p") + 1] == "2200"

    def test_omits_port_flag_without_explicit_port(self):
        runner = FakeRunner(out="hostname 10.0.0.5\n")
        ssh_keyup.resolve_host(runner, "mypi")
        assert "-p" not in runner.cmds[0]

    def test_falls_back_to_typed_host(self):
        runner = FakeRunner(rc=255)
        assert ssh_keyup.resolve_host(runner, "rpi-5.local") == \
            ("rpi-5.local", 22)

    def test_falls_back_without_hostname(self):
        runner = FakeRunner(out="user pi\nport 2222\n")
        assert ssh_keyup.resolve_host(runner, "mypi") == ("mypi", 22)

    def test_falls_back_to_explicit_port(self):
        runner = FakeRunner(rc=255)
        assert ssh_keyup.resolve_host(runner, "rpi-5.local", 2222) == \
            ("rpi-5.local", 2222)

    def test_hints_when_alias_differs(self, capsys):
        runner = FakeRunner(out=SSH_G_OUTPUT)
        ssh_keyup.resolve_host(runner, "mypi")
        assert "resolves to '192.168.1.23'" in capsys.readouterr().out

    def test_quiet_when_host_matches(self, capsys):
        runner = FakeRunner(out="hostname RPI-5\n")
        assert ssh_keyup.resolve_host(runner, "rpi-5") == ("RPI-5", 22)
        assert capsys.readouterr().out == ""


class TestGatherInput:
    @staticmethod
    def _gather(monkeypatch, argv, banner="", resolved=None):
        seen = {}

        def fake_resolve(runner, host, port=None):
            seen["host"], seen["port"] = host, port
            return resolved or host, port or 22

        def fake_check(host, port, fallback=None):
            seen["fallback"] = fallback
            return (*(fallback or (host, port)), banner)

        monkeypatch.setattr(ssh_keyup, "resolve_host", fake_resolve)
        monkeypatch.setattr(ssh_keyup, "check_reachable", fake_check)
        monkeypatch.setattr("builtins.input", lambda *_: "")
        result = ssh_keyup.gather_input(parse(argv), FakeRunner())
        return result, seen

    def test_port_from_host(self, monkeypatch):
        (host, _, _, port, _), seen = self._gather(
            monkeypatch, ["pi@10.0.0.5:2222", "mypi"])
        assert (host, port) == ("10.0.0.5", 2222)
        assert (seen["host"], seen["port"]) == ("10.0.0.5", 2222)

    def test_port_from_flag(self, monkeypatch):
        (host, _, _, port, _), _ = self._gather(
            monkeypatch, ["pi@10.0.0.5", "mypi", "--port", "2222"])
        assert (host, port) == ("10.0.0.5", 2222)

    def test_same_port_both_ways(self, monkeypatch):
        (_, _, _, port, _), _ = self._gather(
            monkeypatch, ["pi@10.0.0.5:2222", "mypi", "--port", "2222"])
        assert port == 2222

    def test_linux_by_default(self, monkeypatch, capsys):
        (*_, target_os), _ = self._gather(monkeypatch, ["pi@10.0.0.5", "mypi"])
        assert target_os is ssh_keyup.TargetOS.LINUX
        assert "detected" not in capsys.readouterr().out

    def test_detects_routeros_from_banner(self, monkeypatch, capsys):
        (*_, target_os), _ = self._gather(
            monkeypatch, ["admin@10.0.0.1", "router"], banner="SSH-2.0-ROSSSH")
        assert target_os is ssh_keyup.TargetOS.ROUTEROS
        assert "RouterOS detected" in capsys.readouterr().out

    def test_os_flag_overrides_detection(self, monkeypatch, capsys):
        (*_, target_os), _ = self._gather(
            monkeypatch, ["admin@10.0.0.1", "router", "--os", "routeros"])
        assert target_os is ssh_keyup.TargetOS.ROUTEROS
        assert "detected" not in capsys.readouterr().out

    def test_conflicting_ports_exit(self, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            self._gather(
                monkeypatch, ["pi@10.0.0.5:2222", "mypi", "--port", "2200"])
        assert "both" in capsys.readouterr().out

    def test_bad_port_in_host_exits(self, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            self._gather(monkeypatch, ["pi@10.0.0.5:ssh", "mypi"])
        assert "Invalid port" in capsys.readouterr().out

    def test_alias_default_ignores_port(self, monkeypatch):
        (_, _, alias, _, _), _ = self._gather(
            monkeypatch, ["pi@rpi-5.local:2222"])
        assert alias == "rpi-5"

    @pytest.mark.parametrize("target, fallback", [
        ("pi@testpi", ("testpi", 22)),
        ("pi@testpi:2222", ("testpi", 2222)),
    ])
    def test_substituted_host_falls_back_to_typed(self, monkeypatch,
                                                  target, fallback):
        (host, _, _, port, _), seen = self._gather(
            monkeypatch, [target, "testpi"], resolved="raspberrypi")
        assert seen["fallback"] == fallback
        assert (host, port) == fallback

    def test_no_fallback_when_names_match(self, monkeypatch):
        _, seen = self._gather(monkeypatch, ["pi@rpi-5", "mypi"],
                               resolved="RPI-5")
        assert seen["fallback"] is None

    def test_explicit_alias_wins(self, monkeypatch):
        (_, _, alias, _, _), _ = self._gather(
            monkeypatch, ["pi@rpi-5.local", "mypi"])
        assert alias == "mypi"

    def test_ip_without_alias_exits(self, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            self._gather(monkeypatch, ["pi@10.0.0.5"])
        assert "No alias provided" in capsys.readouterr().out


class TestBuildBlock:
    def test_omits_port_line_on_default(self):
        block = ssh_keyup.SSHConfig._build_block(
            "mypi", "10.0.0.5", "pi", "id_ed25519_mypi")
        assert "Port" not in block
        assert "    HostName 10.0.0.5\n" in block

    def test_writes_port_line_when_custom(self):
        block = ssh_keyup.SSHConfig._build_block(
            "mypi", "10.0.0.5", "pi", "id_ed25519_mypi", 2222)
        assert "    Port 2222\n" in block

    @pytest.mark.parametrize("key_name", [
        "id_ed25519_mypi", "id_rsa_mypi",
    ])
    def test_identity_file_follows_key_name(self, key_name):
        block = ssh_keyup.SSHConfig._build_block(
            "mypi", "10.0.0.5", "pi", key_name)
        assert f"    IdentityFile ~/.ssh/{key_name}\n" in block


class TestGenerateKey:
    def test_ed25519_takes_no_size(self):
        runner = FakeRunner()
        ssh_keyup.generate_key(runner, Path("id_ed25519_mypi"), "ed25519")
        cmd = runner.cmds[0]
        assert cmd[cmd.index("-t") + 1] == "ed25519"
        assert "-b" not in cmd

    def test_rsa_pins_size(self):
        runner = FakeRunner()
        ssh_keyup.generate_key(runner, Path("id_rsa_mypi"), "rsa")
        cmd = runner.cmds[0]
        assert cmd[cmd.index("-t") + 1] == "rsa"
        assert cmd[cmd.index("-b") + 1] == str(ssh_keyup.RSA_BITS)

    def test_git_bash_carries_the_same_options(self):
        runner = FakeRunner(mode="gitbash")
        ssh_keyup.generate_key(runner, Path("id_rsa_mypi"), "rsa")
        assert f"-t rsa -b {ssh_keyup.RSA_BITS}" in runner.cmds[0]

    def test_exits_when_keygen_fails(self):
        with pytest.raises(SystemExit):
            ssh_keyup.generate_key(
                FakeRunner(rc=1), Path("id_rsa_mypi"), "rsa")


class TestPruneOtherType:
    @staticmethod
    def seed(ssh_dir, name):
        key = ssh_dir / name
        key.write_text("private")
        (ssh_dir / f"{name}.pub").write_text("public")
        return key

    def test_offers_and_deletes_the_stale_pair(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: True)
        stale = self.seed(tmp_path, "id_ed25519_mypi")

        ssh_keyup.prune_other_type(tmp_path, "mypi", "rsa")

        assert not stale.exists()
        assert not (tmp_path / "id_ed25519_mypi.pub").exists()

    def test_keeps_the_pair_when_declined(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: False)
        stale = self.seed(tmp_path, "id_ed25519_mypi")

        ssh_keyup.prune_other_type(tmp_path, "mypi", "rsa")

        assert stale.exists()

    def test_never_touches_the_key_in_use(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: True)
        current = self.seed(tmp_path, "id_rsa_mypi")

        ssh_keyup.prune_other_type(tmp_path, "mypi", "rsa")

        assert current.exists()

    def test_leaves_other_aliases_alone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: True)
        other = self.seed(tmp_path, "id_ed25519_otherpi")

        ssh_keyup.prune_other_type(tmp_path, "mypi", "rsa")

        assert other.exists()

    def test_asks_nothing_when_there_is_no_stale_key(self, tmp_path,
                                                     monkeypatch):
        def refuse(msg):
            raise AssertionError("should not prompt")

        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", refuse)
        ssh_keyup.prune_other_type(tmp_path, "mypi", "rsa")


class TestSSHCommand:
    def test_pins_default_port(self):
        runner = FakeRunner()
        ssh_keyup.Deployer._ssh_cmd(runner, "pi", "h", "cmd", b"key")
        cmd = runner.cmds[0]
        assert cmd[cmd.index("-p") + 1] == "22"

    def test_passes_port_flag_when_custom(self):
        runner = FakeRunner()
        ssh_keyup.Deployer._ssh_cmd(runner, "pi", "h", "cmd", b"key",
                                    port=2222)
        cmd = runner.cmds[0]
        assert cmd[cmd.index("-p") + 1] == "2222"

    @pytest.mark.parametrize("accept_new", [False, True])
    def test_always_verbose(self, accept_new):
        runner = FakeRunner()
        ssh_keyup.Deployer._ssh_cmd(runner, "pi", "h", "cmd", b"key",
                                    accept_new=accept_new)
        assert "-v" in runner.cmds[0]

    def test_feeds_stdin(self):
        runner = FakeRunner()
        ssh_keyup.Deployer._ssh_cmd(runner, "pi", "h", "cmd", b"key")
        assert runner.inputs == [b"key"]

    @pytest.mark.parametrize("host, pinned", [
        ("testpi", "HostName=testpi"),
        ("fe80::1%eth0", "HostName=fe80::1%%eth0"),
    ])
    def test_pins_probed_host(self, host, pinned):
        runner = FakeRunner()
        ssh_keyup.Deployer._ssh_cmd(runner, "pi", host, "cmd", b"key")
        cmd = runner.cmds[0]
        assert cmd[cmd.index(pinned) - 1] == "-o"
        assert f"pi@{host}" in cmd


class TestTargetOS:
    @pytest.mark.parametrize("banner, expected", [
        ("SSH-2.0-OpenSSH_9.2p1 Debian-2", ssh_keyup.TargetOS.LINUX),
        ("SSH-2.0-ROSSSH", ssh_keyup.TargetOS.ROUTEROS),
        ("", ssh_keyup.TargetOS.LINUX),
    ])
    def test_from_banner(self, banner, expected):
        assert ssh_keyup.os_from_banner(banner) is expected

    def test_from_ssh_verbose(self):
        stderr = ("debug1: Remote protocol version 2.0, "
                  "remote software version ROSSSH\n")
        assert ssh_keyup.os_from_ssh_verbose(stderr) is \
            ssh_keyup.TargetOS.ROUTEROS
        assert ssh_keyup.os_from_ssh_verbose("no such line") is \
            ssh_keyup.TargetOS.LINUX

    def test_os_flag_choices(self):
        assert parse(["rpi-5", "--os", "routeros"]).os == "routeros"
        with pytest.raises(SystemExit):
            parse(["rpi-5", "--os", "plan9"])


class FakeSocket:
    def __init__(self, data):
        self.data = data

    def settimeout(self, value):
        pass

    def recv(self, size):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestReadBanner:
    def test_returns_identification_line(self):
        sock = FakeSocket(b"SSH-2.0-ROSSSH\r\n")
        assert ssh_keyup.read_banner(sock) == "SSH-2.0-ROSSSH"

    def test_skips_pre_banner_text(self):
        sock = FakeSocket(b"Welcome\r\nSSH-2.0-OpenSSH_9.2\r\n")
        assert ssh_keyup.read_banner(sock) == "SSH-2.0-OpenSSH_9.2"

    def test_empty_on_timeout(self):
        assert ssh_keyup.read_banner(FakeSocket(socket.timeout())) == ""


# Throwaway key, fingerprint cross-checked with ssh-keygen -lf
VECTOR_KEY = ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJGRmHknhcaBKNcm1wTSutdIQUJ"
              "NztxgJe9W1Vaol8is vector")
VECTOR_FP = "SHA256:Qtfuel0sROISC4b/uk7Epfr9SjZTn3/YBh3CqKy6UcY="


class TestInstallCommand:
    def test_linux_takes_key_on_stdin(self):
        cmd, stdin = ssh_keyup.Deployer._install_command(
            ssh_keyup.TargetOS.LINUX, "pi", "ssh-ed25519 AAAA c")
        assert "authorized_keys" in cmd
        assert "AAAA" not in cmd
        assert stdin == b"ssh-ed25519 AAAA c"

    def test_routeros_takes_key_inline(self):
        cmd, stdin = ssh_keyup.Deployer._install_command(
            ssh_keyup.TargetOS.ROUTEROS, "admin", VECTOR_KEY)
        assert f'/user/ssh-keys/add user="admin" key="{VECTOR_KEY}"' in cmd
        assert stdin == b""

    def test_routeros_add_is_guarded_by_fingerprint_lookup(self):
        cmd, _ = ssh_keyup.Deployer._install_command(
            ssh_keyup.TargetOS.ROUTEROS, "admin", VECTOR_KEY)
        assert cmd.startswith(":local n 0; :do {")
        assert f'user="admin" and fingerprint="{VECTOR_FP}"' in cmd
        # The add must sit outside on-error, or its message gets swallowed
        assert cmd.index("on-error={}") < cmd.index("/user/ssh-keys/add")

    def test_linux_marker_needs_every_step(self):
        cmd, _ = ssh_keyup.Deployer._install_command(
            ssh_keyup.TargetOS.LINUX, "pi", "ssh-ed25519 AAAA c")
        assert cmd.endswith("&& chmod 600 ~/.ssh/authorized_keys "
                            "&& echo ssh-keyup-ok")

    def test_routeros_marker_comes_after_add(self):
        cmd, _ = ssh_keyup.Deployer._install_command(
            ssh_keyup.TargetOS.ROUTEROS, "admin", VECTOR_KEY)
        assert cmd.endswith('; :put "ssh-keyup-ok"')
        assert cmd.index("/user/ssh-keys/add") < cmd.index(":put")


class TestKeyFingerprint:
    def test_matches_ssh_keygen_with_padding(self):
        assert ssh_keyup.key_fingerprint(VECTOR_KEY) == VECTOR_FP

    @pytest.mark.parametrize("line", ["", "ssh-ed25519", "ssh-ed25519 !!! c"])
    def test_empty_for_malformed_line(self, line):
        assert ssh_keyup.key_fingerprint(line) == ""


# Verbose stderr after a successful login and a failing remote command
AUTH_OK_STDERR = """\
OpenSSH_9.5p2, LibreSSL 3.8.2
debug1: Connecting to router [192.168.88.1] port 22.
Authenticated to router ([192.168.88.1]:22) using "password".
debug1: Sending command: mkdir -p ~/.ssh && chmod 700 ~/.ssh
bad command name mkdir (line 1 column 1)
Transferred: sent 2204, received 2776 bytes, in 0.1 seconds
Bytes per second: sent 31485.7, received 39657.2
debug1: Exit status 1
"""

# Same, with the banner echo that identifies RouterOS
ROUTEROS_STDERR = AUTH_OK_STDERR.replace(
    "Authenticated to",
    "debug1: Remote protocol version 2.0, remote software version ROSSSH\n"
    "Authenticated to")

# Verbose output of a clean run, nothing from the remote command
CLEAN_STDERR = """\
OpenSSH_9.5p2, LibreSSL 3.8.2
debug1: Connecting to router [10.0.0.1] port 22.
debug1: Remote protocol version 2.0, remote software version ROSSSH
Warning: Permanently added '10.0.0.1' (ED25519) to the list of known hosts.
Authenticated to router ([10.0.0.1]:22) using "password".

Transferred: sent 2204, received 2776 bytes, in 0.1 seconds
Bytes per second: sent 31485.7, received 39657.2
debug1: Exit status 0
"""

# RouterOS rejecting a key, exit status stays 0
ROUTEROS_REJECT = CLEAN_STDERR.replace(
    "Transferred:",
    "failure: unable to load key file (wrong format or bad passphrase)! "
    "(/user/ssh-keys/add; line 1)\nTransferred:")

# Clean run through OpenSSH 10.1+, RouterOS has no post-quantum key exchange
PQ_WARNING_STDERR = CLEAN_STDERR.replace(
    "Authenticated to",
    "** WARNING: connection is not using a post-quantum key exchange "
    "algorithm.\n"
    '** This session may be vulnerable to "store now, decrypt later" '
    "attacks.\n"
    "** The server may need to be upgraded. See https://openssh.com/pq.html\n"
    "Authenticated to")

# Clean run after two mistyped passwords
RETRY_STDERR = CLEAN_STDERR.replace(
    "Authenticated to",
    "Permission denied, please try again.\n"
    "Permission denied, please try again.\n"
    "Authenticated to")


def confirmed(output):
    """Session output followed by device's install marker on stdout."""
    return output + ssh_keyup.DEPLOY_MARKER + "\n"


# RouterOS offering no password login, its user already has a key
KEYED_USER_STDERR = """\
OpenSSH_for_Windows_9.5p2, LibreSSL 3.8.2
debug1: Remote protocol version 2.0, remote software version ROSSSH
debug1: Authentications that can continue: publickey
debug1: No more authentication methods to try.
admin@192.168.88.1: Permission denied (publickey).
"""

ED25519 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 pi@rpi"
RSA = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQAB pi@rpi"

# Verbose stderr after a rejected password
AUTH_FAIL_STDERR = """\
OpenSSH_9.5p2, LibreSSL 3.8.2
debug1: Connecting to pi [10.0.0.5] port 22.
no such identity: /home/me/.ssh/id_rsa: No such file or directory
Permission denied, please try again.
Permission denied, please try again.
pi@10.0.0.5: Permission denied (publickey,password).
"""


class TestFindGitBash:
    @staticmethod
    def install(tmp_path, monkeypatch, *names):
        root = tmp_path / "Git"
        for name in ("cmd/git.exe", *names):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        monkeypatch.setattr(ssh_keyup, "which",
                            lambda _: str(root / "cmd" / "git.exe"))
        return root

    def test_picks_console_bash_over_windowed_launcher(
            self, tmp_path, monkeypatch):
        root = self.install(tmp_path, monkeypatch,
                            "git-bash.exe", "bin/bash.exe")
        assert ssh_keyup.Runner._find_git_bash() == str(
            root / "bin" / "bash.exe")

    def test_windowed_launcher_alone_is_no_fallback(
            self, tmp_path, monkeypatch):
        self.install(tmp_path, monkeypatch, "git-bash.exe")
        assert ssh_keyup.Runner._find_git_bash() is None

    def test_none_without_git(self, monkeypatch):
        monkeypatch.setattr(ssh_keyup, "which", lambda _: None)
        assert ssh_keyup.Runner._find_git_bash() is None


class TestRunCapture:
    def test_merges_stderr_then_stdout(self):
        runner = ssh_keyup.Runner()
        runner.mode = "native"
        script = ("import sys; print('from stdout'); "
                  "print('from stderr', file=sys.stderr); sys.exit(3)")
        rc, text = runner.run_capture([sys.executable, "-c", script])
        assert rc == 3
        assert text.splitlines() == ["from stderr", "from stdout"]

    def test_feeds_input(self):
        runner = ssh_keyup.Runner()
        runner.mode = "native"
        script = "import sys; print(sys.stdin.read().upper())"
        _, text = runner.run_capture([sys.executable, "-c", script],
                                     input=b"key")
        assert text.strip() == "KEY"

    def test_ctrl_c_hands_over_output_so_far(self, monkeypatch):
        def interrupted(args, **kwargs):
            kwargs["stdout"].write(b"remote said\n")
            kwargs["stderr"].write(b"Authenticated to router\n")
            raise KeyboardInterrupt

        monkeypatch.setattr(ssh_keyup.subprocess, "run", interrupted)
        runner = ssh_keyup.Runner()
        runner.mode = "native"
        with pytest.raises(ssh_keyup.Interrupted) as info:
            runner.run_capture(["ssh"])
        assert info.value.output.splitlines() == [
            "Authenticated to router", "remote said"]


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
        ssh_keyup.Deployer._report_failure(AUTH_OK_STDERR,
                                           ssh_keyup.TargetOS.LINUX, ED25519)
        out = capsys.readouterr().out
        assert "install command failed" in out
        assert "bad command name mkdir" in out
        assert "credentials" not in out
        assert "7.12" not in out

    def test_login_failure_names_credentials(self, capsys):
        ssh_keyup.Deployer._report_failure(AUTH_FAIL_STDERR,
                                           ssh_keyup.TargetOS.LINUX, ED25519)
        out = capsys.readouterr().out
        assert "Check host and credentials" in out
        assert "Permission denied (publickey,password)" in out
        assert "OpenSSH_" not in out

    def test_error_lines_drop_blank_lines(self):
        assert ssh_keyup.Deployer._error_lines(CLEAN_STDERR) == []

    def test_error_lines_drop_post_quantum_warning(self):
        assert ssh_keyup.Deployer._error_lines(PQ_WARNING_STDERR) == []

    def test_routeros_rejected_key_hints_version(self, capsys):
        ssh_keyup.Deployer._report_failure(ROUTEROS_REJECT,
                                           ssh_keyup.TargetOS.ROUTEROS,
                                           ED25519)
        out = capsys.readouterr().out
        assert "install command failed" in out
        assert "unable to load key file" in out
        assert "7.12" in out

    def test_rejected_rsa_key_skips_the_version_hint(self, capsys):
        ssh_keyup.Deployer._report_failure(ROUTEROS_REJECT,
                                           ssh_keyup.TargetOS.ROUTEROS, RSA)
        out = capsys.readouterr().out
        assert "unable to load key file" in out
        assert "7.12" not in out

    def test_routeros_permission_error_hints_policy(self, capsys):
        stderr = CLEAN_STDERR.replace(
            "Transferred:",
            "not enough permissions (9) (/user/ssh-keys/add; line 1)\n"
            "Transferred:")
        ssh_keyup.Deployer._report_failure(stderr,
                                           ssh_keyup.TargetOS.ROUTEROS,
                                           ED25519)
        out = capsys.readouterr().out
        assert "policy permission" in out
        assert "7.12" not in out

    @pytest.mark.parametrize("stderr", [AUTH_FAIL_STDERR, ROUTEROS_STDERR])
    def test_routeros_other_failures_have_no_hint(self, capsys, stderr):
        ssh_keyup.Deployer._report_failure(stderr,
                                           ssh_keyup.TargetOS.ROUTEROS,
                                           ED25519)
        out = capsys.readouterr().out
        assert "7.12" not in out
        assert "policy" not in out

    def test_routeros_keyed_user_is_named(self, capsys):
        ssh_keyup.Deployer._report_failure(KEYED_USER_STDERR,
                                           ssh_keyup.TargetOS.ROUTEROS,
                                           ED25519)
        out = capsys.readouterr().out
        assert "'admin' already has SSH key" in out
        assert "refuses password login" in out
        assert "SSH Keys" in out
        assert "password-authentication" not in out
        assert "credentials" not in out

    def test_keyed_user_found_when_banner_missed(self, capsys):
        ssh_keyup.Deployer._report_failure(KEYED_USER_STDERR,
                                           ssh_keyup.TargetOS.LINUX, ED25519)
        assert "refuses password login" in capsys.readouterr().out

    def test_routeros_wrong_password_is_not_a_keyed_user(self, capsys):
        stderr = KEYED_USER_STDERR.replace("(publickey)",
                                           "(publickey,password)")
        ssh_keyup.Deployer._report_failure(stderr,
                                           ssh_keyup.TargetOS.ROUTEROS,
                                           ED25519)
        out = capsys.readouterr().out
        assert "Check host and credentials" in out
        assert "refuses password login" not in out

    def test_linux_without_password_login_stays_generic(self, capsys):
        stderr = KEYED_USER_STDERR.replace("ROSSSH", "OpenSSH_9.6")
        ssh_keyup.Deployer._report_failure(stderr,
                                           ssh_keyup.TargetOS.LINUX, ED25519)
        out = capsys.readouterr().out
        assert "Check host and credentials" in out
        assert "Permission denied (publickey)" in out

    def test_linux_gets_no_routeros_hints(self, capsys):
        ssh_keyup.Deployer._report_failure(ROUTEROS_REJECT,
                                           ssh_keyup.TargetOS.LINUX, ED25519)
        assert "7.12" not in capsys.readouterr().out

    def test_quiet_session_says_device_did_not_confirm(self, capsys):
        ssh_keyup.Deployer._report_failure(RETRY_STDERR,
                                           ssh_keyup.TargetOS.ROUTEROS,
                                           ED25519)
        out = capsys.readouterr().out
        assert "device did not confirm key install" in out
        assert "install command failed" not in out
        assert "Permission denied" not in out


class TestConfirmed:
    @pytest.mark.parametrize("output", [
        confirmed(CLEAN_STDERR),
        confirmed(RETRY_STDERR),
        confirmed(PQ_WARNING_STDERR),
        CLEAN_STDERR + "ssh-keyup-ok\r\n",
    ])
    def test_marker_line_confirms(self, output):
        assert ssh_keyup.Deployer._confirmed(output)

    @pytest.mark.parametrize("output", [
        CLEAN_STDERR, ROUTEROS_REJECT, AUTH_OK_STDERR, AUTH_FAIL_STDERR,
    ])
    def test_no_marker_no_confirmation(self, output):
        assert not ssh_keyup.Deployer._confirmed(output)

    def test_command_echo_is_not_the_marker(self):
        echo = ("debug1: Sending command: mkdir -p ~/.ssh "
                "&& echo ssh-keyup-ok\n")
        assert not ssh_keyup.Deployer._confirmed(CLEAN_STDERR + echo)


class TestDeploy:
    LINUX = ssh_keyup.TargetOS.LINUX
    ROUTEROS = ssh_keyup.TargetOS.ROUTEROS

    @staticmethod
    def _pub(tmp_path):
        pub = tmp_path / "id_ed25519_r.pub"
        pub.write_text("ssh-ed25519 AAAA test\n")
        return pub

    def test_remote_failure_reported_after_login(self, tmp_path, capsys):
        runner = FakeRunner(rc=1, out=AUTH_OK_STDERR)
        assert ssh_keyup.Deployer.deploy(
            runner, "admin", "router", self._pub(tmp_path)) == (None, True)
        out = capsys.readouterr().out
        assert "install command failed" in out
        assert "credentials" not in out
        assert len(runner.cmds) == 1

    def test_returns_target_os_on_success(self, tmp_path):
        runner = FakeRunner(rc=0, out=confirmed(CLEAN_STDERR))
        deployed = ssh_keyup.Deployer.deploy(
            runner, "admin", "router", self._pub(tmp_path),
            target_os=self.ROUTEROS)
        assert deployed == (self.ROUTEROS, True)
        assert "/user/ssh-keys/add" in runner.cmds[0][-1]
        assert runner.inputs == [b""]

    @pytest.mark.parametrize("output", [RETRY_STDERR, PQ_WARNING_STDERR])
    def test_ssh_chatter_does_not_fail_a_confirmed_install(self, tmp_path,
                                                           output):
        runner = FakeRunner(rc=0, out=confirmed(output))
        deployed = ssh_keyup.Deployer.deploy(
            runner, "admin", "router", self._pub(tmp_path),
            target_os=self.ROUTEROS)
        assert deployed == (self.ROUTEROS, True)

    def test_marker_decides_over_exit_status(self, tmp_path):
        runner = FakeRunner(rc=1, out=confirmed(AUTH_OK_STDERR))
        assert ssh_keyup.Deployer.deploy(
            runner, "pi", "rpi", self._pub(tmp_path)) == (self.LINUX, True)
        assert len(runner.cmds) == 1

    def test_clean_session_without_marker_fails(self, tmp_path, capsys):
        runner = FakeRunner(rc=0, out=RETRY_STDERR)
        assert ssh_keyup.Deployer.deploy(
            runner, "admin", "router", self._pub(tmp_path),
            target_os=self.ROUTEROS) == (None, True)
        assert "did not confirm" in capsys.readouterr().out

    def test_routeros_rejection_with_exit_zero_fails(self, tmp_path, capsys):
        runner = FakeRunner(rc=0, out=ROUTEROS_REJECT)
        assert ssh_keyup.Deployer.deploy(
            runner, "admin", "router", self._pub(tmp_path),
            target_os=self.ROUTEROS) == (None, True)
        out = capsys.readouterr().out
        assert "install command failed" in out
        assert "7.12" in out

    def test_falls_back_to_routeros_after_linux_command_fails(
            self, tmp_path, capsys):
        runner = FakeRunner(results=[(1, ROUTEROS_STDERR),
                                     (0, confirmed(CLEAN_STDERR))])
        deployed = ssh_keyup.Deployer.deploy(
            runner, "admin", "router", self._pub(tmp_path))
        assert deployed == (self.ROUTEROS, True)
        assert "RouterOS detected" in capsys.readouterr().out
        assert len(runner.cmds) == 2
        assert "authorized_keys" in runner.cmds[0][-1]
        assert "/user/ssh-keys/add" in runner.cmds[1][-1]

    def test_no_fallback_without_login(self, tmp_path):
        runner = FakeRunner(rc=1, out=AUTH_FAIL_STDERR)
        assert ssh_keyup.Deployer.deploy(
            runner, "admin", "router", self._pub(tmp_path)) == (None, False)
        assert len(runner.cmds) == 1

    def test_no_fallback_when_os_was_given(self, tmp_path):
        runner = FakeRunner(rc=1, out=ROUTEROS_STDERR)
        assert ssh_keyup.Deployer.deploy(
            runner, "admin", "router", self._pub(tmp_path),
            target_os=self.ROUTEROS) == (None, True)
        assert len(runner.cmds) == 1

    def test_offers_no_ssh_agent_keys(self, tmp_path):
        runner = FakeRunner(rc=0, out=confirmed(CLEAN_STDERR))
        ssh_keyup.Deployer.deploy(runner, "admin", "router",
                                  self._pub(tmp_path))
        assert "IdentitiesOnly=yes" in runner.cmds[0]

    def test_offers_login_keys_in_every_attempt(self, tmp_path):
        runner = FakeRunner(results=[(1, ROUTEROS_STDERR),
                                     (0, confirmed(CLEAN_STDERR))])
        keys = (tmp_path / "id_ed25519_r", tmp_path / "id_rsa_r")
        ssh_keyup.Deployer.deploy(runner, "admin", "router",
                                  self._pub(tmp_path), login_keys=keys)
        for cmd in runner.cmds:
            offered = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-i"]
            assert offered == [key.as_posix() for key in keys]

    def test_offers_default_keys_after_login_keys(self, tmp_path):
        runner = FakeRunner(rc=0, out=confirmed(CLEAN_STDERR))
        key = tmp_path / "id_ed25519_r"
        ssh_keyup.Deployer.deploy(runner, "admin", "router",
                                  self._pub(tmp_path), login_keys=(key,))
        cmd = runner.cmds[0]
        default = cmd.index("IdentityFile=~/.ssh/id_ed25519")
        assert cmd.index(key.as_posix()) < default
        assert "IdentityFile=~/.ssh/id_rsa" in cmd

    def test_offers_no_key_by_default(self, tmp_path):
        runner = FakeRunner(rc=0, out=confirmed(CLEAN_STDERR))
        ssh_keyup.Deployer.deploy(runner, "admin", "router",
                                  self._pub(tmp_path))
        assert "-i" not in runner.cmds[0]

    @pytest.mark.parametrize("output, logged_in", [
        (AUTH_FAIL_STDERR, False),
        (AUTH_OK_STDERR, True),
    ])
    def test_ctrl_c_in_session_says_whether_it_logged_in(
            self, tmp_path, output, logged_in):
        runner = FakeRunner(results=[ssh_keyup.Interrupted(output)])
        with pytest.raises(ssh_keyup.Interrupted) as info:
            ssh_keyup.Deployer.deploy(runner, "pi", "rpi", self._pub(tmp_path))
        assert info.value.logged_in == logged_in

    def test_ctrl_c_in_retry_remembers_earlier_login(self, tmp_path):
        runner = FakeRunner(results=[(1, ROUTEROS_STDERR),
                                     ssh_keyup.Interrupted(AUTH_FAIL_STDERR)])
        with pytest.raises(ssh_keyup.Interrupted) as info:
            ssh_keyup.Deployer.deploy(runner, "pi", "rpi", self._pub(tmp_path))
        assert info.value.logged_in


CTRL_C_EXIT = ssh_keyup.SIGNAL_EXIT_BASE + signal.SIGINT


class Setup:
    """Run main() on a throwaway home with deploy outcome canned."""

    ROUTEROS = ssh_keyup.TargetOS.ROUTEROS

    def __init__(self, home, monkeypatch):
        self.ssh_dir = home / ".ssh"
        self.key = self.ssh_dir / "id_ed25519_gw"
        self.pub = self.ssh_dir / "id_ed25519_gw.pub"
        self.config = self.ssh_dir / "config"
        self.monkeypatch = monkeypatch
        # Path.home() reads one or the other, depending on platform
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.setattr(sys, "argv", ["ssh-keyup", "admin@router", "gw"])
        monkeypatch.setattr(ssh_keyup, "Runner", FakeRunner)
        monkeypatch.setattr(
            ssh_keyup, "gather_input",
            lambda args, runner: ("router", "admin", "gw", 22,
                                  self.ROUTEROS))
        monkeypatch.setattr(ssh_keyup, "generate_key", self.seed)
        self.deploys = []

    def seed(self, runner=None, key_path=None, key_type=None, text="new"):
        key = key_path or self.key
        self.ssh_dir.mkdir(exist_ok=True)
        key.write_text(f"{text} private")
        key.with_name(f"{key.name}.pub").write_text(
            f"ssh-ed25519 AAAA {text}\n")

    def leftovers(self):
        return sorted(p.name for p in self.ssh_dir.glob("*.new*"))

    def run(self, outcome):
        """Run main(), return its exit code."""
        def deploy(*args):
            self.deploys.append(args)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        self.monkeypatch.setattr(ssh_keyup.Deployer, "deploy", deploy)
        try:
            ssh_keyup.main()
        except SystemExit as ex:
            return ex.code
        return 0


@pytest.fixture
def setup(tmp_path, monkeypatch):
    return Setup(tmp_path, monkeypatch)


class TestMainKeys:
    def test_success_writes_config_and_keeps_pair(self, setup):
        assert setup.run((setup.ROUTEROS, True)) == 0
        assert setup.key.exists()
        assert "IdentityFile ~/.ssh/id_ed25519_gw" in setup.config.read_text()

    def test_discards_new_pair_when_nothing_logged_in(self, setup):
        assert setup.run((None, False)) == 1
        assert not setup.key.exists()
        assert not setup.pub.exists()
        assert not setup.config.exists()

    def test_skips_cleanup_once_logged_in(self, setup, capsys):
        assert setup.run((None, True)) == 1
        assert setup.key.exists()
        assert setup.pub.exists()
        assert not setup.config.exists()
        assert "Cleaning up" not in capsys.readouterr().out

    def test_never_discards_a_pair_it_did_not_generate(self, setup,
                                                       monkeypatch):
        setup.seed(text="old")
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: False)
        assert setup.run((None, False)) == 1
        assert setup.key.read_text() == "old private"

    def test_existing_key_is_offered_for_login(self, setup, monkeypatch):
        setup.seed(text="old")
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: False)
        assert setup.run((setup.ROUTEROS, True)) == 0
        assert setup.deploys[0][-1] == (setup.key,)

    def test_new_key_is_not_offered_for_login(self, setup):
        assert setup.run((setup.ROUTEROS, True)) == 0
        assert setup.deploys[0][-1] == ()

    def test_other_type_key_is_offered_for_login(self, setup):
        rsa = setup.ssh_dir / "id_rsa_gw"
        setup.seed(key_path=rsa, text="rsa")
        assert setup.run((setup.ROUTEROS, True)) == 0
        assert setup.deploys[0][-1] == (rsa,)

    def test_current_type_is_offered_first(self, setup, monkeypatch):
        rsa = setup.ssh_dir / "id_rsa_gw"
        setup.seed(key_path=rsa, text="rsa")
        setup.seed(text="old")
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: False)
        assert setup.run((setup.ROUTEROS, True)) == 0
        assert setup.deploys[0][-1] == (setup.key, rsa)

    def test_lone_public_key_is_regenerated_without_prompt(self, setup,
                                                           monkeypatch):
        setup.seed(text="old")
        setup.key.unlink()

        def refuse(msg):
            raise AssertionError("should not prompt")

        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", refuse)
        assert setup.run((setup.ROUTEROS, True)) == 0
        assert setup.key.read_text() == "new private"
        assert setup.deploys[0][-1] == ()
        assert "IdentityFile ~/.ssh/id_ed25519_gw" in setup.config.read_text()

    def test_failed_overwrite_keeps_config_entry(self, setup, monkeypatch):
        assert setup.run((setup.ROUTEROS, True)) == 0
        before = setup.config.read_text()
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: True)
        assert setup.run((None, False)) == 1
        assert setup.config.read_text() == before

    @pytest.mark.parametrize("interrupt", [
        KeyboardInterrupt(), ssh_keyup.Interrupted(logged_in=False),
    ])
    def test_ctrl_c_before_login_discards_new_pair(self, setup, capsys,
                                                    interrupt):
        assert setup.run(interrupt) == CTRL_C_EXIT
        assert not setup.key.exists()
        assert not setup.pub.exists()
        assert "Cancelled" in capsys.readouterr().out

    def test_ctrl_c_after_login_skips_cleanup(self, setup, capsys):
        assert setup.run(ssh_keyup.Interrupted(logged_in=True)) == CTRL_C_EXIT
        assert setup.key.exists()
        assert setup.pub.exists()
        out = capsys.readouterr().out
        assert "Cleaning up" not in out
        assert "Cancelled" in out


class TestMainRegenerate:
    @pytest.fixture(autouse=True)
    def existing(self, setup, monkeypatch):
        setup.seed(text="old")
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: True)

    def test_replaces_existing_pair_once_confirmed(self, setup):
        assert setup.run((setup.ROUTEROS, True)) == 0
        assert setup.deploys[0][3].name == "id_ed25519_gw.new.pub"
        assert setup.deploys[0][-1] == (setup.key,)
        assert setup.key.read_text() == "new private"
        assert setup.pub.read_text() == "ssh-ed25519 AAAA new\n"
        assert setup.leftovers() == []

    def test_failure_leaves_existing_pair_untouched(self, setup):
        assert setup.run((None, False)) == 1
        assert setup.key.read_text() == "old private"
        assert setup.leftovers() == []

    def test_unconfirmed_login_keeps_both_pairs(self, setup):
        assert setup.run((None, True)) == 1
        assert setup.key.read_text() == "old private"
        assert setup.leftovers() == ["id_ed25519_gw.new",
                                     "id_ed25519_gw.new.pub"]

    def test_ctrl_c_before_login_discards_staged_pair(self, setup):
        assert setup.run(ssh_keyup.Interrupted(logged_in=False)) == CTRL_C_EXIT
        assert setup.key.read_text() == "old private"
        assert setup.leftovers() == []

    def test_ctrl_c_after_login_keeps_both_pairs(self, setup):
        assert setup.run(ssh_keyup.Interrupted(logged_in=True)) == CTRL_C_EXIT
        assert setup.key.read_text() == "old private"
        assert setup.leftovers() == ["id_ed25519_gw.new",
                                     "id_ed25519_gw.new.pub"]

    def test_stale_new_pair_is_regenerated(self, setup):
        setup.seed(key_path=setup.ssh_dir / "id_ed25519_gw.new",
                   text="stale")
        assert setup.run((setup.ROUTEROS, True)) == 0
        assert setup.key.read_text() == "new private"
        assert setup.leftovers() == []


class TestMainLonePrivateKey:
    @pytest.fixture(autouse=True)
    def lone(self, setup, monkeypatch):
        setup.seed(text="old")
        setup.pub.unlink()

        def refuse(msg):
            raise AssertionError("should not prompt")

        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", refuse)

    def test_replaced_once_confirmed(self, setup):
        assert setup.run((setup.ROUTEROS, True)) == 0
        assert setup.deploys[0][3].name == "id_ed25519_gw.new.pub"
        assert setup.deploys[0][-1] == (setup.key,)
        assert setup.key.read_text() == "new private"
        assert setup.pub.read_text() == "ssh-ed25519 AAAA new\n"
        assert setup.leftovers() == []

    def test_survives_failed_deploy(self, setup):
        assert setup.run((None, False)) == 1
        assert setup.key.read_text() == "old private"
        assert setup.leftovers() == []


class TestAtomicWrite:
    @pytest.mark.parametrize("text,expected", [
        ("Host a\n\n\n", "Host a\n"),
        ("Host a", "Host a\n"),
        ("", ""),
    ])
    def test_normalizes_trailing_newline(self, tmp_path, text, expected):
        cfg = tmp_path / "config"
        ssh_keyup.SSHConfig.write(cfg, text)
        assert cfg.read_text() == expected

    def test_leaves_no_temp_file_behind(self, tmp_path):
        cfg = tmp_path / "config"
        ssh_keyup.SSHConfig.write(cfg, "Host a\n")
        assert [p.name for p in tmp_path.iterdir()] == ["config"]


class TestProbePort:
    def test_banner_when_connectable(self, monkeypatch):
        monkeypatch.setattr(ssh_keyup.socket, "create_connection",
                            lambda *a, **k: FakeSocket(b"SSH-2.0-ROSSSH\r\n"))
        assert ssh_keyup.probe_port("rpi", 22) == ("SSH-2.0-ROSSSH", None)

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
        banner, failure = ssh_keyup.probe_port("rpi", 22)
        assert banner == ""
        assert failure is not None
        assert fragment in failure[0]


class TestCheckReachable:
    BANNER = "SSH-2.0-OpenSSH_9.2"

    @staticmethod
    def _probe(monkeypatch, reachable):
        probed = []

        def fake_probe(host, port):
            probed.append((host, port))
            if host in reachable:
                return TestCheckReachable.BANNER, None
            return "", (f"Could not resolve hostname '{host}'", "detail")

        monkeypatch.setattr(ssh_keyup, "probe_port", fake_probe)
        return probed

    def test_reports_ok_and_returns_banner(self, monkeypatch, capsys):
        self._probe(monkeypatch, {"rpi"})
        assert ssh_keyup.check_reachable("rpi") == ("rpi", 22, self.BANNER)
        assert "ok" in capsys.readouterr().out

    def test_continues_when_user_accepts(self, monkeypatch):
        self._probe(monkeypatch, set())
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: True)
        assert ssh_keyup.check_reachable("rpi") == ("rpi", 22, "")

    def test_no_fallback_probe_without_fallback(self, monkeypatch):
        probed = self._probe(monkeypatch, set())
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: True)
        ssh_keyup.check_reachable("rpi")
        assert probed == [("rpi", 22)]

    def test_falls_back_to_typed_name(self, monkeypatch, capsys):
        probed = self._probe(monkeypatch, {"testpi"})
        result = ssh_keyup.check_reachable(
            "raspberrypi", 22, fallback=("testpi", 22))
        assert result == ("testpi", 22, self.BANNER)
        assert probed == [("raspberrypi", 22), ("testpi", 22)]
        out = capsys.readouterr().out
        assert "Trying 'testpi' as hostname" in out
        assert "Warning" not in out

    def test_fallback_shows_custom_port(self, monkeypatch, capsys):
        self._probe(monkeypatch, {"testpi"})
        ssh_keyup.check_reachable("raspberrypi", 22,
                                  fallback=("testpi", 2222))
        assert "Trying 'testpi:2222' as hostname" in capsys.readouterr().out

    def test_both_fail_warns_about_config_target(self, monkeypatch, capsys):
        self._probe(monkeypatch, set())
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: True)
        result = ssh_keyup.check_reachable(
            "raspberrypi", 22, fallback=("testpi", 22))
        assert result == ("raspberrypi", 22, "")
        out = capsys.readouterr().out
        assert "hostname 'raspberrypi'" in out
        assert "hostname 'testpi'" not in out

    def test_exits_when_user_declines(self, monkeypatch):
        monkeypatch.setattr(ssh_keyup, "probe_port",
                            lambda h, p: ("", ("no route", "detail")))
        monkeypatch.setattr(ssh_keyup.cli, "ask_yn", lambda msg: False)
        with pytest.raises(SystemExit) as exc:
            ssh_keyup.check_reachable("rpi")
        assert exc.value.code == 0
