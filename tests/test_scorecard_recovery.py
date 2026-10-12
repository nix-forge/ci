"""Run the actual recovery shell against controlled GitHub CLI responses."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW = yaml.load(
    (ROOT / ".github/workflows/retry-scorecard.yml").read_text(), Loader=yaml.BaseLoader
)
SCRIPT = WORKFLOW["jobs"]["retry"]["steps"][0]["run"]
TRANSIENT = (
    "scorecard had an error: internal error: ListCommits:githubv4.Query: "
    "non-200 OK status code: 504 Gateway Timeout"
)


class RecoveryTests(unittest.TestCase):
    def run_recovery(self, changes=None, log=TRANSIENT, latest_attempt=1):
        run = {
            "repository": {"full_name": "nix-forge/example"},
            "head_repository": {"full_name": "nix-forge/example"},
            "name": "OpenSSF Scorecard",
            "path": ".github/workflows/scorecard.yml",
            "head_branch": "main",
            "head_sha": "a" * 40,
            "event": "schedule",
            "status": "completed",
            "conclusion": "failure",
            "run_attempt": 1,
        }
        run.update(changes or {})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "fixture.json"
            fixture.write_text(
                json.dumps({"run": run, "log": log, "latest_attempt": latest_attempt})
            )
            gh = root / "gh"
            gh.write_text(
                f"#!{sys.executable}\n"
                "import json,os,pathlib,sys\n"
                "d=json.loads(pathlib.Path(os.environ['FIXTURE']).read_text())\n"
                "a=sys.argv[1:]\n"
                "if a[:2] == ['run','rerun']:\n"
                " pathlib.Path(os.environ['RETRIED']).touch()\n"
                "elif a[:2] == ['run','view']: print(d['log'])\n"
                "elif '/commits/' in a[1]: print('a'*40)\n"
                "elif '/actions/runs/' in a[1]:\n"
                " print(d['latest_attempt'] if '--jq' in a else json.dumps(d['run']))\n"
                "else: print('main')\n"
            )
            gh.chmod(0o755)
            sleep = root / "sleep"
            sleep.write_text("#!/bin/sh\nexit 0\n")
            sleep.chmod(0o755)
            environment = dict(os.environ)
            environment.update(
                PATH=f"{root}:{os.environ['PATH']}",
                GH_REPO="nix-forge/example",
                SOURCE_RUN_ID="123",
                RUNNER_TEMP=str(root),
                FIXTURE=str(fixture),
                RETRIED=str(root / "retried"),
            )
            result = subprocess.run(
                ["bash", "-c", SCRIPT],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return (root / "retried").exists()

    def test_retries_verified_current_default_branch_outage(self):
        self.assertTrue(self.run_recovery())

    def test_does_not_retry_real_defects(self):
        self.assertFalse(self.run_recovery(log="Policy validation failed"))

    def test_rejects_untrusted_stale_exhausted_or_successful_runs(self):
        for changes in (
            {"head_repository": {"full_name": "attacker/fork"}},
            {"head_sha": "b" * 40},
            {"event": "pull_request"},
            {"head_branch": "feature"},
            {"run_attempt": 3},
            {"run_attempt": None},
            {"conclusion": "success"},
            {"status": "in_progress"},
            {"path": ".github/workflows/ci.yml"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.run_recovery(changes))

    def test_does_not_duplicate_an_existing_retry(self):
        self.assertFalse(self.run_recovery(latest_attempt=2))


if __name__ == "__main__":
    unittest.main()
