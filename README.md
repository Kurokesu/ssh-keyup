# ssh-keyup

![License](https://img.shields.io/badge/license-GPL--3.0-blue)
![Python](https://img.shields.io/badge/python-3.8%2B-brightgreen)

Set up passwordless SSH on Raspberry Pi, NVIDIA Jetson, or any Linux device — in one command.

Tired of juggling `ssh-keygen`, `ssh-copy-id` (missing on Windows), and `~/.ssh/config` edits every time you flash a new device? `ssh-keyup` handles all three in a single interactive session.

## Quickstart

```bash
git clone https://github.com/Kurokesu/ssh-keyup.git
cd ssh-keyup
python ssh-keyup.py
```

Follow the prompts, enter the remote password once, and you're done:

```bash
ssh mypi   # no password, ever again
```

You can also skip the prompts entirely:

```bash
python ssh-keyup.py --host 192.168.1.42 --user pi --alias mypi
```

## Features

- Generates a per-host **Ed25519** key pair (`~/.ssh/id_ed25519_<alias>`)
- Deploys the public key in a **single SSH session** — only one password prompt
- Adds a named entry to `~/.ssh/config` so you can simply `ssh <alias>`
- Detects and recovers from **host key mismatches** (common after reflashing)
- Safe to re-run: existing keys are reused or regenerated, duplicate config entries are detected
- Works with any device you can reach over SSH: Raspberry Pi, NVIDIA Jetson, Orange Pi, VMs, servers
- **Zero dependencies** — Python 3.8+ standard library only

## Prerequisites

**Python 3.8+** and OpenSSH tools (`ssh`, `ssh-keygen`) must be in PATH.

- **Windows 10/11**: Install Python from [python.org](https://www.python.org/downloads/) or the Microsoft Store. OpenSSH Client is included via Settings > Optional Features, or ships with [Git for Windows](https://gitforwindows.org).
- **Linux**: `sudo apt install python3 openssh-client` (usually pre-installed).
