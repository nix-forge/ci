"""Release rollouts cover every caller without editing unrelated dependencies."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
spec = importlib.util.spec_from_file_location(
    "sync", Path(__file__).parents[1] / "scripts/sync-release.py"
)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
OLD = "a" * 40
NEW = "b" * 40


class ReleaseTests(unittest.TestCase):
    def test_resolves_release_tag_to_immutable_commit(self):
        responses = [
            {"tag_name": "v2.5.0", "draft": False, "prerelease": False},
            {"sha": NEW},
        ]
        with patch.object(
            sync.subprocess, "check_output", side_effect=map(json.dumps, responses)
        ) as api:
            self.assertEqual(sync.release_pin(None), ("v2.5.0", NEW))
        self.assertTrue(api.call_args_list[0].args[0][-1].endswith("/releases/latest"))
        self.assertTrue(api.call_args_list[1].args[0][-1].endswith("/commits/v2.5.0"))

    def test_rejects_unpublished_or_prerelease_versions(self):
        for field in ["draft", "prerelease"]:
            release = {
                "tag_name": "v2.5.0",
                "draft": False,
                "prerelease": False,
                field: True,
            }
            with (
                patch.object(
                    sync.subprocess, "check_output", return_value=json.dumps(release)
                ),
                self.assertRaises(ValueError),
            ):
                sync.release_pin(None)

    def test_updates_all_inventories_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = (
                f"    uses: nix-forge/ci/.github/workflows/codeql.yml@{OLD} # v2.4.0\n"
                f"      - uses: nix-forge/ci/actions/setup-nix@{OLD}\n"
                f"      - uses: other/action@{OLD} # v1.0.0\n"
            )
            names = [
                ".github/workflows/ci.yml",
                "workflow-templates/nix.yaml",
                "actions/nested/setup/action.yaml",
                ".github/actions/local/action.yml",
            ]
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
            changes = sync.updates(root, "v2.5.0", NEW)
            self.assertEqual(len(changes), 4)
            for path, content in changes.items():
                self.assertEqual(path.read_text(), source)
                self.assertEqual(content.count(f"@{NEW} # v2.5.0"), 2)
                self.assertIn(f"other/action@{OLD} # v1.0.0", content)
                path.write_text(content)
            self.assertEqual(sync.updates(root, "v2.5.0", NEW), {})

    def test_empty_checkout_fails_instead_of_claiming_success(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            sync.updates(Path(directory), "v2.5.0", NEW)
