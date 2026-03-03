#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
#
# ssh-keyup - Passwordless SSH setup for
# Raspberry Pi, NVIDIA Jetson, or any Linux device
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

    def __init__(self) -> None:
        if not sys.stdout.isatty():
            CLI.BOLD = CLI.DIM = CLI.RESET = ""
            CLI.GREEN = CLI.RED = CLI.YELLOW = CLI.CYAN = ""
            CLI.HIDE_CUR = CLI.SHOW_CUR = ""
            CLI.S_BANNER = CLI.S_VERSION = CLI.S_SEPARATOR = ""
            CLI.S_HINT = CLI.S_SSH_WARNING = ""
            CLI.S_SSH_INFO = CLI.S_SUCCESS = CLI.S_STATUS = ""

    @staticmethod
    def enable_ansi() -> None:
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
        cli.fail(msg)
        sys.exit(1)

    @staticmethod
    def success(msg: str) -> None:
        """Print a success message."""
        print(f"{CLI.S_SUCCESS}Done!{CLI.RESET} {msg}")

    @staticmethod
    def status(msg: str) -> None:
        """Print a status/progress message."""
        print(f"{CLI.S_STATUS}{msg}{CLI.RESET}")

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
        label: str, value: Optional[str] = None, *,
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
        if not sys.stdin.isatty():
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

    def __init__(self) -> None:
        self.git_bash = Runner._find_git_bash()
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
            cli.fatal(
                "No SSH tools found. Install OpenSSH Client "
                "(Settings > Optional Features) or Git for Windows")
        else:
            cli.fatal(
                "No SSH tools found. Install with: "
                "sudo apt install openssh-client")

    def _subprocess_args(
        self, cmd: Union[List[str], str],
    ) -> Tuple[Union[List[str], str], bool]:
        """Prepare the command and shell flag for subprocess.run."""
        if self.mode == "native":
            return cmd, isinstance(cmd, str)
        assert self.git_bash
        sh = (cmd if isinstance(cmd, str)
              else " ".join(shlex.quote(a) for a in cmd))
        return [self.git_bash, "-c", sh], False

    def run(self, cmd: Union[List[str], str], **kwargs) -> int:
        """Run a command and return the exit code."""
        args, shell = self._subprocess_args(cmd)
        return subprocess.run(args, shell=shell, **kwargs).returncode

    def run_capture(
        self, cmd: Union[List[str], str], **kwargs,
    ) -> Tuple[int, str]:
        """Run a command, capture stderr, return (rc, text)."""
        args, shell = self._subprocess_args(cmd)
        r = subprocess.run(args, shell=shell, stderr=subprocess.PIPE, **kwargs)
        return r.returncode, (r.stderr or b"").decode(errors="replace")


class SSHConfig:
    """Manage ssh-keyup entries in ~/.ssh/config."""

    @staticmethod
    def _find_managed_blocks(text: str) -> Dict[str, Tuple[int, int]]:
        """Find ssh-keyup managed blocks in SSH config text."""
        blocks = {}  # type: Dict[str, Tuple[int, int]]
        for m in re.finditer(
            r"^#ssh-keyup:begin (\S+)[^\n]*\n.*?^#ssh-keyup:end \1[^\n]*\n?",
            text, re.MULTILINE | re.DOTALL,
        ):
            blocks[m.group(1)] = (m.start(), m.end())
        return blocks

    @staticmethod
    def _has_unmanaged_host(
        text: str, alias: str, managed_blocks: Dict[str, Tuple[int, int]],
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

    @staticmethod
    def check_existing(ssh_config: Path, alias: str) -> Tuple[str, bool]:
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

        start, end = blocks[alias]
        before = text[:start].rstrip("\n")
        after = text[end:].lstrip("\n")
        if before and after:
            return before + "\n\n" + after, True
        return before or after, True

    @staticmethod
    def _atomic_write(ssh_config: Path, text: str) -> None:
        """Write text to SSH config atomically."""
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
        file_alias: str, base_text: str,
    ) -> None:
        """Write or replace the SSH config entry."""
        block = SSHConfig._build_block(alias, host, user, file_alias)
        if base_text:
            text = base_text.rstrip("\n") + "\n\n" + block + "\n"
        else:
            text = block + "\n"
        SSHConfig._atomic_write(ssh_config, text)

    @staticmethod
    def revert(ssh_config: Path, base_text: str) -> None:
        """Restore SSH config to its pre-update state."""
        SSHConfig._atomic_write(ssh_config, base_text)


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
    def _format_host_key_info(host: str, stderr: str) -> Optional[str]:
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

    @staticmethod
    def _ssh_cmd(runner: Runner, remote: str, install_cmd: str,
                 pub_key: str, accept_new: bool = False) -> Tuple[int, str]:
        """Run the SSH deploy command."""
        policy = "accept-new" if accept_new else "yes"
        cmd = ["ssh"]
        if not accept_new:
            cmd.append("-v")
        cmd.extend(["-o", f"StrictHostKeyChecking={policy}",
                    remote, install_cmd])
        return runner.run_capture(cmd, input=pub_key.encode())

    @staticmethod
    def deploy(runner: Runner, user: str, host: str, pub_path: Path) -> bool:
        """Deploy the public key to the remote host in a single SSH session."""
        remote = f"{user}@{host}"
        pub_key = pub_path.read_text(encoding="utf-8").strip()
        cli.status(f"Deploying key to {user}@{host} ...")

        install_cmd = (
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            "key=$(cat) && "
            "if ! grep -qF \"$key\" ~/.ssh/authorized_keys 2>/dev/null; then "
            "printf '%s\\n' \"$key\" >> ~/.ssh/authorized_keys; fi && "
            "chmod 600 ~/.ssh/authorized_keys"
        )

        rc, stderr = Deployer._ssh_cmd(runner, remote, install_cmd, pub_key)

        if rc != 0 and Deployer._is_host_key_changed(stderr):
            for line in stderr.strip().splitlines():
                if not line.startswith("debug1:"):
                    cli.ssh_warning(line)
            cli.msg()
            if cli.ask_yn("Remove old host key and retry?"):
                cli.status(f"Removing old host key for {host} ...")
                runner.run(["ssh-keygen", "-R", host])
                rc, stderr = Deployer._ssh_cmd(
                    runner, remote, install_cmd, pub_key, accept_new=True)
                if rc != 0:
                    cli.fail(
                        "\nStill can't connect. Check host and credentials."
                    )
                    return False
            else:
                cli.msg(f"\nAborted. To fix manually:\n  ssh-keygen -R {host}")
                return False
        elif rc != 0 and Deployer._is_unknown_host(stderr):
            if not Deployer._handle_unknown_host(host, stderr):
                return False
            rc, stderr = Deployer._ssh_cmd(
                runner, remote, install_cmd, pub_key, accept_new=True)
            if rc != 0:
                cli.fail(
                    "\nSSH connection failed. Check host and credentials."
                )
                return False
        elif rc != 0:
            cli.fail(
                "\nSSH connection failed. Check host and credentials."
            )
            if stderr.strip():
                seen = set()  # type: set
                for line in stderr.strip().splitlines():
                    if line not in seen and not line.startswith("debug1:"):
                        seen.add(line)
                        cli.ssh_info(line)
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


