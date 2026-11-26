# Security policy

## Supported versions

Only latest release on [PyPI](https://pypi.org/project/ssh-keyup/) gets security fixes.

## Reporting a vulnerability

Report privately through [GitHub vulnerability reporting](https://github.com/Kurokesu/ssh-keyup/security/advisories/new). Do not open a public issue.

Include ssh-keyup version, local OS, target device and steps to reproduce. Reports get a reply within 7 days. Confirmed issues are fixed in a patch release and published as a GitHub security advisory.

## Scope

In scope:

- Private key leaving local machine or written with loose permissions
- Password or key material exposed in output or temporary files
- Host key accepted without confirmation
- Unintended commands run locally or on target device
- `~/.ssh/config` or `authorized_keys` left corrupted

Out of scope:

- Bugs in OpenSSH or device firmware
- Attacks that need an already compromised local account
