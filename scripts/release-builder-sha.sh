#!/usr/bin/env bash
# Resolve the signer identity from the same immutable reference used to build.
set -euo pipefail
workflow=${1:?Usage: release-builder-sha.sh WORKFLOW}
builder_sha=$(sed -nE 's%^[[:space:]]*uses:[[:space:]]+nix-forge/ci/\.github/workflows/slsa-(source|nix-seal)-release\.yml@([0-9a-f]{40})([[:space:]]+#.*)?[[:space:]]*$%\2%p' "$workflow")
if [[ ! "$builder_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo 'Release must call exactly one immutable SLSA builder.' >&2
  exit 1
fi
printf '%s\n' "$builder_sha"
