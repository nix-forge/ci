"""Exercise release signer selection against workflow input, without GitHub."""

import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts/release-builder-sha.sh"


class ReleaseBuilderTests(unittest.TestCase):
    def resolve(self, text):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "release.yml"
            workflow.write_text(text)
            return subprocess.run(
                ["bash", str(SCRIPT), str(workflow)],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_tracks_the_builder_update(self):
        for sha in ("a" * 40, "b" * 40):
            result = self.resolve(
                f"    uses: nix-forge/ci/.github/workflows/slsa-source-release.yml@{sha} # reviewed builder\n"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), sha)

    def test_rejects_missing_mutable_or_ambiguous_builders(self):
        pinned = (
            f"uses: nix-forge/ci/.github/workflows/slsa-source-release.yml@{'a' * 40}\n"
        )
        for text in ("", pinned.replace("a" * 40, "main"), pinned * 2):
            with self.subTest(text=text):
                self.assertNotEqual(self.resolve(text).returncode, 0)


if __name__ == "__main__":
    unittest.main()
