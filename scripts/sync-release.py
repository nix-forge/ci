"""Check or update shared CI pins in local repositories to a published release."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from repository_inventory import workflow_files

REFERENCE = re.compile(
    r"(?m)^(\s*(?:-\s+)?uses:\s*nix-forge/ci/[^\s@]+)@[^\s#]+"
    r"(?:[ \t]+#[ \t]*v\d+\.\d+\.\d+)?"
)


def release_pin(tag: str | None) -> tuple[str, str]:
    """Resolve GitHub's latest stable release, or a named published release."""

    def api(endpoint: str) -> dict:
        return json.loads(
            subprocess.check_output(
                ["gh", "api", f"repos/nix-forge/ci/{endpoint}"], text=True
            )
        )

    release = api(f"releases/tags/{quote(tag, safe='')}" if tag else "releases/latest")
    version = release["tag_name"]
    if (
        release["draft"]
        or release["prerelease"]
        or not re.fullmatch(r"v\d+\.\d+\.\d+", version)
    ):
        raise ValueError("Select a published stable semver release")
    sha = api(f"commits/{quote(version, safe='')}")["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Release did not resolve to a full commit SHA")
    return version, sha


def updates(root: Path, version: str, sha: str) -> dict[Path, str]:
    """Prepare workflow, composite and template changes without touching other code."""
    inventory = workflow_files(root)
    result = {}
    found = False
    for paths in inventory.values():
        for path in paths:
            text = path.read_text()
            found = found or bool(REFERENCE.search(text))
            updated = REFERENCE.sub(lambda match: f"{match[1]}@{sha} # {version}", text)
            if text != updated:
                result[path] = updated
    if not found:
        raise ValueError(f"No shared CI references found in {root}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument(
        "--release", help="Published tag; defaults to GitHub's latest release"
    )
    parser.add_argument(
        "--check", action="store_true", help="Exit 1 on drift without writing"
    )
    args = parser.parse_args()
    version, sha = release_pin(args.release)
    # Resolve every input before writing, so a mistyped checkout cannot cause a partial rollout.
    changes = {}
    for root in args.roots:
        changes.update(updates(root.resolve(), version, sha))
    print(
        f"{version} {sha}: {len(changes)} files {'outdated' if args.check else 'updated'}"
    )
    for path, content in changes.items():
        print(path)
        if not args.check:
            path.write_text(content)
    if args.check and changes:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
