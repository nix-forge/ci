# nix-forge CI

Shared workflows and small actions for nix-forge repositories. Package and
platform build definitions remain in their source repositories.

## Shared workflows

| Workflow | Caller permissions | Contract |
| --- | --- | --- |
| `flake-lock.yml` | `contents: read` | Required `lockfile` input; fails on missing or unhealthy lockfiles |
| `codeql.yml` | `contents: read`, `security-events: write` | `languages` input; analysis using build-mode none |
| `dependency-review.yml` | `contents: read` | Fail on new vulnerabilities at low severity or higher; optional `base-ref` and `head-ref` for dispatch |
| `request-review.yml` | `pull-requests: write` | Metadata-only `pull_request_target`; human PRs excluding the maintainer |
| `nur.yml` | `contents: read` | Caller supplies `scripts/check-nur.py` and `tests/nur-supported.nix` |

Call reusable workflows at the job level using a full commit SHA. Callers retain
events, concurrency and repository-specific inputs. Permissions cannot increase
inside a called workflow. No shared workflow inherits all caller secrets.

```yaml
jobs:
  lock:
    permissions:
      contents: read
    uses: nix-forge/ci/.github/workflows/flake-lock.yml@RELEASE_COMMIT_SHA
    with:
      lockfile: flake.lock
```

Replace `RELEASE_COMMIT_SHA` with the full commit listed in a tested release.
CodeQL builds requiring custom commands, such as Swift, stay in the caller.
Scorecard publication also stays local because its publishing API restricts the
workflow's steps and OIDC context.

## Actions

`actions/repository-checks` materializes the caller's partitions, then enters its
Nix shell once to run hooks and publication checks. Check out full history with
`fetch-depth: 0`. The default scan checks history and an archive of the complete
committed tree. An optional historical baseline never suppresses current-tree
findings. Repositories can supply a publication script for stricter local policy.
These are read-only build steps; do not use them in privileged metadata jobs.

`actions/validate-workflows` runs the library's pinned actionlint, Zizmor,
Yamllint and consumer contract validator against the caller's workflows, composite
actions and onboarding templates. It rejects mutable external references, mixed
shared releases, persisted checkout credentials, shallow history scans, missing
queue triggers and callbacks, missing timeouts, broad default permissions and
incomplete template metadata. Set up Nix before calling either action.

`actions/setup-nix` installs the tested Determinate Nix version. Its optional
`cache: 'true'` uses GitHub's repository-scoped cache. Give different build jobs
different `cache-scope` values. Cache keys include platform, Nix version, lockfiles
and commit; restore prefixes remain scoped to matching lockfiles. The action only
saves on trusted default-branch pushes or dispatches, attempts to reduce the store
to 2 GiB before saving, and never requests cache-purge permissions. GC cannot remove
live roots, so the size target is not a hard quota. Measure restore and upload time
before enabling it broadly. The cache is disabled by default.

This action archives the Nix store. Enable it only for workloads whose entire
store can be shared with contributors. Fork pull requests can read base-branch
caches ([GitHub cache access rules](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#restrictions-for-accessing-a-cache)).
Nix's `allowSubstitutes = false` controls substitution; it does not exclude a path
from this archive. Keep jobs producing secrets or redistribution-restricted
outputs uncached.

`actions/reconcile-queue` executes the packaged script from this repository,
without checking out caller code. Supply the JSON `workflows` list and optional
`source-run-id`; the workflow grants checks read and actions, contents,
pull-requests and statuses write. It preserves commit/run-attempt checks and never
mirrors skipped validation as success. `actions/queue-completion` notifies this
trusted reconciler from dispatched queue runs. Retain the caller's existing
`Queue completion callback` name and event guard.

After observing a queued bot PR, reconciliation waits up to 55 seconds for the
live front entry's validation ref. GitHub can acknowledge admission before that
ref exists; old refs do not satisfy readiness for a different queued commit.
Empty queues do not wait. Keep scheduled reconciliation as a backup for longer
GitHub delays; exhaustion emits a warning and never invents passing checks.

Completed failures, timeouts and cancellations from the latest dispatch attempt
are reported as failures, so they release the queue instead of blocking later
PRs until timeout. Other workflows need not finish before reporting a verified
failure. Passing results still require the complete configured workflow set to
succeed. Missing, skipped and stale evidence never becomes a pass.

The queue fallback remains necessary with current `GITHUB_TOKEN` admission.
Replacing it requires an installed GitHub App and proof that app-authenticated
admission produces native merge-group checks. No app key is required for this
release. Do not provide privileged tokens to PR build steps.

## Development and releases

```sh
nix develop --command bash scripts/check.sh
python3 -m unittest discover -s tests -v
```

Validation covers actionlint, pedantic Zizmor, YAML, Ruff and the consolidated
queue regression cases. Consumer CI must also pass on PR, merge-group and manual
dispatch events. A called workflow can change check names; inspect GitHub's actual
checks and update required-check configuration together with each migration.

Publish semver releases after validation. Consumers pin the release commit and
Dependabot proposes updates. Shared privileged-automation changes require review
even when labelled as patch updates. Roll back a consumer by restoring its prior
SHA. Action pins do not automatically pin every downloaded runtime dependency.

The source is MIT licensed. Third-party actions and Nix packages retain their own
licenses. The library neither builds the nix-conf desktop closure on hosted
runners nor publishes package outputs to an external binary cache.

## Version 2 migration

Version 2 removes `automerge.yml`. Remove its consumer wrapper and its name from
reconciler workflow-run triggers. The existing reconciler owns automatic admission
after required checks pass. It requires human admission for `.github/`, `actions/`,
`scripts/` and `workflow-templates/` changes, including rename source paths. This
leaves one admission policy and avoids a redundant privileged workflow per bot PR.
Existing immutable version 1 references still resolve to their original contents.

Update every shared reference in one consumer to the same tested release commit.
Composite actions preserve existing required job names. Preserve specialized
builds and Scorecard publication, and validate both PR and merge-group events.
See [the research and measurements](docs/architecture.md) for the decisions and
limits of the performance evidence.
