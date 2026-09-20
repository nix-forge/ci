# Contributing

This repository contains shared GitHub Actions workflows and small actions for
the `nix-forge` repositories. Changes affect every caller, so keep contracts
explicit, preserve least privilege, and document any required caller change.

Before a larger change, open an issue describing the workflow or action contract,
the affected callers, and the migration plan. Keep pull requests focused. Add
or update regression coverage for queue behavior, workflow validation, action
inputs, and permission changes.

Run the checks documented in the README. At minimum, run `nix flake check` and
the repository's workflow validation checks. Validate a changed reusable
workflow against a representative caller before merging it.

## Testing policy

Pull requests and merge groups run workflow syntax and security validation,
CodeQL, the Nix checks, Python tests, and the reusable-workflow contract suite.
Run `nix develop --command bash scripts/check.sh` locally before requesting
review and run the representative caller checks for a shared contract change.

Every major change to a workflow, composite action, queue path, public input,
or release contract must add or update an automated regression test. If an
automated test is not practical, record the reason, manual evidence, and a
follow-up plan in the pull request.

Do not add mutable action references, broad default permissions, persisted
checkout credentials, or unreviewed secret access. Do not commit secrets or
large generated artifacts.

Commits should include a sign-off with `git commit -s`. This records agreement
to the [Developer Certificate of Origin](https://developercertificate.org/).

Submit changes as pull requests against `main`. The maintainer decides whether
the review is sufficient; passing checks and the merge queue are required.
