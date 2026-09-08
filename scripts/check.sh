#!/usr/bin/env bash
set -euo pipefail
bash scripts/check-workflows.sh
shellcheck scripts/*.sh
ruff check scripts tests
ruff format --check scripts tests
python3 -m unittest discover -s tests -v
