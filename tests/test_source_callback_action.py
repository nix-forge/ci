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

    def invoke(self, activity: str, exit_code: int = 0) -> tuple[int, list[str], str]:
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
                "sys.exit(int(os.environ['CALLBACK_EXIT_CODE']))\n"
            )
            gh.chmod(0o755)
            sleeper = root / "sleep"
            sleeper.write_text("#!/bin/sh\nexit 0\n")
            sleeper.chmod(0o755)
            result = subprocess.run(
                ["bash", "-e", "-c", action["runs"]["steps"][0]["run"]],
                env={
                    **os.environ,
                    "PATH": f"{root}:{os.environ['PATH']}",
                    "CALLBACK_RECEIPT": str(receipt),
                    "DEFAULT_BRANCH": "main",
                    "GH_REPO": "nix-forge/example",
                    "CALLBACK_EXIT_CODE": str(exit_code),
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
                result.stdout + result.stderr,
            )

    def test_legacy_queue_payload_is_unchanged(self) -> None:
        status, args, stderr = self.invoke("")
        self.assertEqual(status, 0, stderr)
        self.assertEqual(
            args,
            [
                "api",
                "repos/nix-forge/example/actions/workflows/reconcile-merge-queue.yml/dispatches",
                "--method",
                "POST",
                "-f",
                "ref=main",
                "-f",
                "inputs[source_run_id]=123",
            ],
        )

    def test_source_start_and_completion_bind_actual_attempt(self) -> None:
        for activity in ("in_progress", "completed"):
            with self.subTest(activity=activity):
                status, args, stderr = self.invoke(activity)
                self.assertEqual(status, 0, stderr)
                self.assertIn("inputs[source_run_id]=123", args)
                self.assertIn("inputs[source_run_attempt]=2", args)
                self.assertIn(f"inputs[source_activity]={activity}", args)

    def test_invalid_phase_cannot_dispatch(self) -> None:
        status, args, stderr = self.invoke("completed --ref unreviewed")
        self.assertNotEqual(status, 0, stderr)
        self.assertEqual(args, [])

    def test_failed_notification_keeps_bounded_retries_and_visible_failure(
        self,
    ) -> None:
        status, args, output = self.invoke("completed", exit_code=1)
        self.assertEqual(status, 1)
        self.assertEqual(output.count("notification attempt"), 3)
        self.assertEqual(args[0], "api")
        self.assertIn("inputs[source_activity]=completed", args)
