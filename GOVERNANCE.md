# Governance

`nix-forge/ci` is a maintainer-led open source project. The current maintainer
is [@IanHollow](https://github.com/IanHollow).

The repository publishes shared CI contracts. Changes should favor explicit
inputs, immutable dependencies, least-privilege permissions, safe behavior for
untrusted pull requests, and a documented migration path for callers.

Issues and pull requests are the public record for technical decisions. The
protected `main` branch, required checks, review rules, and merge queue apply to
all accepted changes.

The maintainer makes release and compatibility decisions. New maintainers may
be invited after sustained, constructive contributions and agreement on the
project's security and support expectations.

Report security issues through [SECURITY.md](SECURITY.md), not through public
issues or pull requests.
