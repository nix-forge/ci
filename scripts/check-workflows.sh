#!/usr/bin/env bash
set -euo pipefail
library=${WORKFLOW_LIBRARY:-$(cd "$(dirname "$0")/.." && pwd)}
cd "${1:-.}"
inventory=$(mktemp -d)
trap 'rm -rf "$inventory"' EXIT
# Write first, then read. Process substitution would hide discovery failures.
python3 "$library/scripts/repository_inventory.py" workflows --null > "$inventory/workflows"
python3 "$library/scripts/repository_inventory.py" actions --null > "$inventory/actions"
mapfile -d '' -t workflows < "$inventory/workflows"
mapfile -d '' -t actions < "$inventory/actions"
if ((${#workflows[@]})); then
  actionlint "${workflows[@]}"
fi
if ((${#workflows[@]} + ${#actions[@]})); then
  zizmor --pedantic --offline "${workflows[@]}" "${actions[@]}"
  yamllint -c "$library/.yamllint.yml" "${workflows[@]}" "${actions[@]}"
fi
if [[ -n ${WORKFLOW_CONTRACT_CHECKER:-} ]]; then
  "$WORKFLOW_CONTRACT_CHECKER" .
else
  python3 "$library/scripts/check-workflow-contracts.py" .
fi
