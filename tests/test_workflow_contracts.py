"""Reject real workflow regressions with small consumer fixtures."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

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
