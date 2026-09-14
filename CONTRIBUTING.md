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

Do not add mutable action references, broad default permissions, persisted
checkout credentials, or unreviewed secret access. Do not commit secrets or
large generated artifacts.

Commits should include a sign-off with `git commit -s`. This records agreement
to the [Developer Certificate of Origin](https://developercertificate.org/).

Submit changes as pull requests against `main`. The protected branch requires
review, passing checks, and the merge queue before changes are accepted.
