# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixtures driving ssh-keyup against live targets in compose.yml."""

from __future__ import annotations

import dataclasses
import os
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOST = os.environ.get("INTEGRATION_HOST", "127.0.0.1")
BOOT_TIMEOUT = 180.0
REFUSE_GRACE = 10.0
RUN_TIMEOUT = 120
SCAN_TRIES = 5
ASKPASS = "#!/bin/sh\nprintf '%s\\n' {}\n"
STRICT = os.environ.get("INTEGRATION_STRICT") == "1"


@dataclass(frozen=True)
class Target:
    """A device family, its login and commands it answers to."""

    name: str
    port: int
    user: str
    password: str
    noop: str
    clear_keys: str


TARGETS = [
    Target("linux", 2200, "keyup", "keyup-integration",
           noop="true", clear_keys="rm -f ~/.ssh/authorized_keys"),
    Target("ros7", 2207, "admin", "",
           noop=":nothing", clear_keys="/user/ssh-keys/remove [find]"),
]

_BANNERS: dict = {}


def _await_banner(port: int) -> str:
    """Wait for an SSH banner, empty if none arrives. CHR boots slowly.

    A refused port means the target was never published, so that gets a
    short grace instead of the whole boot budget.
    """
    deadline = time.monotonic() + BOOT_TIMEOUT
    refused_until = time.monotonic() + REFUSE_GRACE
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, port), 5) as sock:
                sock.settimeout(5)
                line = sock.recv(256).decode(errors="replace").strip()
            if line.startswith("SSH-"):
                return line
        except ConnectionRefusedError:
            if time.monotonic() > refused_until:
                return ""
        except OSError:
            pass
        time.sleep(3)
    return ""


def _banner(target: Target) -> str:
    """Probe once per session, every test would otherwise pay the wait."""
    if target.name not in _BANNERS:
        _BANNERS[target.name] = _await_banner(target.port)
    return _BANNERS[target.name]


def _write_askpass(path: Path, password: str) -> Path:
    path.write_text(ASKPASS.format(shlex.quote(password)))
    path.chmod(0o700)
    return path


@dataclass(frozen=True)
class Session:
    """A target plus the throwaway client environment driving it."""

    target: Target
    home: Path
    askpass: Path

    @property
    def address(self) -> str:
        return f"{self.target.user}@{HOST}:{self.target.port}"

    @property
    def config(self) -> Path:
        return self.home / ".ssh" / "config"

    @property
    def env(self) -> dict:
        # ssh runs without a TTY here, so askpass is the only route a
        # password can take. The tool itself never sees one.
        return {
            **os.environ,
            "HOME": str(self.home),
            "SSH_ASKPASS": str(self.askpass),
            "SSH_ASKPASS_REQUIRE": "force",
        }

    def key(self, alias: str) -> Path:
        stem = "id_ed25519_" + alias.replace("-", "_")
        return self.home / ".ssh" / stem

    def identities(self) -> list:
        return sorted(p for p in (self.home / ".ssh").glob("id_*")
                      if p.suffix != ".pub")

    def config_text(self) -> str:
        return self.config.read_text() if self.config.exists() else ""

    def with_password(self, password: str) -> Session:
        askpass = _write_askpass(
            self.askpass.with_name("askpass-alt"), password)
        return dataclasses.replace(self, askpass=askpass)

    def keyup(self, *args: str) -> subprocess.CompletedProcess:
        script = str(REPO / "ssh_keyup.py")
        return self._run([sys.executable, script, *args])

    def remote(self, command: str,
               identity: Path | None = None
               ) -> subprocess.CompletedProcess:
        """Run a command on the target, by key when one is given."""
        auth = (["-o", "PreferredAuthentications=publickey",
                 "-o", "IdentitiesOnly=yes", "-i", str(identity)]
                if identity else
                ["-o", "PreferredAuthentications=password",
                 "-o", "NumberOfPasswordPrompts=1"])
        return self._run([
            "ssh", "-p", str(self.target.port), *auth,
            f"{self.target.user}@{HOST}", command,
        ])

    def via_alias(self, alias: str,
                  command: str) -> subprocess.CompletedProcess:
        """Log in through the alias by key, no password path.

        The config points IdentityFile at ~, which ssh expands from the
        passwd entry rather than HOME, so the key is named outright.
        """
        return self._run([
            "ssh", "-F", str(self.config), "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes", "-i", str(self.key(alias)),
            alias, command,
        ])

    def _run(self, cmd: list) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, env=self.env, check=False,
                              stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=RUN_TIMEOUT)


def _known_host(port: int) -> str:
    return HOST if port == 22 else f"[{HOST}]:{port}"


def _scan_host_key(port: int) -> str:
    """Collect a host key. CHR answers the banner before it answers a scan."""
    for _ in range(SCAN_TRIES):
        scan = subprocess.run(
            ["ssh-keyscan", "-p", str(port), HOST],
            check=False, capture_output=True, text=True, timeout=60)
        if scan.stdout.strip():
            return scan.stdout
        time.sleep(2)
    return ""


def _unavailable(reason: str) -> None:
    """Skip locally, fail in CI, where the target is meant to be running.

    A skipped test still passes, so without this a target that never
    booted reports a green check having asserted nothing.
    """
    if STRICT:
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def trusted() -> set:
    """Trust reachable target host keys, then take the entries back out.

    ssh expands ~ from the passwd entry, so HOME cannot redirect
    known_hosts and the real file has to carry these. Returns trusted
    target names, so one unreachable target does not fail the rest.
    """
    known = Path.home() / ".ssh" / "known_hosts"
    known.parent.mkdir(parents=True, exist_ok=True)
    names, added = set(), []
    for target in TARGETS:
        if not _banner(target):
            continue
        host_key = _scan_host_key(target.port)
        if not host_key:
            continue
        with known.open("a") as fh:
            fh.write(host_key)
        names.add(target.name)
        added.append(_known_host(target.port))
    yield names
    for name in added:
        subprocess.run(["ssh-keygen", "-R", name], check=False,
                       capture_output=True)


@pytest.fixture(params=TARGETS, ids=lambda t: t.name)
def session(request, tmp_path, trusted):
    target = request.param
    if os.name != "posix":
        pytest.skip("password automation needs a POSIX askpass script")
    if not _banner(target):
        _unavailable(f"no SSH banner on {HOST}:{target.port}")
    if target.name not in trusted:
        _unavailable(f"no host key collected from {HOST}:{target.port}")

    ssh_dir = tmp_path / "home" / ".ssh"
    ssh_dir.mkdir(parents=True)
    ssh_dir.chmod(0o700)
    session = Session(target, ssh_dir.parent,
                      _write_askpass(tmp_path / "askpass", target.password))

    yield session

    # RouterOS refuses passwords once a key is in, so try the keys first
    for identity in session.identities():
        if session.remote(target.clear_keys, identity).returncode == 0:
            return
    session.remote(target.clear_keys)
