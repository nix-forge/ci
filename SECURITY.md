# Security policy

## Reporting a vulnerability

Do not open a public issue or pull request for a suspected vulnerability in a
workflow, action, release process, or dependency. Submit a report through
[GitHub private vulnerability reporting](https://github.com/nix-forge/ci/security/advisories/new).
If that form is unavailable, contact the maintainer through the private address
listed on the GitHub profile and request an encrypted channel.

Include the affected commit or release, workflow or action name, event type,
permissions or trust boundary involved, impact, and a minimal reproduction. Do
not include tokens, secrets, private paths, or unredacted event payloads.

We aim to acknowledge reports within 3 business days, provide an initial
assessment within 7 business days, and agree coordinated disclosure timing with
the reporter. Good-faith research that avoids privacy violations, persistence,
destructive actions, and third-party systems is welcome.

## Scope

The security boundary includes reusable workflows, composite actions, release
automation, action pinning, token permissions, untrusted pull-request handling,
and data passed between caller and called workflows. A caller's local
configuration remains the caller's responsibility.
