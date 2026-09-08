#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
if [[ $(git rev-parse --is-shallow-repository) != false ]]; then
  echo 'A shallow checkout cannot establish a clean history.' >&2
  exit 1
fi
history_args=()
config_args=()
if [[ -f .gitleaks.toml ]]; then
  config_args+=(--config .gitleaks.toml)
fi
if [[ -n ${CHECK_HISTORY_BASELINE:-} ]]; then
  test -f "$CHECK_HISTORY_BASELINE"
  history_args+=(--baseline-path "$CHECK_HISTORY_BASELINE")
fi
gitleaks git "${config_args[@]}" --redact --no-banner "${history_args[@]}"
snapshot=$(mktemp -d)
trap 'rm -rf "$snapshot"' EXIT
git archive --format=tar HEAD | tar -xf - -C "$snapshot"
(
  cd "$snapshot"
  gitleaks dir . "${config_args[@]}" --redact --no-banner
)
