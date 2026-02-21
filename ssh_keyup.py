#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
#
# ssh-keyup - Passwordless SSH setup for Raspberry Pi, NVIDIA Jetson, or any Linux device
#
# Copyright (c) 2026, UAB Kurokesu. All rights reserved.

__version__ = "1.0.0"

import argparse
import ipaddress
import os
import re
import shlex
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from shutil import which
from typing import Dict, List, Optional, Tuple, Union

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
HIDE_CUR = "\033[?25l"
SHOW_CUR = "\033[?25h"
WIDTH = 48

BANNER = r"""
         _           _
 ___ ___| |__       | | _____ _   _ _   _ _ __
/ __/ __| '_ \ _____| |/ / _ \ | | | | | | '_ \
\__ \__ \ | | |_____|   <  __/ |_| | |_| | |_) |
|___/___/_| |_|     |_|\_\___|\__, |\__,_| .__/
                              |___/      |_|"""

if not sys.stdout.isatty():
    BOLD = DIM = RESET = GREEN = RED = YELLOW = ""
    HIDE_CUR = SHOW_CUR = ""


def _enable_ansi() -> None:
    """Enable ANSI escape sequences on Windows 10+."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k.GetStdHandle(-11)
        m = ctypes.c_ulong()
        k.GetConsoleMode(h, ctypes.byref(m))
        k.SetConsoleMode(h, m.value | 0x0004)
    except Exception:
        pass


def separator() -> None:
    """Print a horizontal separator line with surrounding spacing."""
    print(f"\n{DIM}{'-' * WIDTH}{RESET}\n")


def warn(msg: str) -> None:
    """Print a warning message in yellow, pip-style."""
    print(f"{YELLOW}Warning:{RESET} {msg}")


def fail(msg: str) -> None:
    """Print an error message in red, pip-style."""
    nl = "\n" if msg.startswith("\n") else ""
    print(f"{nl}{RED}Error:{RESET} {msg.lstrip()}")


def die(msg: str) -> None:
    """Print an error message and exit."""
    fail(msg)
    sys.exit(1)


def _read_key() -> str:
    """Read a single keypress, returning 'left', 'right', 'enter', 'esc', or the character."""
    if sys.platform == "win32":
        import msvcrt
        ch = msvcrt.getwch()
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in ("\r", "\n"):
            return "enter"
        if ch in ("\xe0", "\x00"):
            return {"K": "left", "M": "right"}.get(msvcrt.getwch(), "")
        return "esc" if ch == "\x1b" else ch
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                return "enter"
            if ch == "\x1b":
                if sys.stdin.read(1) == "[":
                    return {"D": "left", "C": "right"}.get(sys.stdin.read(1), "")
                return "esc"
            if ch == "\x03":
                raise KeyboardInterrupt
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def ask_yn(prompt: str, default: bool = False) -> bool:
    """Interactive yes/no selector with arrow keys. Falls back to default for non-TTY."""
    if not sys.stdin.isatty():
        return default

    sel = 0 if default else 1

    def _render() -> str:
        yes = f"{GREEN}{BOLD}[ Yes ]{RESET}" if sel == 0 else f"{DIM}  Yes  {RESET}"
        no = f"{RED}{BOLD}[ No ]{RESET}" if sel == 1 else f"{DIM}  No  {RESET}"
        return f"\r\033[2K{prompt}  {yes}  {no}"

    sys.stdout.write(HIDE_CUR)
    sys.stdout.flush()
    try:
        while True:
            sys.stdout.write(_render())
            sys.stdout.flush()
            key = _read_key()
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
        sys.stdout.write(SHOW_CUR)
        sys.stdout.flush()


def _find_git_bash() -> Optional[str]:
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


class Runner:
    """Run SSH commands natively or via Git Bash as fallback."""

    def __init__(self) -> None:
        self.git_bash = _find_git_bash()
        openssh = all(which(c) for c in ("ssh", "ssh-keygen"))
        self.mode = (
            "native" if openssh
            else ("gitbash" if self.git_bash else None)
        )  # type: Optional[str]

    def check(self) -> None:
        """Exit with guidance if no SSH tools are available."""
        if self.mode:
            return
        if sys.platform == "win32":
            die("No SSH tools found. Install OpenSSH Client (Settings > Optional Features) or Git for Windows")
        else:
            die("No SSH tools found. Install with: sudo apt install openssh-client")

    def _subprocess_args(
        self, cmd: Union[List[str], str],
    ) -> Tuple[Union[List[str], str], bool]:
        """Prepare the command and shell flag for subprocess.run."""
        if self.mode == "native":
            return cmd, isinstance(cmd, str)
        assert self.git_bash
        sh = cmd if isinstance(cmd, str) else " ".join(shlex.quote(a) for a in cmd)
        return [self.git_bash, "-c", sh], False

    def run(self, cmd: Union[List[str], str], **kwargs) -> int:
        """Run a command and return the exit code."""
        args, shell = self._subprocess_args(cmd)
        return subprocess.run(args, shell=shell, **kwargs).returncode

    def run_capture(self, cmd: Union[List[str], str], **kwargs) -> Tuple[int, str]:
        """Run a command, capture stderr, and return (exit_code, stderr_text)."""
        args, shell = self._subprocess_args(cmd)
        r = subprocess.run(args, shell=shell, stderr=subprocess.PIPE, **kwargs)
        return r.returncode, (r.stderr or b"").decode(errors="replace")


def sanitize_alias(name: str) -> str:
    """Replace non-alphanumeric characters (except - and _) with dashes."""
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name) or "host"


def is_ip(value: str) -> bool:
    """Return True if the value is a valid IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _is_host_key_error(stderr: str) -> bool:
    """Return True if stderr indicates an SSH host key mismatch."""
    return ("REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr
            or "Host key verification failed" in stderr)