_DESCRIPTION = (
    "Set up SSH key auth in one command.\n"
    "Generates a per-host Ed25519 key pair, deploys it\n"
    "to the remote host, and adds an entry to ~/.ssh/config."
)

_EPILOG = (
    "examples:\n"
    "  ssh-keyup"
    "                                       interactive mode\n"
    "  ssh-keyup --host 192.168.1.23 --user pi"
    "         with IP address\n"
    "  ssh-keyup --host rpi-5 --user trinity"
    "           with hostname\n"
    "  ssh-keyup --host rpi-5 --user pi --alias mypi"
    "   custom alias"
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
    p.add_argument("--host",
                   help="IP address or hostname of the remote device")
    p.add_argument("--user",
                   help="login username on the remote device")
    p.add_argument("--alias",
                   help="friendly name for ~/.ssh/config (default: hostname)")
    return p.parse_args()


def gather_input(args: argparse.Namespace) -> Tuple[str, str, str]:
    """Collect host, username, and alias from args or prompts."""
    host = cli.prompt("Remote host", args.host, hint="IP or name")
    if not host:
        cli.fatal("No host provided.")

    user = cli.prompt("Username", args.user)
    if not user:
        cli.fatal("No username provided.")

    if args.alias:
        alias = cli.prompt("Alias", args.alias)
    elif is_ip(host):
        alias = cli.prompt("Alias")
        if not alias:
            cli.fatal("No alias provided.")
    else:
        raw = host[:-6] if host.endswith(".local") else host
        alias = cli.prompt("Alias", default=sanitize_alias(raw, quiet=True))

    alias = sanitize_alias(alias)

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
        cli.fatal("ssh-keygen failed.")


def main() -> None:
    """Entry point: gather input, generate keys, deploy, update config."""
    try:
        cli.enable_ansi()
        args = parse_args()

        cli.banner()
        cli.separator()

        host, user, alias = gather_input(args)
        file_alias = alias.replace("-", "_")

        cli.separator()

        ssh_dir = Path.home() / ".ssh"
        ssh_config = ssh_dir / "config"
        config_base, overwriting = SSHConfig.check_existing(ssh_config, alias)

        runner = Runner()
        runner.check()
        ssh_dir.mkdir(parents=True, exist_ok=True)

        key_path = ssh_dir / f"id_ed25519_{file_alias}"
        pub_path = ssh_dir / f"id_ed25519_{file_alias}.pub"

        key_generated = False
        if pub_path.exists():
            cli.msg(f"Key pair exists {pub_path}")
            if cli.ask_yn("Regenerate key pair?"):
                key_path.unlink(missing_ok=True)
                pub_path.unlink()
                generate_key(runner, key_path, pub_path, file_alias)
                key_generated = True
        else:
            generate_key(runner, key_path, pub_path, file_alias)
            key_generated = True

        cli.separator()
        if not Deployer.deploy(runner, user, host, pub_path):
            if key_generated:
                cli.status("Cleaning up generated key pair...")
                key_path.unlink(missing_ok=True)
                pub_path.unlink(missing_ok=True)
            if overwriting:
                try:
                    SSHConfig.revert(ssh_config, config_base)
                except Exception as ex:
                    cli.fail(
                        f"SSH config cleanup failed: {ex}"
                    )
            sys.exit(1)

        try:
            SSHConfig.update(ssh_config, alias, host, user, file_alias,
                             config_base)
        except Exception as ex:
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
