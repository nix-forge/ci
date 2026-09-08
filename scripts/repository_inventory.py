"""Discover tracked Nix inputs and workflow files without executing repository code."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def tracked_files(root: Path, *, submodules: bool = False) -> list[Path]:
    """List Git-owned files, optionally including initialized submodules."""
    command = ["git", "-C", str(root), "ls-files", "-z"]
    if submodules:
        command.append("--recurse-submodules")
    return [
        Path(name)
        for name in subprocess.check_output(command).decode().split("\0")
        if name
    ]


def lockfiles(root: Path, *, submodules: bool = False) -> list[str]:
    """Require a tracked lockfile for every tracked flake."""
    files = set(tracked_files(root, submodules=submodules))
    expected = {
        path.with_name("flake.lock") for path in files if path.name == "flake.nix"
    }
    missing = sorted(str(path) for path in expected - files)
    if missing:
        raise ValueError("Tracked flakes are missing lockfiles: " + ", ".join(missing))
    return sorted(str(path) for path in expected)


def partitions(root: Path) -> list[str]:
    """Find nested flake sources, including partitions within submodules."""
    result = []
    for lockfile in lockfiles(root, submodules=True):
        directory = Path(lockfile).parent
        path = root / directory
        if directory == Path(".") or (path / ".git").exists():
            continue
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Partition escapes the checkout: {directory}")
        result.append(str(directory))
    return sorted(set(result))


def workflow_files(root: Path) -> dict[str, list[Path]]:
    """Use the same complete inventory for syntax and semantic validation."""

    def yaml_files(directory: Path) -> list[Path]:
        return sorted(p for p in directory.glob("*") if p.suffix in {".yml", ".yaml"})

    return {
        "workflows": yaml_files(root / ".github/workflows"),
        "templates": yaml_files(root / "workflow-templates"),
        "actions": sorted(
            p
            for directory in (root / "actions", root / ".github/actions")
            for p in directory.rglob("*")
            if p.name in {"action.yml", "action.yaml"} and p.is_file()
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "kind", choices=["lockfiles", "partitions", "workflows", "actions"]
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--null", action="store_true")
    parser.add_argument("--materialize", action="store_true")
    args = parser.parse_args()
    if args.kind == "lockfiles":
        paths = lockfiles(args.root)
    elif args.kind == "partitions":
        paths = partitions(args.root)
    else:
        inventory = workflow_files(args.root)
        kinds = ["workflows", "templates"] if args.kind == "workflows" else ["actions"]
        paths = [str(path) for kind in kinds for path in inventory[kind]]
    if args.materialize:
        if args.kind != "partitions":
            parser.error("--materialize requires partitions")
        for path in paths:
            subprocess.run(
                ["nix", "store", "add-path", "--", path], cwd=args.root, check=True
            )
    elif args.null:
        print("\0".join(paths), end="\0" if paths else "")
    else:
        print(json.dumps(paths))


if __name__ == "__main__":
    main()