def _find_managed_blocks(text: str) -> Dict[str, Tuple[int, int]]:
    """Find ssh-keyup managed blocks in SSH config text."""
    blocks = {}  # type: Dict[str, Tuple[int, int]]
    for m in re.finditer(
        r"^#ssh-keyup:begin (\S+)[^\n]*\n.*?^#ssh-keyup:end \1[^\n]*\n?",
        text, re.MULTILINE | re.DOTALL,
    ):
        blocks[m.group(1)] = (m.start(), m.end())
    return blocks


def _has_unmanaged_host(
    text: str, alias: str, managed_blocks: Dict[str, Tuple[int, int]],
) -> bool:
    """Return True if a Host entry for *alias* exists outside managed markers."""
    for m in re.finditer(r"^Host\s+(\S+)", text, re.MULTILINE):
        if m.group(1) != alias:
            continue
        pos = m.start()
        if not any(s <= pos < e for s, e in managed_blocks.values()):
            return True
    return False


def _build_config_block(
    alias: str, host: str, user: str, file_alias: str,
) -> str:
    """Build the SSH config block text for a managed host entry."""
    return (
        f"#ssh-keyup:begin {alias} {date.today().isoformat()}\n"
        f"Host {alias}\n"
        f"    HostName {host}\n"
        f"    User {user}\n"
        f"    IdentityFile ~/.ssh/id_ed25519_{file_alias}\n"
        f"#ssh-keyup:end {alias}\n"
    )


def check_existing_alias(ssh_config: Path, alias: str) -> str:
    """Check SSH config for an existing alias, prompting to overwrite if found."""
    if not ssh_config.exists():
        return ""

    text = ssh_config.read_text(encoding="utf-8")
    blocks = _find_managed_blocks(text)

    has_unmanaged = _has_unmanaged_host(text, alias, blocks)
    has_managed = alias in blocks

    if has_unmanaged:
        fail(f"Host '{alias}' already exists in SSH config (not managed by ssh-keyup).")
        print(f"Use a different alias or remove the existing entry from {ssh_config}")
        sys.exit(1)

    if not has_managed:
        return text

    if not ask_yn(f"'{alias}' already configured by ssh-keyup. Overwrite?"):
        print(f"\n{YELLOW}Cancelled.{RESET} No changes were made.")
        sys.exit(0)

    start, end = blocks[alias]
    before = text[:start].rstrip("\n")
    after = text[end:].lstrip("\n")
    if before and after:
        return before + "\n\n" + after
    return before or after


def update_ssh_config(
    ssh_config: Path, alias: str, host: str, user: str,
    file_alias: str, base_text: str,
) -> None:
    """Write or replace the SSH config entry for a managed host."""
    block = _build_config_block(alias, host, user, file_alias)
    if base_text:
        text = base_text.rstrip("\n") + "\n\n" + block + "\n"
    else:
        text = block + "\n"
    fd, tmp = tempfile.mkstemp(dir=ssh_config.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, ssh_config)
    except BaseException:
        os.unlink(tmp)
        raise


def gather_input(args: argparse.Namespace) -> Tuple[str, str, str]:
    """Collect remote host, username, and alias from args or interactive prompts."""
    host = args.host or input(
        f"Remote host {DIM}(IP or name){RESET}: "
    ).strip()
    if not host:
        die("No host provided.")
    elif args.host:
        print(f"Remote host: {host}")

    user = args.user or input("Username: ").strip()
    if not user:
        die("No username provided.")
    elif args.user:
        print(f"Username: {user}")

    if args.alias:
        alias_in = args.alias
    elif is_ip(host):
        alias_in = input("Alias: ").strip()
        if not alias_in:
            die("No alias provided.")
    else:
        value = input(f"Alias [{host}]: ").strip()
        alias_in = value if value else host

    alias = sanitize_alias(alias_in)
    if alias != alias_in:
        print(f"{DIM}(sanitized to: {alias}){RESET}")

    return host, user, alias


