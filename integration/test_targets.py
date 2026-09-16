# SPDX-License-Identifier: GPL-3.0-or-later
"""Full deploys against live targets, one body per behavior."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_deploy_grants_key_login(session):
    run = session.keyup(session.address, "alpha")
    assert run.returncode == 0, run.stdout + run.stderr
    assert session.key("alpha").exists()
    assert "Host alpha" in session.config_text()

    login = session.via_alias("alpha", session.target.noop)
    assert login.returncode == 0, login.stderr


def test_detects_routeros_from_banner(session):
    run = session.keyup(session.address, "alpha")
    assert run.returncode == 0, run.stdout + run.stderr
    detected = "RouterOS detected" in run.stdout
    assert detected == (session.target.name == "ros7")


def test_second_alias_adds_second_key(session):
    for alias in ("alpha", "beta"):
        run = session.keyup(session.address, alias)
        assert run.returncode == 0, run.stdout + run.stderr

    for alias in ("alpha", "beta"):
        login = session.via_alias(alias, session.target.noop)
        assert login.returncode == 0, login.stderr


def test_bad_password_leaves_nothing_behind(session):
    run = session.with_password("not-the-password").keyup(
        session.address, "gamma")
    assert run.returncode == 1
    assert not session.key("gamma").exists()
    assert not session.key("gamma").with_suffix(".pub").exists()
    assert "gamma" not in session.config_text()


def test_routeros_refuses_password_once_keyed(session):
    if session.target.name != "ros7":
        pytest.skip("RouterOS-only behavior")

    assert session.remote(":nothing").returncode == 0
    assert session.keyup(session.address, "alpha").returncode == 0
    assert session.remote(":nothing").returncode != 0
