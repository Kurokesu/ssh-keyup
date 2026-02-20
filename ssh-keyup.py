#!/usr/bin/env python3
"""ssh-keyup: SSH key auth for a new device in one command."""

__version__ = "1.0.0"

import argparse
import ipaddress
import shlex
import subprocess
import sys
from datetime import date
from pathlib import Path
from shutil import which
from typing import List, Optional, Tuple, Union


# ── ANSI formatting (zero dependencies) ─────────────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"

if not sys.stdout.isatty():
    BOLD = DIM = RESET = GREEN = RED = YELLOW = ""


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


def ok(msg: str) -> None:
    print(f"  {GREEN}OK{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}!!{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}FAIL{RESET} {msg}")


HIDE_CUR = "\033[?25l"
SHOW_CUR = "\033[?25h"

if not sys.stdout.isatty():
    HIDE_CUR = SHOW_CUR = ""


def _read_key() -> str:
    """Read a single keypress. Returns 'left', 'right', 'enter', 'esc', or char."""
    if sys.platform == "win32":
        import msvcrt
        ch = msvcrt.getwch()
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
    """Interactive yes/no selector with arrow keys. Falls back to text for non-TTY."""
    if not sys.stdin.isatty():
        return default

    sel = 0 if default else 1

    def _render() -> str:
        yes = f"{GREEN}{BOLD}[ Yes ]{RESET}" if sel == 0 else f"{DIM}  Yes  {RESET}"
        no = f"{RED}{BOLD}[ No ]{RESET}" if sel == 1 else f"{DIM}  No  {RESET}"
        return f"\r\033[2K  {prompt}  {yes}  {no}"

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


# ── SSH runner ───────────────────────────────────────────────────

def _find_git_bash() -> Optional[str]:
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
    """Runs SSH/SCP commands natively or via Git Bash as fallback."""

    def __init__(self) -> None:
        self.git_bash = _find_git_bash()
        openssh = all(which(c) for c in ("ssh", "ssh-keygen", "scp"))
        self.mode: Optional[str] = "native" if openssh else ("gitbash" if self.git_bash else None)

    def check(self) -> None:
        if self.mode:
            return
        fail("No SSH tools found.")
        if sys.platform == "win32":
            print("    Install OpenSSH Client (Settings > Optional Features)")
            print("    or Git for Windows (https://gitforwindows.org)")
        else:
            print("    sudo apt install openssh-client   # Debian/Ubuntu")
        sys.exit(1)

    def run(self, cmd: Union[List[str], str]) -> int:
        if self.mode == "native":
            return subprocess.run(cmd if isinstance(cmd, list) else cmd, shell=isinstance(cmd, str)).returncode
        assert self.git_bash
        sh = cmd if isinstance(cmd, str) else " ".join(shlex.quote(a) for a in cmd)
        return subprocess.run([self.git_bash, "-c", sh]).returncode

    def run_capture(self, cmd: Union[List[str], str]) -> Tuple[int, str]:
        """Like run(), but captures stderr and returns (exit_code, stderr_text)."""
        if self.mode == "native":
            r = subprocess.run(cmd if isinstance(cmd, list) else cmd,
                               shell=isinstance(cmd, str), stderr=subprocess.PIPE)
        else:
            assert self.git_bash
            sh = cmd if isinstance(cmd, str) else " ".join(shlex.quote(a) for a in cmd)
            r = subprocess.run([self.git_bash, "-c", sh], stderr=subprocess.PIPE)
        return r.returncode, (r.stderr or b"").decode(errors="replace")


# ── Helpers ──────────────────────────────────────────────────────

def sanitize_alias(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name) or "host"


def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def is_host_key_error(stderr: str) -> bool:
    return ("REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr
            or "Host key verification failed" in stderr)


def step(n: int, total: int, msg: str) -> None:
    print(f"  [{n}/{total}] {msg}")


# ── CLI ──────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="ssh-keyup",
        description="SSH key auth for a new device in one command.\n"
                    "Generates a per-host key pair, copies it to the remote host,\n"
                    "and adds an entry to ~/.ssh/config.",
        epilog="Works with Raspberry Pi, NVIDIA Jetson, and any Linux device.\n"
               "https://github.com/Kurokesu/ssh-keyup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--host", help="IP address or hostname of the remote device")
    p.add_argument("--user", help="login username on the remote device")
    p.add_argument("--alias", help="friendly name for ~/.ssh/config (default: hostname)")
    p.add_argument("-y", "--yes", action="store_true", help="skip initial confirmation")
    return p.parse_args()


# ── Main ─────────────────────────────────────────────────────────

