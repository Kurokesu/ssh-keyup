#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
#
# ssh-keyup - Passwordless SSH setup for
# Raspberry Pi, NVIDIA Jetson, or any Linux device
#
# Copyright (c) 2026, UAB Kurokesu. All rights reserved.

# Makes modern annotation syntax runtime safe on Python 3.8
from __future__ import annotations

__version__ = "1.2.0"

import argparse
import contextlib
import ipaddress
import os
import re
import shlex
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from shutil import which

if sys.platform == "win32":
    import ctypes
    import msvcrt
else:
    import termios
    import tty

SSH_PORT = 22
CONNECT_TIMEOUT = 3.0


class CLI:
    """Styled terminal output and interaction. All UI in one place."""

    BANNER = r"""
         _           _
 ___ ___| |__       | | _____ _   _ _   _ _ __
/ __/ __| '_ \ _____| |/ / _ \ | | | | | | '_ \
\__ \__ \ | | |_____|   <  __/ |_| | |_| | |_) |
|___/___/_| |_|     |_|\_\___|\__, |\__,_| .__/
                              |___/      |_|"""

    WIDTH = 48
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    HIDE_CUR = "\033[?25l"
    SHOW_CUR = "\033[?25h"

    S_BANNER = ""
    S_VERSION = CYAN + BOLD
    S_SEPARATOR = DIM
    S_HINT = DIM
    S_SSH_WARNING = YELLOW
    S_SSH_INFO = DIM
    S_SUCCESS = GREEN
    S_STATUS = CYAN
    S_FAIL = RED

    STYLE_ATTRS = (
        "BOLD", "DIM", "RESET", "GREEN", "RED", "YELLOW", "CYAN",
        "HIDE_CUR", "SHOW_CUR", "S_BANNER", "S_VERSION", "S_SEPARATOR",
        "S_HINT", "S_SSH_WARNING", "S_SSH_INFO", "S_SUCCESS", "S_STATUS",
        "S_FAIL",
    )

    def __init__(self) -> None:
        if not sys.stdout.isatty():
            for attr in CLI.STYLE_ATTRS:
                setattr(CLI, attr, "")

    @staticmethod
    def enable_ansi() -> None:
        """Enable ANSI escape sequences on Windows 10+."""
        if sys.platform == "win32":
            # Best effort, colors just stay off if this fails
            with contextlib.suppress(Exception):
                k = ctypes.windll.kernel32
                h = k.GetStdHandle(-11)
                m = ctypes.c_ulong()
                k.GetConsoleMode(h, ctypes.byref(m))
                k.SetConsoleMode(h, m.value | 0x0004)

    @staticmethod
    def banner() -> None:
        """Print the ASCII banner and version."""
        print(f"{CLI.S_BANNER}{CLI.BANNER}{CLI.RESET}")
        ver = ("v" + __version__).rjust(CLI.WIDTH)
        print(f"{CLI.S_VERSION}{ver}{CLI.RESET}")

    @staticmethod
    def separator() -> None:
        """Print a horizontal separator line."""
        print(f"\n{CLI.S_SEPARATOR}{'-' * CLI.WIDTH}{CLI.RESET}\n")

    @staticmethod
    def hint(msg: str) -> None:
        """Print a hint/informational message."""
        print(f"{CLI.S_HINT}{msg}{CLI.RESET}")

    @staticmethod
    def warn(msg: str) -> None:
        """Print a warning message, pip-style."""
        print(f"{CLI.YELLOW}Warning:{CLI.RESET} {msg}")

    @staticmethod
    def fail(msg: str) -> None:
        """Print an error message, pip-style."""
        nl = "\n" if msg.startswith("\n") else ""
        print(f"{nl}{CLI.RED}Error:{CLI.RESET} {msg.lstrip()}")

    @staticmethod
    def fatal(msg: str) -> None:
        """Print an error message and exit."""
        CLI.fail(msg)
        sys.exit(1)

    @staticmethod
    def success(msg: str) -> None:
        """Print a success message."""
        print(f"{CLI.S_SUCCESS}Done!{CLI.RESET} {msg}")

    @staticmethod
    def status(msg: str, end: str = "\n") -> None:
        """Print a status/progress message."""
        print(f"{CLI.S_STATUS}{msg}{CLI.RESET}", end=end, flush=True)

    @staticmethod
    def ok(msg: str = "ok") -> None:
        """Finish a status line with a success result."""
        print(f"{CLI.S_SUCCESS}{msg}{CLI.RESET}", flush=True)

    @staticmethod
    def failed(msg: str = "failed") -> None:
        """Finish a status line with a failure result."""
        print(f"{CLI.S_FAIL}{msg}{CLI.RESET}", flush=True)

    @staticmethod
    def cancel(msg: str = "") -> None:
        """Print a cancellation message."""
        print(f"{CLI.YELLOW}Cancelled.{CLI.RESET}", end="")
        if msg:
            print(f" {msg}")
        else:
            print()

    @staticmethod
    def ssh_warning(msg: str) -> None:
        """Print an SSH warning line."""
        print(f"{CLI.S_SSH_WARNING}{msg}{CLI.RESET}")

    @staticmethod
    def ssh_info(msg: str) -> None:
        """Print an SSH info/detail line."""
        print(f"  {CLI.S_SSH_INFO}{msg}{CLI.RESET}")

    @staticmethod
    def prompt(
        label: str, value: str | None = None, *,
        hint: str = "", default: str = "",
    ) -> str:
        """Prompt for input, or display and return a pre-supplied value."""
        if value:
            print(f"{label}: {value}")
            return value
        if default:
            suffix = f" [{CLI.CYAN}{default}{CLI.RESET}]"
        elif hint:
            suffix = f" {CLI.S_HINT}({hint}){CLI.RESET}"
        else:
            suffix = ""
        result = input(f"{label}{suffix}: ").strip()
        return result if result else default

    @staticmethod
    def msg(msg: str = "") -> None:
        """Print an unstyled message."""
        print(msg)

    @staticmethod
    def _read_key() -> str:
        """Read a single keypress."""
        if sys.platform == "win32":
            ch = msvcrt.getwch()
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch in ("\r", "\n"):
                return "enter"
            if ch in ("\xe0", "\x00"):
                return {"K": "left", "M": "right"}.get(msvcrt.getwch(), "")
            return "esc" if ch == "\x1b" else ch
        else:
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
                if ch in ("\r", "\n"):
                    return "enter"
                if ch == "\x1b":
                    if sys.stdin.read(1) == "[":
                        return {"D": "left", "C": "right"}.get(
                            sys.stdin.read(1), "")
                    return "esc"
                if ch == "\x03":
                    raise KeyboardInterrupt
                return ch
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)

    @staticmethod
    def ask_yn(prompt: str, default: bool = False) -> bool:
        """Interactive yes/no selector with arrow keys."""
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return default

        sel = 0 if default else 1

        def _render() -> str:
            yes = (f"{CLI.GREEN}{CLI.BOLD}[ Yes ]{CLI.RESET}" if sel == 0
                   else f"{CLI.DIM}  Yes  {CLI.RESET}")
            no = (f"{CLI.RED}{CLI.BOLD}[ No ]{CLI.RESET}" if sel == 1
                  else f"{CLI.DIM}  No  {CLI.RESET}")
            return f"\r\033[2K{prompt}  {yes}  {no}"

        sys.stdout.write(CLI.HIDE_CUR)
        sys.stdout.flush()
        try:
            while True:
                sys.stdout.write(_render())
                sys.stdout.flush()
                key = CLI._read_key()
                if key in ("left", "right", "y", "n"):
                    if key == "y":
                        sel = 0
                    elif key == "n":
                        sel = 1
                    else:
                        sel = 1 - sel
                elif key == "enter":
                    sys.stdout.write(_render() + "\n")
                    sys.stdout.flush()
                    return sel == 0
                elif key == "esc":
                    sel = 1
                    sys.stdout.write(_render() + "\n")
                    sys.stdout.flush()
                    return False
        finally:
            sys.stdout.write(CLI.SHOW_CUR)
            sys.stdout.flush()


