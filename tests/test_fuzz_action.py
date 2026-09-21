"""Check the shared fuzz action's input boundary and process arguments."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ACTION = Path(__file__).parents[1] / "actions/fuzz-atheris/action.yml"


class FuzzActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        action = yaml.load(ACTION.read_text(), Loader=yaml.BaseLoader)
        cls.steps = {step["name"]: step for step in action["runs"]["steps"]}

    def run_step(self, name, root, **inputs):
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", self.steps[name]["run"]],
            cwd=root,
            env=os.environ | inputs,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_rejects_unsafe_target_and_unbounded_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fuzz").mkdir()
            (root / "fuzz/target.py").touch()
            valid = {
                "FUZZ_TARGET": "fuzz/target.py",
                "FUZZ_DURATION": "180",
                "FUZZ_INPUT_TIMEOUT": "10",
            }
            self.assertEqual(
                self.run_step("Validate fuzz inputs", root, **valid).returncode, 0
            )
            for changed in [
                {"FUZZ_TARGET": "fuzz/../../outside.py"},
                {"FUZZ_TARGET": "fuzz/target.py; touch injected"},
                {"FUZZ_TARGET": "fuzz/missing.py"},
                {"FUZZ_DURATION": "601"},
                {"FUZZ_DURATION": "$(touch injected)"},
                {"FUZZ_INPUT_TIMEOUT": "121"},
                {"FUZZ_INPUT_TIMEOUT": "0"},
            ]:
                with self.subTest(changed=changed):
                    result = self.run_step(
                        "Validate fuzz inputs", root, **valid | changed
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((root / "injected").exists())

    def test_campaign_passes_target_and_limits_as_single_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "fuzz-venv/bin/python"
            executable.parent.mkdir(parents=True)
            executable.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                'with open(os.environ["CALL_LOG"], "w") as output:\n'
                "    json.dump(sys.argv[1:], output)\n"
            )
            executable.chmod(0o755)
            log = root / "argv.json"
            result = self.run_step(
                "Run Atheris",
                root,
                RUNNER_TEMP=str(root),
                CALL_LOG=str(log),
                FUZZ_TARGET="fuzz/target.py",
                FUZZ_DURATION="180",
                FUZZ_INPUT_TIMEOUT="60",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(log.read_text()),
                [
                    "fuzz/target.py",
                    "-max_total_time=180",
                    "-timeout=60",
                    f"-artifact_prefix={root}/fuzz-crashes/",
                ],
            )
            self.assertTrue((root / "fuzz-crashes").is_dir())

    def test_dependencies_and_failure_artifact_are_pinned(self):
        install = self.steps["Install Atheris"]["run"]
        self.assertIn("atheris==3.1.0", install)
        self.assertIn("--only-binary=:all:", install)
        artifact = self.steps["Save crashing inputs"]
        self.assertEqual(artifact["if"], "failure()")
        self.assertEqual(artifact["with"]["retention-days"], "7")
