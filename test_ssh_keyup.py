# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for ssh_keyup argument handling and helpers."""

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
