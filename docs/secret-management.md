# Secret management policy

This policy covers repository administration, reusable workflows, queue
automation, release automation, and local development in the ci repository.
The shared workflows must be safe for callers to adopt without exposing their
secrets to pull-request code.

## Storage and handling

Secrets belong in the caller's protected repository or environment secret
stores, or in a provider's secret manager. They must never be committed,
placed in the Nix store, passed as command-line arguments, copied into
artifacts, or printed in logs, tests, issues, or pull requests.

Pull-request and metadata-only workflows receive no repository secrets and do
not check out or execute untrusted pull-request code. A reusable workflow can
use only permissions and secrets explicitly supplied by its caller. Release
automation uses short-lived GitHub OIDC credentials where provider access is
needed; it does not require a long-lived cloud key.

## Access and review

Repository administrators review Actions secrets, environments, Pages, and
release access before granting it. Access begins at the narrowest role needed,
is individual rather than shared, and is removed when responsibility ends. A
new secret or permission requires a pull request documenting its purpose,
scope, workflow, and failure behavior without exposing its value.

## Rotation and incident response

Secrets are rotated at least annually and immediately when a maintainer,
provider, workflow trust boundary, or authorization scope changes. Suspected
exposure triggers revocation, replacement, log and artifact review, caller
notification where relevant, and a private vulnerability report or incident
record. Rotation must not publish an old or replacement value in repository
history.

`security/vex.json` contains only reviewed non-affectability statements. It
must never contain secret values or private caller details.

