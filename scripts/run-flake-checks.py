"""Build every declared native check in separate, memory-bounded evaluations."""

from __future__ import annotations

import argparse
import json
import subprocess


def run(system: str, *, cores: int = 2) -> int:
    """Discover the current check set and report every build failure."""
    selector = ".#checks." + json.dumps(system)
    options = [
        "--option",
        "eval-cores",
        "1",
        "--no-allow-import-from-derivation",
        "--no-write-lock-file",
    ]
    names = json.loads(
        subprocess.check_output(
            [
                "nix",
                "eval",
                "--json",
                *options,
                selector,
                "--apply",
                "builtins.attrNames",
            ],
            text=True,
        )
    )
    if (
        not isinstance(names, list)
        or not names
        or any(not isinstance(name, str) or not name for name in names)
        or len(set(names)) != len(names)
    ):
        raise ValueError("Expected a nonempty, unique list of flake check names")
    failed = []
    for name in sorted(names):
        print(f"::group::Check {name}", flush=True)
        result = subprocess.run(
            [
                "nix",
                "build",
                *options,
                "--max-jobs",
                "1",
                "--cores",
                str(cores),
                "--show-trace",
                "--print-build-logs",
                "--no-link",
                selector + "." + json.dumps(name),
            ],
            check=False,
        )
        print("::endgroup::", flush=True)
        if result.returncode:
            failed.append(name)
    if failed:
        print("Failed checks: " + ", ".join(failed), flush=True)
    return bool(failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", required=True)
    parser.add_argument("--cores", type=int, default=2)
    args = parser.parse_args()
    if args.cores < 0:
        parser.error("--cores must be nonnegative")
    raise SystemExit(run(args.system, cores=args.cores))


if __name__ == "__main__":
    main()
