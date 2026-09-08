"""Exercise additions, deletions and failed discovery through the public commands."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from repository_inventory import lockfiles, partitions, workflow_files


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git("init", "-q")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args])

    def file(self, name, content="{}"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        self.git("add", "--", name)

    def flake(self, directory):
        for name in ("flake.nix", "flake.lock"):
            self.file(str(Path(directory) / name))

    def test_added_renamed_and_deleted_partitions(self):
        self.flake(".")
        self.flake("flake/dev")
        self.assertEqual(partitions(self.root), ["flake/dev"])
        self.flake("tools/new partition")
        self.assertEqual(
            lockfiles(self.root),
            ["flake.lock", "flake/dev/flake.lock", "tools/new partition/flake.lock"],
        )
        self.git("mv", "flake/dev", "flake/test")
        self.git("rm", "-rf", "tools")
        self.assertEqual(partitions(self.root), ["flake/test"])

    def test_untracked_files_and_unpaired_locks_are_ignored(self):
        self.file("unpaired/flake.lock")
        (self.root / "flake.lock").write_text("{}")
        (self.root / "flake.nix").write_text("{}")
        self.assertEqual(lockfiles(self.root), [])

    def test_submodule_partitions_without_submodule_root(self):
        self.flake(".")
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        )
        with tempfile.TemporaryDirectory() as source:
            child = Path(source)
            subprocess.run(
                ["git", "clone", "-q", str(self.root), str(child / "child")], check=True
            )
            child /= "child"
            (child / "nested").mkdir()
            for name in ("flake.nix", "flake.lock"):
                (child / "nested" / name).write_text("{}")
            subprocess.run(["git", "-C", str(child), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(child),
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "commit",
                    "-qm",
                    "nested",
                ],
                check=True,
            )
            self.git(
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(child),
                "children/example",
            )
            self.assertEqual(lockfiles(self.root), ["flake.lock"])
            self.assertEqual(partitions(self.root), ["children/example/nested"])

    def test_nested_actions_and_both_yaml_extensions(self):
        for name in (
            "actions/deep/new/action.yaml",
            ".github/actions/a/b/action.yml",
            ".github/workflows/test.yaml",
            "workflow-templates/test.yml",
        ):
            self.file(name)
        inventory = workflow_files(self.root)
        self.assertEqual(len(inventory["actions"]), 2)
        self.assertEqual(len(inventory["workflows"]), 1)
        self.assertEqual(len(inventory["templates"]), 1)
        (self.root / "actions/deep/new/action.yaml").unlink()
        self.assertEqual(len(workflow_files(self.root)["actions"]), 1)


class CheckRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.log = self.root / "calls.jsonl"
        executable = self.root / "nix"
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "with open(os.environ['CALL_LOG'], 'a') as log:\n"
            "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if sys.argv[1] == 'eval':\n"
            "    print(os.environ['CHECK_NAMES'])\n"
            "    sys.exit(int(os.environ.get('EVAL_STATUS', '0')))\n"
            "sys.exit(1 if sys.argv[-1].endswith('.\"fail\"') else 0)\n"
        )
        executable.chmod(0o755)

    def run_checks(self, names, **extra):
        self.log.unlink(missing_ok=True)
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "run-flake-checks.py"),
                "--system",
                "x86_64-linux",
            ],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
            env=os.environ
            | {
                "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
                "CALL_LOG": str(self.log),
                "CHECK_NAMES": json.dumps(names),
            }
            | extra,
        )
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return result, [call for call in calls if call[0] == "build"]

    def test_additions_and_deletions_follow_current_outputs(self):
        for names in [["first"], ["first", "added"], ["added"]]:
            result, builds = self.run_checks(names)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                [json.loads(call[-1].rsplit(".", 1)[1]) for call in builds],
                sorted(names),
            )

    def test_one_failed_build_does_not_skip_other_checks(self):
        result, builds = self.run_checks(["after", "fail", "last"])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(builds), 3)
        self.assertIn("Failed checks: fail", result.stdout)

    def test_discovery_failure_cannot_build_a_default_target(self):
        for names, extra in [
            ([], {}),
            ({}, {}),
            (["ok"], {"EVAL_STATUS": "1"}),
            (["same", "same"], {}),
            ([123], {}),
        ]:
            result, builds = self.run_checks(names, **extra)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(builds, [])

    def test_names_are_single_arguments_without_shell_execution(self):
        name = "name with spaces;$(touch injected)"
        result, builds = self.run_checks([name])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(builds[0][-1], '.#checks."x86_64-linux".' + json.dumps(name))
        self.assertFalse((self.root / "injected").exists())


if __name__ == "__main__":
    unittest.main()
