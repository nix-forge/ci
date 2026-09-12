"""Exercise base-aware native check selection without invoking Nix builds."""

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import yaml

SCRIPT = Path(__file__).parents[1] / "scripts/run-flake-checks.py"
SPEC = importlib.util.spec_from_file_location("flake_checks", SCRIPT)
FLAKE_CHECKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FLAKE_CHECKS)
ACTION = Path(__file__).parents[1] / "actions/flake-checks/action.yml"


class FlakeCheckSelectionTests(unittest.TestCase):
    def test_action_passes_pull_request_and_merge_group_bases(self):
        action = yaml.load(ACTION.read_text(), Loader=yaml.BaseLoader)
        expression = action["runs"]["steps"][0]["env"]["BASE_REVISION"]

        self.assertIn("inputs.base-revision", expression)
        self.assertIn("github.event.inputs.base_sha", expression)
        self.assertIn("github.event.pull_request.base.sha", expression)
        self.assertIn("github.event.merge_group.base_sha", expression)
        self.assertNotIn("github.event.before", expression)

    @mock.patch.object(FLAKE_CHECKS, "check_derivation")
    @mock.patch.object(FLAKE_CHECKS, "resolve_base_source")
    @mock.patch.object(FLAKE_CHECKS.subprocess, "run")
    @mock.patch.object(FLAKE_CHECKS.subprocess, "check_output")
    def test_builds_only_checks_changed_from_base(
        self, check_output, run_command, resolve_base, check_derivation
    ):
        check_output.return_value = '["changed", "unchanged"]'
        resolve_base.return_value = "git+file:///repo?rev=" + "a" * 40

        def derivation(_system, name, *, output="checks", source="."):
            del output
            revision = "base" if source.startswith("git+") else "head"
            if name == "unchanged":
                revision = "same"
            return f"/nix/store/{'0' * 32}-{name}-{revision}.drv"

        check_derivation.side_effect = derivation
        run_command.return_value.returncode = 0

        result = FLAKE_CHECKS.run("x86_64-linux", base_revision="a" * 40)

        self.assertEqual(result, 0)
        build_calls = [
            call
            for call in run_command.call_args_list
            if call.args and call.args[0][0:2] == ["nix", "build"]
        ]
        self.assertEqual(len(build_calls), 1)
        self.assertIn("-changed-head.drv^*", build_calls[0].args[0][-1])

    @mock.patch.object(FLAKE_CHECKS, "check_derivation")
    @mock.patch.object(FLAKE_CHECKS, "resolve_base_source")
    @mock.patch.object(FLAKE_CHECKS.subprocess, "run")
    @mock.patch.object(FLAKE_CHECKS.subprocess, "check_output")
    def test_base_evaluation_failure_builds_current_check(
        self, check_output, run_command, resolve_base, check_derivation
    ):
        check_output.return_value = '["check"]'
        resolve_base.return_value = "git+file:///repo?rev=" + "a" * 40
        check_derivation.side_effect = [
            "/nix/store/" + "0" * 32 + "-check-head.drv",
            subprocess.CalledProcessError(1, ["nix", "eval"]),
        ]
        run_command.return_value.returncode = 0

        result = FLAKE_CHECKS.run("x86_64-linux", base_revision="a" * 40)

        self.assertEqual(result, 0)
        self.assertTrue(
            any(
                call.args and call.args[0][0:2] == ["nix", "build"]
                for call in run_command.call_args_list
            )
        )

    def test_rejects_invalid_base_revision(self):
        with self.assertRaisesRegex(ValueError, "Invalid base revision"):
            FLAKE_CHECKS.resolve_base_source("main; touch injected")

    @mock.patch.object(FLAKE_CHECKS.subprocess, "check_output")
    @mock.patch.object(FLAKE_CHECKS.subprocess, "run")
    def test_fetches_a_missing_base_revision(self, run_command, check_output):
        run_command.side_effect = [
            mock.Mock(returncode=1),
            mock.Mock(returncode=0),
            mock.Mock(returncode=0),
        ]
        check_output.return_value = "/repo\n"

        source = FLAKE_CHECKS.resolve_base_source("a" * 40)

        self.assertEqual(source, "git+file:///repo?submodules=1&rev=" + "a" * 40)
        self.assertEqual(
            run_command.call_args_list[1].args[0],
            ["git", "fetch", "--no-tags", "--depth=1", "origin", "a" * 40],
        )

    @mock.patch.object(FLAKE_CHECKS.subprocess, "run")
    def test_missing_base_after_fetch_keeps_full_build(self, run_command):
        run_command.side_effect = [
            mock.Mock(returncode=1),
            mock.Mock(returncode=1),
            mock.Mock(returncode=1),
        ]

        self.assertIsNone(FLAKE_CHECKS.resolve_base_source("a" * 40))


if __name__ == "__main__":
    unittest.main()
