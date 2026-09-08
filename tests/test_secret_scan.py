"""Exercise history and current-tree scanning with disposable Git canaries."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts/scan-secrets.sh"


class ScanTests(unittest.TestCase):
    def test_history_current_tree_and_shallow_clones(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()

            def git(*args):
                return subprocess.check_output(
                    ["git", *args], cwd=root, text=True
                ).strip()

            def commit():
                git("add", ".")
                git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

            def scan(path=root):
                return subprocess.run(
                    ["bash", str(SCRIPT)],
                    cwd=path,
                    check=False,
                    capture_output=True,
                    text=True,
                    env=os.environ | {"CHECK_HISTORY_BASELINE": ""},
                )

            git("init", "-q", "-b", "main")
            git("config", "user.name", "CI Test")
            git("config", "user.email", "ci@example.invalid")
            git("config", "core.hooksPath", str(root / "empty-hooks"))
            (root / ".gitleaks.toml").write_text(
                '[[rules]]\nid = "canary"\ndescription = "Disposable test"\n'
                'regex = "FORGE_TEST_[A-Z]{16}"\n'
            )
            (root / "data.txt").write_text("safe\n")
            commit()
            self.assertEqual(scan().returncode, 0)
            shallow = Path(directory) / "shallow"
            subprocess.run(
                ["git", "clone", "--quiet", "--depth=1", root.as_uri(), str(shallow)],
                check=True,
            )
            result = scan(shallow)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("shallow", result.stderr)
            (root / "data.txt").write_text("FORGE_TEST_" + "ABCDEFGHIJKLMNOP" + "\n")
            commit()
            self.assertNotEqual(scan().returncode, 0)
            # A metadata-only historical exemption cannot hide the current file.
            sha = git("rev-parse", "HEAD")
            with (root / ".gitleaks.toml").open("a") as config:
                config.write(f'[[rules.allowlists]]\ncommits = ["{sha}"]\n')
            commit()
            result = scan()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("leaks found", result.stderr)
            (root / "data.txt").unlink()
            commit()
            self.assertEqual(scan().returncode, 0)
            # Removing the exact historical exemption exposes the deleted canary.
            policy = root / ".gitleaks.toml"
            policy.write_text(policy.read_text().split("[[rules.allowlists]]")[0])
            commit()
            self.assertNotEqual(scan().returncode, 0)
