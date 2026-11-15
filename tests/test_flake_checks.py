"""Exercise base-aware native check selection without invoking Nix builds."""

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

SCRIPT = Path(__file__).parents[1] / "scripts/run-flake-checks.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("flake_checks", SCRIPT)
FLAKE_CHECKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FLAKE_CHECKS)
ACTION = Path(__file__).parents[1] / "actions/flake-checks/action.yml"


class FlakeCheckSelectionTests(unittest.TestCase):
    @mock.patch.object(FLAKE_CHECKS.subprocess, "check_output")
    def test_derivation_evaluation_applies_timeout(self, check_output):
        check_output.return_value = "/nix/store/" + "0" * 32 + "-check.drv\n"

        FLAKE_CHECKS.check_derivation("x86_64-linux", "check", timeout=30)

        self.assertEqual(check_output.call_args.kwargs["timeout"], 30)

    def test_action_passes_pull_request_and_merge_group_bases(self):
        action = yaml.load(ACTION.read_text(), Loader=yaml.BaseLoader)
        expression = action["runs"]["steps"][0]["env"]["BASE_REVISION"]

        self.assertIn("inputs.base-revision", expression)
        self.assertIn("github.event.inputs.base_sha", expression)
        self.assertIn("github.event.pull_request.base.sha", expression)
        self.assertIn("github.event.merge_group.base_sha", expression)
        self.assertNotIn("github.event.before", expression)

    def test_action_passes_partition_inputs(self):
        action = yaml.load(ACTION.read_text(), Loader=yaml.BaseLoader)
        step = action["runs"]["steps"][0]

        self.assertEqual(
            step["env"]["PARTITION_COUNT"], "${{ inputs.partition-count }}"
        )
        self.assertEqual(
            step["env"]["PARTITION_INDEX"], "${{ inputs.partition-index }}"
        )
        self.assertIn('--partition-count "$PARTITION_COUNT"', step["run"])
        self.assertIn('--partition-index "$PARTITION_INDEX"', step["run"])
        self.assertEqual(step["env"]["WEIGHTS_FILE"], "${{ inputs.weights-file }}")
        self.assertIn('arguments+=(--weights "$WEIGHTS_FILE")', step["run"])

    def test_action_bounds_base_evaluation(self):
        action = yaml.load(ACTION.read_text(), Loader=yaml.BaseLoader)
        step = action["runs"]["steps"][0]
        self.assertEqual(action["inputs"]["base-eval-timeout"]["default"], "60")
        self.assertEqual(
            step["env"]["BASE_EVAL_TIMEOUT"], "${{ inputs.base-eval-timeout }}"
        )
        self.assertIn('--base-eval-timeout "$BASE_EVAL_TIMEOUT"', step["run"])

    def test_weighted_partitions_balance_a_heavy_check(self):
        from ci_partitioning import partition_names

        partitions = partition_names(
            ["heavy", "light-a", "light-b", "light-c"],
            2,
            weights={"heavy": 10},
        )
        self.assertEqual(partitions[0], ["heavy"])
        self.assertEqual(partitions[1], ["light-a", "light-b", "light-c"])

    @mock.patch.object(FLAKE_CHECKS, "check_derivation")
    @mock.patch.object(FLAKE_CHECKS, "resolve_base_source")
    @mock.patch.object(FLAKE_CHECKS.subprocess, "run")
    @mock.patch.object(FLAKE_CHECKS.subprocess, "check_output")
    def test_partitions_are_disjoint_and_complete(
        self, check_output, run_command, resolve_base, check_derivation
    ):
        names = ["alpha", "bravo", "charlie", "delta", "echo"]
        check_output.return_value = '["echo", "charlie", "alpha", "delta", "bravo"]'
        resolve_base.return_value = None
        check_derivation.side_effect = lambda _system, name, **_kwargs: (
            f"/nix/store/{'0' * 32}-{name}.drv"
        )
        run_command.return_value.returncode = 0

        selected = []
        for index in range(3):
            run_command.reset_mock()
            result = FLAKE_CHECKS.run(
                "x86_64-linux", partition_count=3, partition_index=index
            )
            self.assertEqual(result, 0)
            selected.append(
                {
                    call.args[0][-1]
                    .removeprefix(f"/nix/store/{'0' * 32}-")
                    .removesuffix(".drv^*")
                    for call in run_command.call_args_list
                    if call.args and call.args[0][0:2] == ["nix", "build"]
                }
            )

        self.assertEqual(set.union(*selected), set(names))
        self.assertFalse(selected[0] & selected[1])
        self.assertFalse(selected[0] & selected[2])
        self.assertFalse(selected[1] & selected[2])

    @mock.patch.object(FLAKE_CHECKS, "check_derivation")
    @mock.patch.object(FLAKE_CHECKS, "resolve_base_source")
    @mock.patch.object(FLAKE_CHECKS.subprocess, "run")
    @mock.patch.object(FLAKE_CHECKS.subprocess, "check_output")
    def test_unvalidated_base_cannot_suppress_an_unchanged_failing_check(
        self, names, build, resolve, derivation
    ):
        names.return_value = '["unchanged"]'
        derivation.return_value = "/nix/store/" + "0" * 32 + "-unchanged.drv"
        build.return_value.returncode = 1
        self.assertEqual(FLAKE_CHECKS.run("x86_64-linux", base_revision="a" * 40), 1)
        resolve.assert_not_called()
        self.assertEqual(build.call_count, 1)

    def test_rejects_empty_partition(self):
        with (
            mock.patch.object(
                FLAKE_CHECKS.subprocess, "check_output", return_value='["only"]'
            ),
            self.assertRaisesRegex(ValueError, "selects no flake checks"),
        ):
            FLAKE_CHECKS.run("x86_64-linux", partition_count=2, partition_index=1)

    @mock.patch.object(FLAKE_CHECKS, "check_derivation")
    @mock.patch.object(FLAKE_CHECKS, "resolve_base_source")
    @mock.patch.object(FLAKE_CHECKS.subprocess, "run")
    @mock.patch.object(FLAKE_CHECKS.subprocess, "check_output")
    def test_builds_only_checks_changed_from_base(
        self, check_output, run_command, resolve_base, check_derivation
    ):
        check_output.return_value = '["changed", "unchanged"]'
        resolve_base.return_value = "git+file:///repo?rev=" + "a" * 40

        def derivation(_system, name, *, output="checks", source=".", timeout=None):
            del output, timeout
            revision = "base" if source.startswith("git+") else "head"
            if name == "unchanged":
                revision = "same"
            return f"/nix/store/{'0' * 32}-{name}-{revision}.drv"

        check_derivation.side_effect = derivation
        run_command.return_value.returncode = 0

        result = FLAKE_CHECKS.run(
            "x86_64-linux", base_revision="a" * 40, base_validated=True
        )

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

        result = FLAKE_CHECKS.run(
            "x86_64-linux", base_revision="a" * 40, base_validated=True
        )

        self.assertEqual(result, 0)
        self.assertTrue(
            any(
                call.args and call.args[0][0:2] == ["nix", "build"]
                for call in run_command.call_args_list
            )
        )

    @mock.patch.object(FLAKE_CHECKS, "check_derivation")
    @mock.patch.object(FLAKE_CHECKS, "resolve_base_source")
    @mock.patch.object(FLAKE_CHECKS.subprocess, "run")
    @mock.patch.object(FLAKE_CHECKS.subprocess, "check_output")
    def test_slow_base_evaluation_builds_current_check(
        self, check_output, run_command, resolve_base, check_derivation
    ):
        check_output.return_value = '["check"]'
        resolve_base.return_value = "git+file:///repo?rev=" + "a" * 40
        check_derivation.side_effect = [
            "/nix/store/" + "0" * 32 + "-check-head.drv",
            subprocess.TimeoutExpired(["nix", "eval"], 60),
        ]
        run_command.return_value.returncode = 0

        result = FLAKE_CHECKS.run(
            "x86_64-linux", base_revision="a" * 40, base_validated=True
        )

        self.assertEqual(result, 0)
        self.assertEqual(check_derivation.call_args_list[1].kwargs["timeout"], 60)
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

        self.assertEqual(
            source,
            "git+file:///repo?shallow=1&submodules=1&rev=" + "a" * 40,
        )
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