def generate_key(
    runner: Runner, key_path: Path, pub_path: Path, file_alias: str,
) -> None:
    """Generate an Ed25519 key pair."""
    if runner.mode == "native":
        rc = runner.run([
            "ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path),
        ])
    else:
        rc = runner.run(
            f"ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519_{file_alias}"
        )

    if rc != 0:
        die("ssh-keygen failed.")


def deploy_key(runner: Runner, user: str, host: str, pub_path: Path) -> None:
    """Deploy the public key to the remote host in a single SSH session."""
    remote = f"{user}@{host}"
    pub_key = pub_path.read_text(encoding="utf-8").strip()

    install_cmd = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        "key=$(cat) && "
        "if ! grep -qF \"$key\" ~/.ssh/authorized_keys 2>/dev/null; then "
        "printf '%s\\n' \"$key\" >> ~/.ssh/authorized_keys; fi && "
        "chmod 600 ~/.ssh/authorized_keys"
    )

    rc, stderr = runner.run_capture(
        ["ssh", remote, install_cmd], input=pub_key.encode(),
    )

    if rc != 0 and _is_host_key_error(stderr):
        if stderr.strip():
            for line in stderr.strip().splitlines():
                print(f"{DIM}{line}{RESET}")
            print()
        if ask_yn("Remove old host key and retry?"):
            runner.run(["ssh-keygen", "-R", host])
            rc, stderr = runner.run_capture(
                ["ssh", remote, install_cmd], input=pub_key.encode(),
            )
            if rc != 0:
                die("\nStill can't connect. Check host and credentials.")
        else:
            print(f"\nAborted. To fix manually:\n  ssh-keygen -R {host}")
            sys.exit(0)
    elif rc != 0:
        fail("\nSSH connection failed. Check host and credentials.")
        if stderr.strip():
            seen = set()  # type: set
            for line in stderr.strip().splitlines():
                if line not in seen:
                    seen.add(line)
                    print(f"  {DIM}{line}{RESET}")
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    p = argparse.ArgumentParser(
        prog="ssh-keyup",
        description="SSH key auth for a new device in one command.\n"
                    "Generates a per-host Ed25519 key pair, deploys it to the\n"
                    "remote host, and adds an entry to ~/.ssh/config.",
        epilog="examples:\n"
               "  ssh-keyup.py                                      interactive mode\n"
               "  ssh-keyup.py --host 192.168.1.42 --user pi        with IP address\n"
               "  ssh-keyup.py --host rpi-5 --user trinity           with hostname\n"
               "  ssh-keyup.py --host rpi-5 --user pi --alias mypi   custom alias",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    p.add_argument("--host",
                   help="IP address or hostname of the remote device")
    p.add_argument("--user",
                   help="login username on the remote device")
    p.add_argument("--alias",
                   help="friendly name for ~/.ssh/config (default: hostname)")
    return p.parse_args()


def main() -> None:
    """Entry point: gather input, generate keys, deploy, update config."""
    _enable_ansi()
    args = parse_args()

    print(f"{DIM}{BANNER}{RESET}")
    print(f"{DIM}{('v' + __version__).rjust(WIDTH)}{RESET}")
    separator()

    host, user, alias = gather_input(args)
    file_alias = alias.replace("-", "_")

    separator()

    ssh_dir = Path.home() / ".ssh"
    ssh_config = ssh_dir / "config"
    config_base = check_existing_alias(ssh_config, alias)

    runner = Runner()
    runner.check()
    ssh_dir.mkdir(parents=True, exist_ok=True)

    key_path = ssh_dir / f"id_ed25519_{file_alias}"
    pub_path = ssh_dir / f"id_ed25519_{file_alias}.pub"

    if pub_path.exists():
        print(f"Key pair {GREEN}exists{RESET} {DIM}{pub_path}{RESET}")
        if ask_yn("Regenerate key pair?"):
            key_path.unlink(missing_ok=True)
            pub_path.unlink()
            print("Generating key pair ...")
            generate_key(runner, key_path, pub_path, file_alias)
    else:
        print("Generating key pair ...")
        generate_key(runner, key_path, pub_path, file_alias)

    separator()
    print(f"{DIM}You may be prompted for the remote password.{RESET}")

    print(f"Deploying key to {user}@{host} ...")
    deploy_key(runner, user, host, pub_path)

    try:
        update_ssh_config(ssh_config, alias, host, user, file_alias, config_base)
    except Exception as ex:
        die(f"Key deployed, but SSH config update failed: {ex}")
    print(f"Config updated {DIM}{ssh_config}{RESET}")

    separator()
    print(f"{GREEN}Done!{RESET} Connect with: {BOLD}ssh {alias}{RESET}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.stdout.write(SHOW_CUR)
        sys.stdout.flush()
        print(f"\n{YELLOW}Cancelled.{RESET}")
        sys.exit(130)
