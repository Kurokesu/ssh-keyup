# ssh-keyup

![License](https://img.shields.io/badge/license-GPL--3.0-blue)
![Python](https://img.shields.io/badge/python-3.8%2B-brightgreen)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey)

Set up passwordless SSH on Raspberry Pi, NVIDIA Jetson, or any Linux device — in one command.

Tired of juggling `ssh-keygen`, `ssh-copy-id` (missing on Windows), and `~/.ssh/config` edits every time you set up a new device? `ssh-keyup` handles all three in a single interactive session.

![ssh-keyup demo](demo.gif)

## Quickstart

Install globally with pip:

```bash
pip install git+https://github.com/Kurokesu/ssh-keyup.git

ssh-keyup   # ready to use anywhere
```

Or run directly without installing:

```bash
git clone https://github.com/Kurokesu/ssh-keyup.git
cd ssh-keyup
python ssh_keyup.py   # Windows
python3 ssh_keyup.py  # Linux
```

Follow the prompts, enter the remote password once, and you're done.

SSH via command:

```bash
ssh mypi   # no password, ever again
```

Or open [VSCode Remote - SSH](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-ssh) — your `~/.ssh/config` is already set up. Hit `Ctrl+Shift+P`, select **Remote-SSH: Connect to Host**, pick your alias, and get a full IDE on your remote device — no password:

![VSCode Remote SSH](demo-vscode.gif)

You can also skip the remote host prompts:

```bash
ssh-keyup --host 192.168.1.23 --user pi --alias mypi
```

Or configure a whole fleet:

```bash
for host in 192.168.1.10 192.168.1.11 192.168.1.12; do
  ssh-keyup --host $host
done
```

## Why?

```bash
# without ssh-keyup (you do this for every new device)
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519_rpi5
scp ~/.ssh/id_ed25519_rpi5.pub trinity@192.168.1.23:~/
ssh trinity@192.168.1.23 "mkdir -p ~/.ssh && chmod 700 ~/.ssh"
ssh trinity@192.168.1.23 "cat ~/id_ed25519_rpi5.pub >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && rm ~/id_ed25519_rpi5.pub"
nano ~/.ssh/config # add Host entry manually
...

# with ssh-keyup
ssh-keyup
```

## Prerequisites

**Python 3.8+** and OpenSSH tools (`ssh`, `ssh-keygen`) must be in PATH.

- **Windows 10/11**: Install Python from [python.org](https://www.python.org/downloads/) or the Microsoft Store. OpenSSH Client is included via Settings > Optional Features, or ships with [Git for Windows](https://gitforwindows.org).
- **Linux**: `sudo apt install python3 openssh-client` (usually pre-installed).

## Features

- Generates a per-host **Ed25519** key pair (`~/.ssh/id_ed25519_<alias>`)
- Deploys the public key to remote host in a **single SSH session** — only one password prompt
- **Never touches your password** — `ssh-keyup` only pipes the public key; **SSH** handles password authentication directly through its own terminal
- Adds a named entry to `~/.ssh/config` — works instantly with `ssh <alias>` and VSCode Remote SSH
- Detects and recovers from **host key mismatches** (common after reflashing)
- Handles re-runs gracefully: reuses existing keys or offers regeneration, detects duplicate config entries
- Works with any device you can reach over SSH: Raspberry Pi, NVIDIA Jetson, Orange Pi, VMs, servers
- **Zero dependencies** — Python 3.8+ standard library only
- Installs via `pip` or runs as a single script