class BatchSelectionTests(unittest.TestCase):
    def setUp(self):
        self.drv = "/nix/store/" + "0" * 32 + "-"

    @mock.patch.object(FLAKE_CHECKS.subprocess, "check_output")
    def test_batch_validates_complete_output_and_timeout(self, output):
        import json

        output.return_value = json.dumps({"one": self.drv + "one.drv", "missing": None})
        result = FLAKE_CHECKS.check_derivations(
            "x86_64-linux", ["one", "missing"], timeout=30
        )
        self.assertIsNone(result["missing"])
        self.assertEqual(output.call_args.kwargs["timeout"], 30)
        for malformed in ["{}", "[]", '{"one": 3, "missing": null}']:
            output.return_value = malformed
            with self.assertRaises(ValueError):
                FLAKE_CHECKS.check_derivations("x86_64-linux", ["one", "missing"])

    def _run(self, head, base, *, source="base", roots=None):
        with (
            mock.patch.object(
                FLAKE_CHECKS.subprocess,
                "check_output",
                return_value='["changed","new","same"]',
            ),
            mock.patch.object(FLAKE_CHECKS, "resolve_base_source", return_value=source),
            mock.patch.object(
                FLAKE_CHECKS,
                "check_derivations",
                side_effect=[head, base] if source else [head],
            ) as batch,
            mock.patch.object(
                FLAKE_CHECKS,
                "check_derivation",
                side_effect=lambda _system, name, **_kw: self.drv + name + ".drv",
            ) as isolated,
            mock.patch.object(
                FLAKE_CHECKS.subprocess, "run", return_value=mock.Mock(returncode=0)
            ) as command,
        ):
            result = FLAKE_CHECKS.run(
                "x86_64-linux",
                eval_batch_size=4,
                cache_roots=roots,
                base_validated=True,
            )
        return result, batch, isolated, command

    def test_changed_and_new_build_but_unchanged_skips_in_two_evaluations(self):
        head = {n: self.drv + n + ".drv" for n in ["changed", "new", "same"]}
        base = {"changed": self.drv + "old.drv", "new": None, "same": head["same"]}
        result, batch, isolated, command = self._run(head, base)
        self.assertEqual(result, 0)
        self.assertEqual(batch.call_count, 2)
        isolated.assert_not_called()
        self.assertEqual(
            [c.args[0][-1] for c in command.call_args_list],
            [head[n] + "^*" for n in ["changed", "new"]],
        )

    def test_base_timeout_builds_all_without_per_check_retries(self):
        head = {n: self.drv + n + ".drv" for n in ["changed", "new", "same"]}
        result, batch, isolated, command = self._run(
            head, subprocess.TimeoutExpired("nix", 60)
        )
        self.assertEqual(result, 0)
        self.assertEqual(batch.call_count, 2)
        isolated.assert_not_called()
        self.assertEqual(command.call_count, 3)

    def test_current_batch_failure_falls_back_to_isolated_current_checks(self):
        result, _batch, isolated, command = self._run(
            ValueError("batch invalid"), {}, source=None
        )
        self.assertEqual(result, 0)
        self.assertEqual(isolated.call_count, 3)
        self.assertEqual(command.call_count, 3)

    def test_cache_roots_retain_successful_outputs_using_safe_names(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            head = {n: self.drv + n + ".drv" for n in ["changed", "new", "same"]}
            result, _batch, _isolated, command = self._run(
                head, {}, source=None, roots=Path(directory)
            )
            self.assertEqual(result, 0)
            for call in command.call_args_list:
                args = call.args[0]
                self.assertNotIn("--no-link", args)
                root = Path(args[args.index("--out-link") + 1])
                self.assertEqual(root.parent, Path(directory))
                self.assertRegex(root.name, r"^[0-9a-f]{64}$")

    def test_rejects_unbounded_batches_and_relative_roots(self):
        for size in [0, 9]:
            with self.assertRaises(ValueError):
                FLAKE_CHECKS.run("x86_64-linux", eval_batch_size=size)
        with self.assertRaises(ValueError):
            FLAKE_CHECKS.run("x86_64-linux", cache_roots=Path("relative"))


if __name__ == "__main__":
    unittest.main()
