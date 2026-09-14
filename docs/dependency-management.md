# Dependency management policy

This policy applies to reusable workflows, composite actions, queue automation,
Nix inputs, GitHub Action references, and the Python and Nix test tooling in
the ci repository. Dependency updates can change the permissions and trust
boundary inherited by every caller, so they receive security review.

## Inventory and provenance

The authoritative dependency records are `flake.lock`, Python dependency
metadata, workflow and action references, and release scripts. Action references
are immutable commit SHAs. This repository has no GitHub-supported dependency
manifest for the dependency-review action to compare, so the reusable
dependency-review workflow is tested as a caller contract; the repository's
own Nix lock and action surface are checked by flake validation, Dependabot,
the workflow contract validator, CodeQL, and repository tests.

## Selection and review

Maintainers review upstream provenance, maintenance status, security
advisories, licensing, compatibility, and permission changes. Updates to a
reusable workflow identify affected callers and include a representative
contract test. Inputs from callers are treated as untrusted and are validated
or passed through quoted environment variables. Lockfiles and generated
release metadata are updated together with their declarations.

## Release gate and exceptions

Before a source release, applicable dependency, CodeQL, lock-health, contract,
and test checks must pass. A high- or critical-severity finding, an unreviewed
license problem, a failed provenance check, or an undocumented permission
expansion blocks release. The only exception is a reviewed, time-bounded
pull-request record that names the component, explains why it is not
exploitable here, assigns an owner, and gives a remediation date.
`security/vex.json` records reviewed non-affectability statements in OpenVEX
form; it does not waive an affectable finding.

## Update and rollback

Updates are exercised against the repository's contract tests and a
representative caller. A regression is rolled back by reverting the lockfile,
action pin, or workflow change, then tracked with a follow-up issue. Emergency
security updates use the smallest safe change and receive normal review
retrospectively if immediate action is required.

