"""Exercise cache trust and namespace policy independently of GitHub expressions."""

import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from nix_cache_policy import cache_policy, effective_providers


class CachePolicyTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "CACHE_ENABLED": "true",
            "CACHE_WRITE_MODE": "auto",
            "CACHE_SCOPE": "python-quality",
            "CACHE_PARTITION": "0",
            "CACHE_LOCK_HASH": "a" * 64,
            "GITHUB_SHA": "b" * 40,
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": "refs/heads/main",
            "DEFAULT_BRANCH": "main",
            "RUNNER_OS": "Linux",
            "RUNNER_ARCH": "X64",
        }

    def policy(self, **changes):
        return cache_policy(self.env | changes, "nix (Determinate Nix 3.24.2) 2.35.3")

    def test_default_behavior_only_saves_on_trusted_push_or_dispatch(self):
        for event in ["push", "workflow_dispatch"]:
            self.assertEqual(self.policy(GITHUB_EVENT_NAME=event)["save"], "true")
        for event in [
            "pull_request",
            "pull_request_target",
            "merge_group",
            "workflow_run",
            "schedule",
        ]:
            self.assertEqual(self.policy(GITHUB_EVENT_NAME=event)["save"], "false")
        self.assertEqual(self.policy(GITHUB_REF="refs/heads/topic")["save"], "false")
        self.assertEqual(self.policy(DEFAULT_BRANCH="")["save"], "false")
        self.assertEqual(self.policy(CACHE_ENABLED="false")["save"], "false")

    def test_read_only_never_saves_even_on_default_branch(self):
        for event in ["push", "workflow_dispatch", "schedule", "merge_group"]:
            self.assertEqual(
                self.policy(CACHE_WRITE_MODE="read-only", GITHUB_EVENT_NAME=event)[
                    "save"
                ],
                "false",
            )

    def test_seed_requires_trusted_default_branch_event(self):
        self.assertEqual(
            self.policy(CACHE_WRITE_MODE="seed", GITHUB_EVENT_NAME="schedule")["save"],
            "true",
        )
        for changes in [
            {"GITHUB_EVENT_NAME": "pull_request"},
            {"GITHUB_REF": "refs/heads/topic"},
            {"DEFAULT_BRANCH": ""},
        ]:
            with self.assertRaisesRegex(ValueError, "trusted default-branch"):
                self.policy(CACHE_WRITE_MODE="seed", **changes)

    def test_key_changes_with_actual_runtime_platform_workload_partition_and_locks(
        self,
    ):
        baseline = self.policy()["key"]
        for key, value in [
            ("RUNNER_ARCH", "ARM64"),
            ("RUNNER_OS", "macOS"),
            ("CACHE_SCOPE", "guide"),
            ("CACHE_PARTITION", "1"),
            ("CACHE_LOCK_HASH", "c" * 64),
            ("GITHUB_SHA", "d" * 40),
        ]:
            self.assertNotEqual(self.policy(**{key: value})["key"], baseline)
        self.assertNotEqual(
            cache_policy(self.env, "different Nix runtime")["key"], baseline
        )
        self.assertTrue(baseline.startswith("nix-v2-Linux-X64-"))

    def test_broader_restore_is_opt_in_and_stays_in_workload(self):
        self.assertEqual(self.policy()["compatible-prefix"], "")
        old = self.policy(CACHE_RESTORE_COMPATIBLE="true")
        changed = self.policy(CACHE_RESTORE_COMPATIBLE="true", CACHE_LOCK_HASH="d" * 64)
        self.assertEqual(old["compatible-prefix"], changed["compatible-prefix"])
        self.assertNotEqual(old["restore-prefix"], changed["restore-prefix"])
        self.assertNotEqual(
            old["compatible-prefix"],
            self.policy(CACHE_RESTORE_COMPATIBLE="true", CACHE_PARTITION="2")[
                "compatible-prefix"
            ],
        )

    def test_dimensions_and_compatible_prefixes_have_unambiguous_boundaries(self):
        left = self.policy(
            CACHE_SCOPE="foo-p-bar", CACHE_PARTITION="", CACHE_RESTORE_COMPATIBLE="true"
        )
        right = self.policy(
            CACHE_SCOPE="foo", CACHE_PARTITION="bar", CACHE_RESTORE_COMPATIBLE="true"
        )
        other = self.policy(
            CACHE_SCOPE="foo-extra",
            CACHE_PARTITION="bar",
            CACHE_RESTORE_COMPATIBLE="true",
        )
        self.assertNotEqual(left["key"], right["key"])
        self.assertFalse(other["key"].startswith(right["compatible-prefix"]))
        self.assertFalse(right["key"].startswith(left["compatible-prefix"]))

    def test_rejects_invalid_identifiers_and_output_injection(self):
        for key, value in [
            ("CACHE_SCOPE", "path/name"),
            ("CACHE_PARTITION", "x\nsave=true"),
            ("GITHUB_SHA", "main"),
            ("CACHE_LOCK_HASH", "bad"),
            ("RUNNER_OS", ""),
            ("CACHE_WRITE_MODE", "always"),
        ]:
            with self.assertRaises(ValueError):
                self.policy(**{key: value})

    def test_provider_report_omits_credentials_queries_paths_and_private_keys(self):
        result = effective_providers(
            {
                "substituters": {
                    "value": [
                        "https://username:secret@cache.example.invalid/private?token=secret",
                        "file:///private/cache",
                    ]
                },
                "trusted-public-keys": {"value": ["cache.example.invalid-1:PUBLIC"]},
                "access-tokens": {"value": "secret"},
            }
        )
        self.assertEqual(
            result,
            {
                "substituters": ["https://cache.example.invalid", "file"],
                "keyNames": ["cache.example.invalid-1"],
            },
        )

    def test_action_uses_policy_save_result_and_has_no_purge(self):
        action = yaml.load(
            (Path(__file__).parents[1] / "actions/setup-nix/action.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        self.assertEqual(action["inputs"]["cache"]["default"], "false")
        self.assertEqual(action["inputs"]["cache-write-mode"]["default"], "auto")
        cache = next(s for s in action["runs"]["steps"] if s.get("id") == "cache")
        self.assertEqual(
            cache["with"]["save"], "${{ steps.cache-policy.outputs.save }}"
        )
        self.assertEqual(cache["with"]["purge"], "false")


if __name__ == "__main__":
    unittest.main()
