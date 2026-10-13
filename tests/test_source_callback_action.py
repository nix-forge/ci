"""Exercise the callback's actual shell payload without calling GitHub."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


class SourceCallbackActionTests(unittest.TestCase):
    """Keep existing queue callers compatible and bind source attempt notifications."""

    def invoke(self, activity: str) -> tuple[int, list[str], str]:
        action = yaml.load(
            (
                Path(__file__).parents[1] / "actions/queue-completion/action.yml"
            ).read_text(),
            Loader=yaml.BaseLoader,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = root / "arguments.json"
            gh = root / "gh"
            gh.write_text(
                f"#!{sys.executable}\nimport json, os, sys\n"
                "from pathlib import Path\n"
                "Path(os.environ['CALLBACK_RECEIPT']).write_text(json.dumps(sys.argv[1:]))\n"
            )
            gh.chmod(0o755)
            result = subprocess.run(
                ["bash", "-e", "-c", action["runs"]["steps"][0]["run"]],
                env={
                    **os.environ,
                    "PATH": f"{root}:{os.environ['PATH']}",
                    "CALLBACK_RECEIPT": str(receipt),
                    "DEFAULT_BRANCH": "main",
                    "GITHUB_RUN_ID": "123",
                    "GITHUB_RUN_ATTEMPT": "2",
                    "SOURCE_ACTIVITY": activity,
                },
                capture_output=True,
                text=True,
                check=False,
            )
            return (
                result.returncode,
                json.loads(receipt.read_text()) if receipt.exists() else [],
                result.stderr,
            )

    def test_legacy_queue_payload_is_unchanged(self) -> None:
        status, args, stderr = self.invoke("")
        self.assertEqual(status, 0, stderr)
        self.assertEqual(
            args,
            [
                "workflow",
                "run",
                "reconcile-merge-queue.yml",
                "--ref",
                "main",
                "-f",
                "source_run_id=123",
            ],
        )

    def test_source_start_and_completion_bind_actual_attempt(self) -> None:
        for activity in ("in_progress", "completed"):
            with self.subTest(activity=activity):
                status, args, stderr = self.invoke(activity)
                self.assertEqual(status, 0, stderr)
                self.assertIn("source_run_id=123", args)
                self.assertIn("source_run_attempt=2", args)
                self.assertIn(f"source_activity={activity}", args)

    def test_invalid_phase_cannot_dispatch(self) -> None:
        status, args, stderr = self.invoke("completed --ref unreviewed")
        self.assertNotEqual(status, 0, stderr)
        self.assertEqual(args, [])
