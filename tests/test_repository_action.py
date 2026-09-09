"""Exercise the composite's shell selection without downloading a toolchain."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ACTION = Path(__file__).parents[1] / "actions/repository-checks"


class RepositoryActionTests(unittest.TestCase):
    def test_default_and_custom_shell_are_single_arguments(self):
        action = yaml.load((ACTION / "action.yml").read_text(), Loader=yaml.BaseLoader)
        command = action["runs"]["steps"][0]["run"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "nix"
            executable.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                'with open(os.environ["CALL_LOG"], "a") as output:\n'
                '    output.write(json.dumps(sys.argv[1:]) + "\\n")\n'
            )
            executable.chmod(0o755)
            for shell in [
                action["inputs"]["dev-shell"]["default"],
                "ci",
                "$(touch injected)",
            ]:
                with self.subTest(shell=shell):
                    log = root / "calls.jsonl"
                    log.unlink(missing_ok=True)
                    subprocess.run(
                        ["bash", "-e", "-o", "pipefail", "-c", command],
                        cwd=root,
                        check=True,
                        env=os.environ
                        | {
                            "PATH": str(root) + os.pathsep + os.environ["PATH"],
                            "ACTION_PATH": str(ACTION),
                            "CHECK_DEV_SHELL": shell,
                            "CHECK_PARTITIONS": "flake/dev\n",
                            "CALL_LOG": str(log),
                        },
                    )
                    calls = [json.loads(line) for line in log.read_text().splitlines()]
                    self.assertEqual(calls[0], ["store", "add-path", "--", "flake/dev"])
                    self.assertEqual(
                        calls[1][:3], ["develop", ".#" + shell, "--command"]
                    )
                    self.assertEqual(calls[1][3], "bash")
                    self.assertFalse((root / "injected").exists())
