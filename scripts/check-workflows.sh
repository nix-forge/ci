#!/usr/bin/env bash
set -euo pipefail
library=$(cd "$(dirname "$0")/.." && pwd)
cd "${1:-.}"
shopt -s nullglob
workflows=(.github/workflows/*.yml .github/workflows/*.yaml workflow-templates/*.yml workflow-templates/*.yaml)
actions=(actions/*/action.yml .github/actions/*/action.yml)
actionlint "${workflows[@]}"
zizmor --pedantic --offline "${workflows[@]}" "${actions[@]}"
yamllint -c "$library/.yamllint.yml" "${workflows[@]}" "${actions[@]}"
python3 "$library/scripts/check-workflow-contracts.py" .
