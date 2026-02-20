# ssh-keyup

**SSH key authentication for a new device -- in one command.**

Setting up passwordless SSH on a freshly flashed Raspberry Pi (or Jetson, or any Linux device) means juggling `ssh-keygen`, `ssh-copy-id` (which doesn't exist on Windows), and manually editing `~/.ssh/config`. `ssh-keyup` does all three in a single interactive session:

1. Generates a **per-host named key pair** (`~/.ssh/id_rsa_mydevice`)
2. Copies the public key to the remote host and installs it into `authorized_keys`
3. Adds a **named entry** to `~/.ssh/config` so you can simply `ssh mydevice`

No dependencies beyond Python and the standard OpenSSH tools that ship with your OS.

## Quick start

Interactive -- just run it and follow the prompts:

```
python ssh-keyup.py
```

```
  ssh-keyup v1.0.0
  Sets up SSH key authentication so you can connect
  to a remote device without typing a password.
  ──────────────────────────────────────────

  Host (IP or hostname) .. 192.168.1.42
  User (login name) ..... pi
  Alias (friendly name) .. mypi
  ──────────────────────────────────────────

  Press Enter to continue (Ctrl+C to cancel)

  OK Key pair generated

  [1/3] Preparing remote ~/.ssh ...
  OK Remote ~/.ssh ready
  [2/3] Uploading public key ...
  OK Public key uploaded
  [3/3] Installing into authorized_keys ...
  OK Key installed
  OK Config updated ~/.ssh/config

  ──────────────────────────────────────────
  Done! Connect with: ssh mypi
```

Or pass everything on the command line:

```
python ssh-keyup.py --host 192.168.1.42 --user pi --alias mypi -y
```

That's it. From now on, `ssh mypi` connects without a password.

## Features

- **Host key conflict recovery** -- reflashed your device? The script detects the mismatch and offers to fix it
- **Per-host named keys** -- each device gets its own key file, no collisions
- **Rerun safe** -- existing keys are reused, won't overwrite anything
- **Works non-interactively** -- pass `--host`, `--user`, `--alias`, `-y` for scripted use
- **Zero dependencies** -- Python 3.6+ stdlib only

## Prerequisites

| Requirement | Details |
|---|---|
| Python | 3.6 or newer |
| SSH tools | `ssh`, `ssh-keygen`, `scp` in PATH |

**Windows 10/11** -- OpenSSH Client is included (Settings > Optional Features) or comes with Git for Windows.

**Ubuntu / Debian** -- OpenSSH is pre-installed. If not: `sudo apt install openssh-client`.

## Works with

Any Linux device you can reach over SSH:

- Raspberry Pi (all models)
- NVIDIA Jetson
- Orange Pi, Banana Pi, other SBCs
- Any Linux server or VM

## How it works

```
Your machine                          Remote device
───────────                           ─────────────
1. ssh-keygen
   id_rsa_mypi + id_rsa_mypi.pub
                    ──scp──►
2.                                    .ssh/authorized_keys ← pub key
3. ~/.ssh/config ← Host mypi entry
```

- Keys are named per host (`id_rsa_<alias>`), so multiple devices get separate keys.
- Existing keys are reused -- safe to re-run.
- The `~/.ssh/config` entry is appended with a date comment for easy housekeeping.
