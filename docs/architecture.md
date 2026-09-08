# Shared CI design and validation

Reviewed September 8, 2026 across all seven nix-forge repositories.

## Ownership

The CI library owns common workflow policy, queue admission and reconciliation,
Nix setup, repository validation and contract tests. The organization `.github`
repository owns short onboarding templates. Source repositories own package
selection, native checks, assurance, releases and deployment policy. Templates are
copied at onboarding; shared SHA references carry later library updates through
reviewed Dependabot PRs. [GitHub reuse documentation](https://docs.github.com/en/actions/concepts/workflows-and-actions/reusing-workflow-configurations).

Use reusable workflows for complete shared jobs, and composite actions for steps
inside existing jobs. Composites preserve required job names during this migration.
Permissions cannot increase inside a reusable workflow. Keep Scorecard local because
publication constrains its steps and OIDC context. Swift CodeQL also needs its
repository-specific native build. [Workflow reuse reference](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations),
[Scorecard restrictions](https://github.com/ossf/scorecard-action#workflow-restrictions).

## Corrections

The previous version had two admission policies, several shared release pins per
consumer and repeated hook/scanner setup. Some history scanners used shallow
checkouts. The organization template validator checked syntax and JSON parsing
without the shared security checks or metadata contracts.

Version 2 consolidates admission in the queue reconciler and validates both paths
of renames. Repository checks use full history and the current committed tree,
retaining repository-owned scanner exceptions and stricter publication scripts.
A historical baseline does not exempt a surviving current file. Shared validation
checks real consumer workflows and paired template metadata. Negative regression
fixtures cover these failure modes, including real Git and Gitleaks canaries.

## Trust and queue behavior

Keep PR builds read-only. Privileged metadata jobs execute the pinned shared action
without checking out PR code or consuming build artifacts. Use per-job permissions,
no inherited secret sets and no persisted checkout credentials. Workflow and
shared action changes need maintainer admission. Fork code must never receive an
App key or a write token. [Privileged trigger guidance](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target).

Required workflows run for merge groups and retain their emitted check names.
Do not put path filters on required workflows. Cancel superseded PR runs, while
allowing each queue ref's validation to finish. Callback dependencies include every
validation job. Missing, stale, skipped and failed evidence cannot become a passing
queue status. [Required-check troubleshooting](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks),
[merge queue configuration](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue).

Retain the exact-SHA dispatch adapter for GITHUB_TOKEN admission. Most resulting
events do not trigger new workflows, while explicit dispatch is supported. Replacing
this adapter requires an installed App and passing tests for native admission,
changed heads and failed or cancelled groups. Adding an untested credential would
not establish correct queue behavior. [Trigger rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).

## Performance evidence

The two most recent successful CI runs sampled before implementation had these
job durations. They are observations of different workloads, not controlled
before/after benchmarks or forecasts.

| Job | Observed seconds |
| --- | ---: |
| Root repository hooks | 530–600 |
| Root native x86 checks | 5410–6213 |
| Package lint and security | 61–65 |
| Package native x86 builds | 4931–5211 |
| Framework repository hooks | 38–42 |
| Shared CI validation | 24–32 |
| Community validation | 28–33 |

Native package builds dominate elapsed time. Preserve derivation comparison and
conservative fallback when base evaluation is unavailable. The updater must pass
its actual base SHA to dispatched package CI; omitting it unnecessarily selects a
full build. Hook and scanner execution now reuse one repository Nix shell. Removing
the five duplicate admission workflows also removes their per-PR runner launches.
No numeric end-to-end speedup is claimed until comparable hosted runs exist.

Do not broadly enable store archives. Forks can read eligible base-branch caches;
archives can include outputs whose substitution or redistribution is restricted.
The 2 GiB garbage-collection target is not a hard quota when roots remain live.
The existing public framework pilot should compare cold and warm total job time,
restore/save durations and stored bytes before enabling a cache. Repository-scoped
GitHub caches do not share builds across repositories. A signed cross-repository
binary cache needs a separate measured justification and trusted publication policy.
[GitHub cache restrictions](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching),
[cache action inputs](https://github.com/nix-community/cache-nix-action/blob/v7/action.yml),
[Nix binary-cache trust](https://nix.dev/guides/recipes/add-binary-cache.html).

The pinned Determinate action already defaults its installer source tag to its
release. Preserve that distribution during consolidation. New GitHub concurrency
syntax can exceed the pinned linter's support; ordinary ref-scoped concurrency is
sufficient here. [Pinned installer metadata](https://github.com/DeterminateSystems/determinate-nix-action/blob/v3.22.3/action.yml),
[concurrency semantics](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