cli = CLI()


class Runner:
    """Run SSH commands natively or via Git Bash as fallback."""

    @staticmethod
    def _find_git_bash() -> str | None:
        """Locate Git Bash on Windows for use as an SSH fallback."""
        git = which("git")
        if not git:
            return None
        root = Path(git).resolve().parent.parent
        for name in ("git-bash.exe", "bin/bash.exe"):
            p = root / name
            if p.exists():
                return str(p)
        return None

    def __init__(self) -> None:
        self.git_bash = Runner._find_git_bash()
        openssh = all(which(c) for c in ("ssh", "ssh-keygen"))
        self.mode: str | None = (
            "native" if openssh
            else ("gitbash" if self.git_bash else None)
        )

    def check(self) -> None:
        """Exit with guidance if no SSH tools are available."""
        if self.mode:
            return
        if sys.platform == "win32":
            cli.fatal(
                "No SSH tools found. Install OpenSSH Client "
                "(Settings > Optional Features) or Git for Windows")
        else:
            cli.fatal(
                "No SSH tools found. Install with: "
                "sudo apt install openssh-client")

    def _subprocess_args(
        self, cmd: list[str] | str,
    ) -> tuple[list[str] | str, bool]:
        """Prepare the command and shell flag for subprocess.run."""
        if self.mode == "native":
            return cmd, isinstance(cmd, str)
        if self.git_bash is None:
            raise RuntimeError("gitbash mode without Git Bash path")
        sh = (cmd if isinstance(cmd, str)
              else " ".join(shlex.quote(a) for a in cmd))
        return [self.git_bash, "-c", sh], False

    def run(self, cmd: list[str] | str, **kwargs) -> int:
        """Run a command and return the exit code."""
        args, shell = self._subprocess_args(cmd)
        r = subprocess.run(args, shell=shell, check=False, **kwargs)
        return r.returncode

    def run_capture(
        self, cmd: list[str] | str, **kwargs,
    ) -> tuple[int, str]:
        """Run a command, capture stderr, return (rc, text)."""
        args, shell = self._subprocess_args(cmd)
        r = subprocess.run(args, shell=shell, check=False,
                           stderr=subprocess.PIPE, **kwargs)
        return r.returncode, (r.stderr or b"").decode(errors="replace")

    def run_stdout(
        self, cmd: list[str] | str, **kwargs,
    ) -> tuple[int, str]:
        """Run a command, capture stdout, return (rc, text)."""
        args, shell = self._subprocess_args(cmd)
        r = subprocess.run(args, shell=shell, check=False,
                           stdout=subprocess.PIPE, **kwargs)
        return r.returncode, (r.stdout or b"").decode(errors="replace")


