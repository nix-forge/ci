#!/usr/bin/env bash
set -euo pipefail

case "${CHECK_HOOK_RUNNER:-prek}" in
  prek|pre-commit) ;;
  *) echo 'Unsupported hook runner' >&2; exit 1 ;;
esac
if [[ $(git rev-parse --is-shallow-repository) != false ]]; then
  echo 'Repository checks require checkout fetch-depth: 0 to scan history.' >&2
  exit 1
fi
"${CHECK_HOOK_RUNNER:-prek}" run --all-files
if [[ -n ${CHECK_PUBLICATION_SCRIPT:-} ]]; then
  bash "$CHECK_PUBLICATION_SCRIPT"
  exit 0
fi
bash "$(dirname "$0")/scan-secrets.sh"