def main() -> None:
    _enable_ansi()
    args = parse_args()

    print(f"\n  {BOLD}ssh-keyup{RESET} {DIM}v{__version__}{RESET}")
    print(f"  Sets up SSH key authentication so you can connect")
    print(f"  to a remote device without typing a password.")
    print(f"  {DIM}──────────────────────────────────────────{RESET}\n")

    host = args.host or input(f"  Host {DIM}(IP or hostname){RESET} .. ").strip()
    if not host:
        sys.exit(f"  {RED}No host provided.{RESET}")

    user = args.user or input(f"  User {DIM}(login name){RESET} ..... ").strip()
    if not user:
        sys.exit(f"  {RED}No username provided.{RESET}")

    if args.alias:
        alias_in = args.alias
    elif is_ip(host):
        alias_in = input(f"  Alias {DIM}(friendly name){RESET} .. ").strip()
        if not alias_in:
            sys.exit(f"  {RED}No alias provided.{RESET}")
    else:
        alias_in = host
        print(f"  Alias ................. {alias_in} {DIM}(from hostname){RESET}")

    alias = sanitize_alias(alias_in)
    if alias != alias_in:
        print(f"            {DIM}sanitized to: {alias}{RESET}")
    file_alias = alias.replace("-", "_")

    print(f"  {DIM}──────────────────────────────────────────{RESET}")

    if not args.yes:
        print()
        input(f"  Press Enter to continue (Ctrl+C to cancel) ")

    print()

    runner = Runner()
    runner.check()

    ssh_dir = Path.home() / ".ssh"
    key_path = ssh_dir / f"id_rsa_{file_alias}"
    pub_path = ssh_dir / f"id_rsa_{file_alias}.pub"
    ssh_config = ssh_dir / "config"
    ssh_dir.mkdir(parents=True, exist_ok=True)

    # ── Key generation ───────────────────────────────────────────
    if pub_path.exists():
        ok(f"Key pair exists {DIM}{pub_path}{RESET}")
    else:
        print(f"  Generating key pair: {DIM}{key_path}{RESET}")
        if runner.mode == "native":
            rc = runner.run(["ssh-keygen", "-t", "rsa", "-b", "4096", "-N", "", "-f", str(key_path)])
        else:
            rc = runner.run(f"ssh-keygen -t rsa -b 4096 -N '' -f ~/.ssh/id_rsa_{file_alias}")
        if rc != 0:
            fail("ssh-keygen failed.")
            sys.exit(1)
        ok("Key pair generated")

    remote = f"{user}@{host}"
    temp_remote = f"~/.ssh/id_rsa_{file_alias}.pub.tmp"

    print(f"\n  {DIM}You may be prompted for the password (up to 3 times).{RESET}\n")

    # ── Step 1: prepare remote ~/.ssh ────────────────────────────
    step(1, 3, "Preparing remote ~/.ssh ...")
    rc, stderr = runner.run_capture(["ssh", remote, "mkdir -p ~/.ssh && chmod 700 ~/.ssh"])
    if rc != 0 and is_host_key_error(stderr):
        fail("Host key mismatch.")
        print()
        warn("This is common after reflashing a device.")
        warn("The old host key in known_hosts no longer matches.")
        print()
        if ask_yn("Remove old host key and retry?", default=False):
            print()
            runner.run(["ssh-keygen", "-R", host])
            print()
            step(1, 3, "Retrying ...")
            rc = runner.run(["ssh", remote, "mkdir -p ~/.ssh && chmod 700 ~/.ssh"])
            if rc != 0:
                fail("Still can't connect. Check IP/hostname and credentials.")
                sys.exit(1)
        else:
            print()
            print("  Aborted. To fix manually:")
            print(f"    ssh-keygen -R {host}")
            sys.exit(0)
    elif rc != 0:
        fail("SSH connection failed. Check IP/hostname and credentials.")
        if stderr.strip():
            for line in stderr.strip().splitlines()[:3]:
                print(f"    {DIM}{line}{RESET}")
        sys.exit(1)
    ok("Remote ~/.ssh ready")

    # ── Step 2: upload public key ────────────────────────────────
    step(2, 3, "Uploading public key ...")
    if runner.mode == "native":
        rc = runner.run(["scp", str(pub_path), f"{remote}:{temp_remote}"])
    else:
        rc = runner.run(f"scp {shlex.quote(str(pub_path))} {shlex.quote(f'{remote}:{temp_remote}')}")
    if rc != 0:
        fail("Failed to upload public key.")
        sys.exit(1)
    ok("Public key uploaded")

    # ── Step 3: install into authorized_keys ─────────────────────
    step(3, 3, "Installing into authorized_keys ...")
    rc = runner.run([
        "ssh", remote,
        f"cat {temp_remote} >> ~/.ssh/authorized_keys && "
        f"chmod 600 ~/.ssh/authorized_keys && "
        f"rm -f {temp_remote}",
    ])
    if rc != 0:
        fail("Failed to install key on remote host.")
        sys.exit(1)
    ok("Key installed")

    # ── Update local SSH config ──────────────────────────────────
    try:
        ssh_config.parent.mkdir(parents=True, exist_ok=True)
        with ssh_config.open("a", encoding="utf-8", newline="\n") as f:
            f.write("\n")
            f.write(f"#Begin-{alias} {date.today().isoformat()}\n")
            f.write(f"Host {alias}\n")
            f.write(f"    HostName {host}\n")
            f.write(f"    User {user}\n")
            f.write(f"    IdentityFile ~/.ssh/id_rsa_{file_alias}\n")
            f.write(f"#End-{alias}\n")
            f.write("\n")
    except Exception as ex:
        fail(f"Key installed, but SSH config update failed: {ex}")
        sys.exit(1)
    ok(f"Config updated {DIM}{ssh_config}{RESET}")

    print(f"\n  {DIM}──────────────────────────────────────────{RESET}")
    print(f"  {GREEN}Done!{RESET} Connect with: {BOLD}ssh {alias}{RESET}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.stdout.write(SHOW_CUR)
        sys.stdout.flush()
        print("\n  Cancelled.")
        sys.exit(130)
