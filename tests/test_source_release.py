"""Reject release tags outside reviewed default-branch history."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW = yaml.load(
    (ROOT / ".github/workflows/slsa-source-release.yml").read_text(),
    Loader=yaml.BaseLoader,
)
SCRIPT = next(
    step["run"]
    for step in WORKFLOW["jobs"]["build"]["steps"]
    if step.get("name") == "Validate release inputs"
)


class SourceReleaseTests(unittest.TestCase):
    def validate(self, reviewed):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = dict(os.environ)
            environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")

            def git(*arguments):
                return subprocess.check_output(
                    ["git", *arguments], cwd=root, env=environment, text=True
                ).strip()

            git("init", "-q")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@invalid.example")
            notes = root / "docs/releases"
            notes.mkdir(parents=True)
            tag = "v0.1.0"
            (notes / f"{tag}.md").write_text("## Changelog\n\nFixture release.\n")
            git("add", ".")
            git("commit", "-qm", "Reviewed fixture")
            git("update-ref", "refs/remotes/origin/main", "HEAD")
            if not reviewed:
                tag = "v0.2.0"
                (notes / f"{tag}.md").write_text(
                    "## Changelog\n\nUnreviewed fixture.\n"
                )
                git("add", ".")
                git("commit", "-qm", "Unreviewed fixture")
            git("tag", tag)
            environment.update(
                TAG=tag,
                COMMIT=git("rev-parse", "HEAD"),
                PROJECT_NAME="fixture",
                RELEASE_NOTES_DIRECTORY="docs/releases",
                DEFAULT_BRANCH="main",
                GITHUB_REF=f"refs/tags/{tag}",
            )
            return subprocess.run(
                ["bash", "-c", SCRIPT],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_accepts_reviewed_default_branch_tag(self):
        result = self.validate(reviewed=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_unreviewed_tag_even_with_valid_notes(self):
        self.assertNotEqual(self.validate(reviewed=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()
