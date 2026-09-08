#!/usr/bin/env bash
set -euo pipefail
actionlint
zizmor --pedantic --offline .github/workflows actions
yamllint .github actions
ruff check scripts tests
ruff format --check scripts tests
