# Support

Start with the [README](README.md) and the contract documented for the workflow
or action you call. Check the caller's permissions, pinned revision, required
inputs, and event type before opening an issue.

For a reproducible workflow or action problem, [open an issue](https://github.com/nix-forge/ci/issues/new).
Include the called workflow or action, immutable revision, caller repository,
event name, relevant inputs, and the smallest useful log excerpt. Remove tokens,
secrets, private paths, and unredacted event payloads before posting.

Use [SECURITY.md](SECURITY.md) for suspected vulnerabilities. Do not disclose
security-sensitive details in a public issue.

This project does not provide private CI administration or support for
unreviewed caller changes. A minimal workflow reproducer is the most useful
support request.

## Release lifecycle

The current `v2.x` release line is supported for shared workflow and action
contracts. The latest minor release receives normal bug and security fixes; a
minor release remains supported until two later minor releases have shipped or
12 months have elapsed since its publication, whichever is later. Critical
security fixes may require upgrading to the latest supported minor release.

The historical `v1.x` line is end-of-life and does not receive new security
updates. The release notes record any exception, migration requirement, or
earlier end-of-life decision for an individual release.
