# OpenSSF baseline policy

This repository follows the [OSPS Baseline](https://baseline.openssf.org/versions/2026-08-28)
version 2026.08.28. The policy covers shared workflows, composite actions,
queue reconciliation, release automation, and repository tests.

## Project scope and releases

nix-forge CI publishes reusable GitHub Actions workflows and small actions. It
has source releases in the v2.x series. A release is created from reviewed main
with an immutable unique tag and a change log. The release represents workflow
contracts and source actions, not compiled binary assets. The trusted builder
and verification contract are documented in [docs/slsa.md](slsa.md).

## Change and build controls

Every commit must carry a matching Signed-off-by trailer. The DCO file defines
the certificate and .github/workflows/dco.yml checks proposed non-merge commits
on pull requests and merge-group refs.

All workflows start with empty default permissions. Reusable workflows document
their caller permissions, jobs grant only the scopes they need, checkout does
not persist credentials, and actions use full commit SHAs. Pull requests and
merge groups run workflow validation, CodeQL, and the Python and Nix test
suites before protected main can advance. The dependency-review workflow in
this repository is a reusable caller contract. This repository has no
GitHub-supported dependency manifest for the action to compare, so its own Nix
lock and action references are checked by flake validation, Dependabot, the
workflow contract validator, and CodeQL instead.

The normal evidence set is:

    nix flake check --show-trace
    nix develop --command bash scripts/check.sh
    python3 -m unittest discover -s tests

Changes to a reusable workflow or action include a contract test and a
representative caller check. Inputs from callers are untrusted and are
validated or passed through quoted environment variables. Metadata jobs that
run on pull-request events do not check out or execute pull-request code.

## Release and dependency controls

Flake inputs, action pins, workflow contracts, and lockfiles are reviewed with
their security and compatibility impact. Caller repositories use the
dependency-review workflow to block new low-or-higher severity
vulnerabilities. This repository's Nix lock and pinned action surface are
checked by flake validation, Dependabot, and the workflow contract validator.
CodeQL and SCA findings must be fixed before release unless a reviewed
suppression records why the finding is not exploitable.

Each source release records the reviewed commit, unique tag, workflow contract
changes, public inputs and outputs, security impact, release actor and
workflow, source verification method, threat-model review, and support window.
Reusable builders publish portable SLSA provenance beside release artifacts;
the release process does not upload opaque compiled assets.

## Governance and vulnerability response

The maintainers listed in [GOVERNANCE.md](../GOVERNANCE.md) own repository
administration, Actions secrets, Pages, dependency policy, and releases.
Sensitive access is granted after review of the contributor's history and
intended responsibility. New maintainers receive the narrowest role needed.

Report vulnerabilities through [SECURITY.md](../SECURITY.md) or GitHub private
vulnerability reporting. The maintainer acknowledges reports within three
business days and provides an initial assessment within seven days. Public
disclosure follows a fix or documented mitigation. [security/vex.json](../security/vex.json)
records reviewed non-affectability statements. Support and end-of-life rules
are part of the release notes and [SUPPORT.md](../SUPPORT.md).

The operating procedures for [dependency management](dependency-management.md)
and [secret management](secret-management.md) are part of this policy. They
define the review, release-gate, storage, access, and rotation requirements
used to support the controls below.

## Control evidence

| Control area | Evidence |
| --- | --- |
| Least-privilege CI and trusted inputs | Empty default permissions, per-job scopes, pinned actions, contract validation, and metadata-only target workflows |
| Releases and change logs | scripts/sync-release.py, release tags, and release notes |
| Dependencies | flake.lock, action pins, Dependabot, the reusable dependency-review contract, CodeQL, and repository tests |
| Build and test instructions | [README.md](../README.md), [CONTRIBUTING.md](../CONTRIBUTING.md), and scripts/check.sh |
| Governance | [GOVERNANCE.md](../GOVERNANCE.md) |
| Contributor legal agreement | [DCO](../DCO) and .github/workflows/dco.yml |
| Security assessment | [THREAT_MODEL.md](../THREAT_MODEL.md) |
| Vulnerability response | [SECURITY.md](../SECURITY.md), private reporting, advisories, and [security/vex.json](../security/vex.json) |
| Public interfaces and release identity | Workflow inputs, outputs, action contracts, reviewed commits, and source tags |
| Support lifecycle | [SUPPORT.md](../SUPPORT.md) and release notes |

Review this policy when a reusable contract, permission, dependency, queue, or
release behavior changes.