class SSHConfig:
    """Manage ssh-keyup entries in ~/.ssh/config."""

    @staticmethod
    def _find_managed_blocks(text: str) -> dict[str, tuple[int, int]]:
        """Find ssh-keyup managed blocks in SSH config text."""
        blocks: dict[str, tuple[int, int]] = {}
        for m in re.finditer(
            r"^#ssh-keyup:begin (\S+)[^\n]*\n.*?^#ssh-keyup:end \1[^\n]*\n?",
            text, re.MULTILINE | re.DOTALL,
        ):
            blocks[m.group(1)] = (m.start(), m.end())
        return blocks

    @staticmethod
    def _has_unmanaged_host(
        text: str, alias: str, managed_blocks: dict[str, tuple[int, int]],
    ) -> bool:
        """Check for a Host entry outside managed markers."""
        for m in re.finditer(r"^Host\s+(\S+)", text, re.MULTILINE):
            if m.group(1) != alias:
                continue
            pos = m.start()
            if not any(s <= pos < e for s, e in managed_blocks.values()):
                return True
        return False

    @staticmethod
    def _build_block(
        alias: str, host: str, user: str, file_alias: str,
        port: int = SSH_PORT,
    ) -> str:
        """Build the SSH config block text for a managed host entry."""
        stamp = datetime.now(timezone.utc).astimezone().date().isoformat()
        port_line = f"    Port {port}\n" if port != SSH_PORT else ""
        return (
            f"#ssh-keyup:begin {alias} {stamp}\n"
            f"Host {alias}\n"
            f"    HostName {host}\n"
            f"    User {user}\n"
            f"{port_line}"
            f"    IdentityFile ~/.ssh/id_ed25519_{file_alias}\n"
            f"#ssh-keyup:end {alias}\n"
        )

    @staticmethod
    def _splice_out(text: str, span: tuple[int, int]) -> str:
        """Remove a text span, collapsing surrounding blank lines."""
        start, end = span
        before = text[:start].rstrip("\n")
        after = text[end:].lstrip("\n")
        if before and after:
            return before + "\n\n" + after
        return before or after

    @staticmethod
    def check_existing(ssh_config: Path, alias: str) -> tuple[str, bool]:
        """Check for an existing alias, prompt to overwrite."""
        if not ssh_config.exists():
            return "", False

        text = ssh_config.read_text(encoding="utf-8")
        blocks = SSHConfig._find_managed_blocks(text)

        has_unmanaged = SSHConfig._has_unmanaged_host(text, alias, blocks)
        has_managed = alias in blocks

        if has_unmanaged:
            cli.fail(f"Host '{alias}' already exists in SSH config "
                     "(not managed by ssh-keyup).")
            cli.msg(f"Use a different alias or remove the existing entry "
                    f"from {ssh_config}")
            sys.exit(1)

        if not has_managed:
            return text, False

        msg = f"'{alias}' already configured by ssh-keyup. Overwrite?"
        if not cli.ask_yn(msg):
            cli.cancel("No changes were made.")
            sys.exit(0)

        return SSHConfig._splice_out(text, blocks[alias]), True

    @staticmethod
    def remove_stale(ssh_config: Path, base_text: str) -> None:
        """Write config with the overwritten entry spliced out."""
        SSHConfig._atomic_write(ssh_config, base_text)

    @staticmethod
    def collect_entries(text: str) -> list[dict[str, str]]:
        """Parse managed entries from SSH config text."""
        entries = []
        for m in re.finditer(
            r"^#ssh-keyup:begin (\S+)(?: (\S+))?[^\n]*\n"
            r"(.*?)^#ssh-keyup:end \1",
            text, re.MULTILINE | re.DOTALL,
        ):
            body = m.group(3)

            def field(name: str, body: str = body) -> str:
                fm = re.search(rf"^\s*{name}\s+(\S+)", body, re.MULTILINE)
                return fm.group(1) if fm else "?"

            entries.append({
                "alias": m.group(1),
                "date": m.group(2) or "?",
                "host": field("HostName"),
                "user": field("User"),
                "key": field("IdentityFile"),
            })
        return entries

    @staticmethod
    def list_entries(ssh_config: Path) -> None:
        """Print managed entries as aligned columns."""
        text = (ssh_config.read_text(encoding="utf-8")
                if ssh_config.exists() else "")
        entries = SSHConfig.collect_entries(text)
        if not entries:
            cli.msg("No entries managed by ssh-keyup.")
            return

        header = ("alias", "host", "user", "key", "added")
        rows = [(e["alias"], e["host"], e["user"], e["key"], e["date"])
                for e in entries]
        widths = [max(len(row[i]) for row in rows + [header])
                  for i in range(len(header))]
        cli.hint("  ".join(h.ljust(w) for h, w in zip(header, widths)))
        for row in rows:
            cli.msg("  ".join(v.ljust(w) for v, w in zip(row, widths)))

    @staticmethod
    def remove_entry(ssh_config: Path, alias: str) -> None:
        """Remove a managed entry and its key pair."""
        text = (ssh_config.read_text(encoding="utf-8")
                if ssh_config.exists() else "")
        blocks = SSHConfig._find_managed_blocks(text)
        if alias not in blocks:
            cli.fatal(f"No entry '{alias}' managed by ssh-keyup.")

        start, end = blocks[alias]
        key_m = re.search(r"^\s*IdentityFile\s+(\S+)", text[start:end],
                          re.MULTILINE)

        new_text = SSHConfig._splice_out(text, blocks[alias])
        SSHConfig._atomic_write(ssh_config, new_text)
        cli.msg(f"Removed '{alias}' from {ssh_config}")

        if not key_m:
            return
        key_path = Path(key_m.group(1)).expanduser()
        pub_path = Path(str(key_path) + ".pub")
        if key_path.exists() or pub_path.exists():
            key_path.unlink(missing_ok=True)
            pub_path.unlink(missing_ok=True)
            cli.msg(f"Deleted key pair {key_path.name}")

    @staticmethod
    def _atomic_write(ssh_config: Path, text: str) -> None:
        """Write text to SSH config atomically, one trailing newline."""
        if text:
            text = text.rstrip("\n") + "\n"
        fd, tmp = tempfile.mkstemp(dir=ssh_config.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.replace(tmp, ssh_config)
        except BaseException:
            os.unlink(tmp)
            raise

    @staticmethod
    def update(
        ssh_config: Path, alias: str, host: str, user: str,
        file_alias: str, base_text: str, port: int = SSH_PORT,
    ) -> None:
        """Write or replace the SSH config entry."""
        block = SSHConfig._build_block(alias, host, user, file_alias, port)
        if base_text:
            text = base_text.rstrip("\n") + "\n\n" + block
        else:
            text = block
        SSHConfig._atomic_write(ssh_config, text)


class Deployer:
    """Deploy an SSH public key to a remote host."""

    @staticmethod
    def _is_host_key_changed(stderr: str) -> bool:
        """Return True if stderr indicates the remote host key has changed."""
        return "REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr

    @staticmethod
    def _is_unknown_host(stderr: str) -> bool:
        """Return True if stderr indicates an unknown (first-time) host."""
        return ("Host key verification failed" in stderr
                and "REMOTE HOST IDENTIFICATION HAS CHANGED" not in stderr)

    @staticmethod
    def _format_host_key_info(host: str, stderr: str) -> str | None:
        """Parse verbose SSH stderr into native-looking host key info."""
        key_m = re.search(r"Server host key: (\S+) (\S+)", stderr)
        if not key_m:
            return None
        key_type = key_m.group(1).replace("ssh-", "").upper()
        fingerprint = key_m.group(2)
        ip_m = re.search(r"Connecting to \S+ \[([^\]]+)\]", stderr)
        addr = f" ({ip_m.group(1)})" if ip_m else ""
        lines = [
            f"The authenticity of host '{host}{addr}' can't be established.",
            f"{key_type} key fingerprint is {fingerprint}.",
            "This key is not known by any other names.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _handle_unknown_host(host: str, stderr: str) -> bool:
        """Show host key info, return True if user confirms."""
        info = Deployer._format_host_key_info(host, stderr)
        if info:
            for line in info.splitlines():
                cli.ssh_warning(line)
        if not cli.ask_yn("Are you sure you want to continue connecting?"):
            cli.cancel()
            return False
        return True

    # ssh -v chatter that is not an error and not remote output
    _SSH_NOISE = ("debug1:", "OpenSSH_", "Authenticated to ",
                  "Transferred: ", "Bytes per second: ",
                  "Warning: Permanently added")

    @staticmethod
    def _is_authenticated(stderr: str) -> bool:
        """Return True if verbose stderr shows login succeeded."""
        return "Authenticated to " in stderr

    @staticmethod
    def _error_lines(stderr: str) -> list[str]:
        """Keep ssh errors and remote output, drop -v chatter and repeats."""
        seen: set[str] = set()
        lines = []
        for line in stderr.strip().splitlines():
            if line.startswith(Deployer._SSH_NOISE) or line in seen:
                continue
            seen.add(line)
            lines.append(line)
        return lines

    @staticmethod
    def _report_failure(stderr: str) -> None:
        """Explain failed deploy, a remote error is not a login error."""
        if Deployer._is_authenticated(stderr):
            cli.fail("\nLogged in, but install command failed on device.")
        else:
            cli.fail("\nSSH connection failed. Check host and credentials.")
        for line in Deployer._error_lines(stderr):
            cli.ssh_info(line)

    @staticmethod
    def _ssh_cmd(runner: Runner, remote: str, install_cmd: str,
                 pub_key: str, accept_new: bool = False,
                 port: int = SSH_PORT) -> tuple[int, str]:
        """Run SSH deploy command, verbose so failures can be explained."""
        policy = "accept-new" if accept_new else "yes"
        cmd = ["ssh", "-v"]
        if port != SSH_PORT:
            cmd.extend(["-p", str(port)])
        cmd.extend(["-o", f"StrictHostKeyChecking={policy}",
                    remote, install_cmd])
        return runner.run_capture(cmd, input=pub_key.encode())

    @staticmethod
    def deploy(runner: Runner, user: str, host: str, pub_path: Path,
               port: int = SSH_PORT) -> bool:
        """Deploy the public key to the remote host in a single SSH session."""
        remote = f"{user}@{host}"
        pub_key = pub_path.read_text(encoding="utf-8").strip()
        shown = remote if port == SSH_PORT else f"{remote}:{port}"
        cli.status(f"Deploying key to {shown} ...")

        install_cmd = (
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            "key=$(cat) && "
            "if ! grep -qF \"$key\" ~/.ssh/authorized_keys 2>/dev/null; then "
            "printf '%s\\n' \"$key\" >> ~/.ssh/authorized_keys; fi && "
            "chmod 600 ~/.ssh/authorized_keys"
        )

        rc, stderr = Deployer._ssh_cmd(runner, remote, install_cmd, pub_key,
                                       port=port)

        if rc != 0 and Deployer._is_host_key_changed(stderr):
            for line in stderr.strip().splitlines():
                if not line.startswith("debug1:"):
                    cli.ssh_warning(line)
            cli.msg()
            if cli.ask_yn("Remove old host key and retry?"):
                cli.status(f"Removing old host key for {host} ...")
                # Non-default ports are keyed as [host]:port in known_hosts
                known = host if port == SSH_PORT else f"[{host}]:{port}"
                runner.run(["ssh-keygen", "-R", known])
                rc, stderr = Deployer._ssh_cmd(
                    runner, remote, install_cmd, pub_key, accept_new=True,
                    port=port)
                if rc != 0:
                    Deployer._report_failure(stderr)
                    return False
            else:
                cli.msg(f"\nAborted. To fix manually:\n  ssh-keygen -R {host}")
                return False
        elif rc != 0 and Deployer._is_unknown_host(stderr):
            if not Deployer._handle_unknown_host(host, stderr):
                return False
            rc, stderr = Deployer._ssh_cmd(
                runner, remote, install_cmd, pub_key, accept_new=True,
                port=port)
            if rc != 0:
                Deployer._report_failure(stderr)
                return False
        elif rc != 0:
            Deployer._report_failure(stderr)
            return False

        return True


def sanitize_alias(name: str, quiet: bool = False) -> str:
    """Replace non-alphanumeric characters (except - and _) with dashes."""
    clean = "".join(
        c if c.isalnum() or c in "-_" else "-"
        for c in name
    ) or "host"
    if not quiet and clean != name:
        cli.hint(f"(sanitized to: {clean})")
    return clean


def is_ip(value: str) -> bool:
    """Return True if the value is a valid IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def split_target(target: str) -> tuple[str | None, str]:
    """Split a [user@]host target into user and host."""
    if "@" in target:
        user, host = target.rsplit("@", 1)
        return (user or None), host
    return None, target


def probe_port(host: str, port: int) -> tuple[str, str] | None:
    """Try a TCP connect, return (reason, detail) on failure."""
    try:
        with socket.create_connection((host, port), CONNECT_TIMEOUT):
            return None
    except socket.gaierror:
        return (f"Could not resolve hostname '{host}'.",
                "Check spelling or use an IP address instead.")
    except ConnectionRefusedError:
        return (f"Connection refused by {host} on port {port}.",
                "Host is up but no SSH server is listening.")
    except socket.timeout:
        return (f"No response from {host} on port {port}.",
                "Device may be off or on a different network.")
    except OSError as ex:
        return f"Cannot reach {host}.", str(ex)


def resolve_ssh_target(runner: Runner, host: str) -> tuple[str, int] | None:
    """Resolve host through ssh config, return effective (hostname, port)."""
    rc, out = runner.run_stdout(["ssh", "-G", host],
                                stderr=subprocess.DEVNULL)
    if rc != 0:
        return None
    hostname, port = None, None
    for line in out.splitlines():
        key, _, value = line.partition(" ")
        if key == "hostname":
            hostname = value.strip()
        elif key == "port":
            with contextlib.suppress(ValueError):
                port = int(value)
    if not hostname:
        return None
    return hostname, port or SSH_PORT


def resolve_host(runner: Runner, host: str) -> tuple[str, int]:
    """Resolve a typed host through ssh config to (hostname, port)."""
    target = resolve_ssh_target(runner, host)
    if not target:
        return host, SSH_PORT

    rhost, rport = target
    if rhost.lower() != host.lower():
        # Alias stops resolving once its block is rewritten
        cli.hint(f"'{host}' resolves to {rhost} via SSH config")
    return rhost, rport


def check_reachable(host: str, port: int = SSH_PORT) -> None:
    """Probe SSH port. On failure explain why and offer to continue."""
    cli.status("Checking connection ... ", end="")
    failure = probe_port(host, port)
    if failure is None:
        cli.ok()
        return

    cli.failed()
    cli.warn(failure[0])
    cli.ssh_info(failure[1])
    cli.msg()
    if not cli.ask_yn("Continue anyway?"):
        cli.cancel()
        sys.exit(0)


_DESCRIPTION = (
    "Set up SSH key auth in one command.\n"
    "Generates a per-host Ed25519 key pair, deploys it\n"
    "to the remote host and adds an entry to ~/.ssh/config."
)

_EXAMPLES = [
    ("ssh-keyup", "interactive mode"),
    ("ssh-keyup pi@192.168.1.23 mypi", "user, host and alias"),
    ("ssh-keyup trinity@rpi-5.local", "alias defaults to rpi-5"),
    ("ssh-keyup 192.168.1.23", "prompts for username and alias"),
    ("ssh-keyup --host rpi-5 --user pi --alias mypi", "flags work too"),
    ("ssh-keyup --list", "show managed entries"),
    ("ssh-keyup --remove mypi", "delete a managed entry"),
]

_CMD_WIDTH = max(len(cmd) for cmd, _ in _EXAMPLES) + 2

_EPILOG = "examples:\n" + "\n".join(
    f"  {cmd.ljust(_CMD_WIDTH)}{desc}" for cmd, desc in _EXAMPLES
)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    p = argparse.ArgumentParser(
        prog="ssh-keyup",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    p.add_argument("target", nargs="?", metavar="[user@]host",
                   help="remote device, e.g. pi@192.168.1.23")
    p.add_argument("alias_pos", nargs="?", metavar="alias",
                   help="friendly name for ~/.ssh/config (default: hostname)")
    p.add_argument("--host",
                   help="IP address or hostname of the remote device")
    p.add_argument("--user",
                   help="login username on the remote device")
    p.add_argument("--alias",
                   help="friendly name for ~/.ssh/config (default: hostname)")
    p.add_argument("--list", action="store_true",
                   help="list entries managed by ssh-keyup")
    p.add_argument("--remove", metavar="ALIAS",
                   help="remove a managed entry from ~/.ssh/config")
    args = p.parse_args()

    if args.list and args.remove:
        p.error("--list and --remove cannot be combined")
    if ((args.list or args.remove)
            and any((args.target, args.alias_pos,
                     args.host, args.user, args.alias))):
        p.error("--list/--remove cannot be combined with setup arguments")

    if args.target:
        user, host = split_target(args.target)
        if args.host:
            p.error("host given both as positional and --host")
        args.host = host
        if user:
            if args.user:
                p.error("user given both as user@ prefix and --user")
            args.user = user
    if args.alias_pos:
        if args.alias:
            p.error("alias given both as positional and --alias")
        args.alias = args.alias_pos

    return args


def gather_input(
    args: argparse.Namespace, runner: Runner,
) -> tuple[str, str, str, int]:
    """Collect host, username, alias and port from args or prompts."""
    typed = cli.prompt("Remote host", args.host, hint="IP or name")
    if not typed:
        cli.fatal("No host provided.")

    host, port = resolve_host(runner, typed)
    check_reachable(host, port)

    user = cli.prompt("Username", args.user)
    if not user:
        cli.fatal("No username provided.")

    if args.alias:
        alias = cli.prompt("Alias", args.alias)
    elif is_ip(typed):
        alias = cli.prompt("Alias")
        if not alias:
            cli.fatal("No alias provided.")
    else:
        raw = typed[:-6] if typed.endswith(".local") else typed
        alias = cli.prompt("Alias", default=sanitize_alias(raw, quiet=True))

    alias = sanitize_alias(alias)

    return host, user, alias, port


def generate_key(runner: Runner, key_path: Path) -> None:
    """Generate an Ed25519 key pair."""
    if runner.mode == "native":
        rc = runner.run([
            "ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path),
        ])
    else:
        rc = runner.run(
            f"ssh-keygen -t ed25519 -N '' -f ~/.ssh/{key_path.name}"
        )

    if rc != 0:
        cli.fatal("ssh-keygen failed.")


def discard_keys(key_path: Path, pub_path: Path) -> None:
    """Delete a key pair generated during a failed run."""
    cli.status("Cleaning up generated key pair...")
    key_path.unlink(missing_ok=True)
    pub_path.unlink(missing_ok=True)


def main() -> None:
    """Entry point: gather input, generate keys, deploy, update config."""
    try:
        cli.enable_ansi()
        args = parse_args()

        ssh_config = Path.home() / ".ssh" / "config"
        if args.list:
            SSHConfig.list_entries(ssh_config)
            return
        if args.remove:
            SSHConfig.remove_entry(ssh_config, args.remove)
            return

        cli.banner()
        cli.separator()

        runner = Runner()
        runner.check()

        host, user, alias, port = gather_input(args, runner)
        file_alias = alias.replace("-", "_")

        cli.separator()

        ssh_dir = Path.home() / ".ssh"
        ssh_config = ssh_dir / "config"
        config_base, overwriting = SSHConfig.check_existing(ssh_config, alias)

        ssh_dir.mkdir(parents=True, exist_ok=True)

        key_path = ssh_dir / f"id_ed25519_{file_alias}"
        pub_path = ssh_dir / f"id_ed25519_{file_alias}.pub"

        key_generated = False
        if pub_path.exists():
            cli.msg(f"Key pair exists {pub_path}")
            if cli.ask_yn("Regenerate key pair?"):
                key_path.unlink(missing_ok=True)
                pub_path.unlink()
                generate_key(runner, key_path)
                key_generated = True
        else:
            generate_key(runner, key_path)
            key_generated = True

        cli.separator()
        # Remove stale entry now, otherwise deploy's ssh would resolve
        # the typed host through its old HostName. Stays removed on
        # deploy failure, user chose to overwrite.
        try:
            if overwriting:
                try:
                    SSHConfig.remove_stale(ssh_config, config_base)
                except OSError as ex:
                    if key_generated:
                        discard_keys(key_path, pub_path)
                    cli.fatal(f"SSH config update failed: {ex}")
            deployed = Deployer.deploy(runner, user, host, pub_path, port)
        except KeyboardInterrupt:
            if key_generated:
                cli.msg()
                discard_keys(key_path, pub_path)
            raise
        if not deployed:
            if key_generated:
                discard_keys(key_path, pub_path)
            sys.exit(1)

        try:
            SSHConfig.update(ssh_config, alias, host, user, file_alias,
                             config_base, port)
        except OSError as ex:
            cli.fatal(f"Key deployed, but SSH config update failed: {ex}")
        cli.msg(f"Config updated {ssh_config}")

        cli.separator()
        cli.success(f"SSH key deployed for '{alias}'.\n")
    except KeyboardInterrupt:
        sys.stdout.write(CLI.SHOW_CUR)
        sys.stdout.flush()
        cli.msg()
        cli.cancel()
        sys.exit(130)


if __name__ == "__main__":
    main()
