# Release process

The CI library uses immutable semantic-version tags in the v2.x series. A
release starts from a reviewed commit on main after the pull request has
passed workflow validation, dependency review, CodeQL, the DCO check, and the
repository test suite.

## Candidate checklist

1. Update the release notes with workflow and action contract changes,
   migration impact, security changes, and the exact checks that ran.
2. Run nix flake check, scripts/check.sh, and the complete Python test suite.
3. Run a representative caller repository against the candidate commit.
4. Run scripts/sync-release.py in check mode for all known callers before
   publishing a new shared release.
5. Tag the exact reviewed commit. Do not tag a dirty working tree.
6. Verify the tag, source commit, generated manifest, checksums, and GitHub
   build attestation before announcing the release.

The release workflow creates a source archive, a SHA-256 manifest, and an
OIDC-backed build attestation. It does not publish opaque compiled assets.
Release notes name the actor and workflow, list public inputs and outputs,
describe the security assessment, and state the support and end-of-life
window. A release stops receiving security updates when its support window
ends or when the next major contract removes it from the supported matrix.

## Compatibility

Reusable workflow inputs and outputs are public APIs. A breaking contract
change requires a migration note, updated contract tests, and caller updates
before the release tag is created.
