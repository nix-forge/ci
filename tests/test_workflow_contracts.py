"""Reject real workflow regressions with small consumer fixtures."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

spec = importlib.util.spec_from_file_location(
    "contracts", Path(__file__).parents[1] / "scripts/check-workflow-contracts.py"
)
contracts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contracts)
PIN = "a" * 40
WORKFLOW = """name: CI
on:
  pull_request:
  merge_group:
permissions: {}
jobs:
  lint:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@PIN
        with:
          persist-credentials: false
          fetch-depth: 0
      - uses: nix-forge/ci/actions/repository-checks@PIN
""".replace("PIN", PIN)


class ContractTests(unittest.TestCase):
    def test_action_metadata_extensions(self):
        action = (
            "runs:\n  using: composite\n  steps:\n    - uses: example/action@main\n"
        )
        for directory in ["actions/example", ".github/actions/example"]:
            for extension in ["yml", "yaml"]:
                name = f"{directory}/action.{extension}"
                with self.subTest(name=name):
                    errors = self.check(WORKFLOW, {name: action})
                    self.assertTrue(
                        any(
                            name in error and "full commit SHA" in error
                            for error in errors
                        )
                    )

    def test_action_only_repository_reaches_each_validator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            action = root / "actions/example/action.yaml"
            action.parent.mkdir(parents=True)
            action.write_text("runs: {using: composite, steps: []}\n")
            commands = root / "bin"
            commands.mkdir()
            log = root / "calls.jsonl"
            for name in ["actionlint", "zizmor", "yamllint", "contracts"]:
                executable = commands / name
                executable.write_text(
                    f"#!{sys.executable}\n"
                    "import json, os, sys\n"
                    'with open(os.environ["CALL_LOG"], "a") as output:\n'
                    '    output.write(json.dumps(sys.argv) + "\\n")\n'
                )
                executable.chmod(0o755)
            subprocess.run(
                [
                    "bash",
                    str(Path(__file__).parents[1] / "scripts/check-workflows.sh"),
                    str(root),
                ],
                check=True,
                env=os.environ
                | {
                    "PATH": str(commands) + os.pathsep + os.environ["PATH"],
                    "WORKFLOW_LIBRARY": str(Path(__file__).parents[1]),
                    "WORKFLOW_CONTRACT_CHECKER": str(commands / "contracts"),
                    "CALL_LOG": str(log),
                },
            )
            calls = {
                Path(args[0]).name: args[1:]
                for args in map(json.loads, log.read_text().splitlines())
            }
            self.assertNotIn("actionlint", calls)
            for name in ["zizmor", "yamllint"]:
                self.assertIn("actions/example/action.yaml", calls[name])
            self.assertEqual(calls["contracts"], ["."])

    def check(self, workflow, extra=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".github/workflows/ci.yml"
            path.parent.mkdir(parents=True)
            path.write_text(workflow)
            for name, content in (extra or {}).items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            return contracts.validate(root)

    def test_valid_read_only_consumer(self):
        self.assertEqual(self.check(WORKFLOW), [])

    def test_missing_merge_queue_trigger(self):
        self.assertTrue(self.check(WORKFLOW.replace("  merge_group:\n", "")))

    def test_shallow_history(self):
        self.assertTrue(
            self.check(WORKFLOW.replace("fetch-depth: 0", "fetch-depth: 1"))
        )

    def test_mutable_pin_and_persisted_credentials(self):
        for before, after in [
            (PIN, "main"),
            ("persist-credentials: false", "persist-credentials: true"),
        ]:
            with self.subTest(after=after):
                self.assertTrue(self.check(WORKFLOW.replace(before, after)))

    def test_mixed_shared_releases(self):
        extra = WORKFLOW.replace(PIN, "b" * 40)
        self.assertTrue(self.check(WORKFLOW, {".github/workflows/second.yml": extra}))

    def test_nested_composites_cannot_escape_pin_validation(self):
        action = """name: fixture
description: fixture
runs:
  using: composite
  steps:
    - uses: actions/checkout@main
"""
        for path in [
            "actions/new/nested/action.yaml",
            ".github/actions/new/action.yml",
        ]:
            with self.subTest(path=path):
                errors = self.check(WORKFLOW, {path: action})
                self.assertTrue(any("full commit SHA" in error for error in errors))

    def test_privileged_checkout(self):
        workflow = WORKFLOW.replace("pull_request:", "pull_request_target:")
        self.assertTrue(any("must not check out" in e for e in self.check(workflow)))

    def test_template_metadata(self):
        self.assertTrue(self.check(WORKFLOW, {"workflow-templates/nix.yml": WORKFLOW}))
        self.assertEqual(
            self.check(
                WORKFLOW,
                {
                    "workflow-templates/nix.yml": WORKFLOW,
                    "workflow-templates/nix.properties.json": '{"name":"Nix","description":"Run checks"}',
                },
            ),
            [],
        )

    def test_incomplete_queue_callback(self):
        reconciler = """name: Reconcile
on:
  workflow_dispatch:
permissions: {}
jobs:
  reconcile:
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    steps:
      - uses: nix-forge/ci/actions/reconcile-queue@PIN
        with:
          workflows: '["ci.yml"]'
""".replace("PIN", PIN)
        errors = self.check(
            WORKFLOW, {".github/workflows/reconcile-merge-queue.yml": reconciler}
        )
        self.assertTrue(any("callback" in error for error in errors))
        self.assertTrue(any("dispatch triggers" in error for error in errors))
