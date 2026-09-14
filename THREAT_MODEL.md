# Threat model

## Scope

This model covers reusable workflows, composite actions, queue reconciliation,
workflow contracts, tests, documentation, and release automation. It does not
cover the caller repository's own code or secrets.

## Assets and actors

Assets include caller workflow inputs, GITHUB_TOKEN permissions, queue state,
release tags, action code, and repository administration. Pull requests and
workflow callers are untrusted unless the specific contract says otherwise.
Maintainers control releases and sensitive repository settings. Metadata-only
pull_request_target jobs may inspect event metadata but must not execute
pull-request code.

## Trust boundaries

The caller repository, reusable workflow, runner, GitHub API, and release
process are separate boundaries. A reusable workflow cannot grant a caller
more permissions than the caller provides. Queue reconciliation is trusted
default-branch automation and does not check out pull-request code.

## Main threats and controls

| Threat | Control |
| --- | --- |
| A caller gives an action an unsafe input | Contract tests, input validation, quoted environment variables, and pinned references |
| A pull request obtains write access through a reusable workflow | Empty defaults, explicit job scopes, and no secrets in untrusted jobs |
| Queue automation changes the wrong pull request or ref | Default-branch-only execution, API metadata checks, and validated queue refs |
| A release changes a workflow contract without review | Required tests, DCO, human approval, protected main, and release notes |

Review this model when workflow inputs, token permissions, queue behavior,
release automation, or cross-repository access changes.
