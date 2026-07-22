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
