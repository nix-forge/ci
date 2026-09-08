"""Build every declared native check in separate, memory-bounded evaluations."""

from __future__ import annotations

import argparse
import json
import re
import subprocess

OPTIONS = [
    "--option",
    "eval-cores",
    "1",
    "--no-allow-import-from-derivation",
    "--no-write-lock-file",
]


def check_derivation(system: str, name: str) -> str:
    """Resolve arbitrary attribute names through Nix, not CLI path quoting."""
    # The outer string is ASCII JSON syntax, also a valid Nix string after
    # escaping interpolation. fromJSON decodes every legal Unicode/control name.
    interpolation = "${"
    literal = json.dumps(json.dumps(name)).replace(interpolation, "\\" + interpolation)
    expression = (
        f"checks: (builtins.getAttr (builtins.fromJSON {literal}) checks).drvPath"
    )
    path = subprocess.check_output(
        ["nix", "eval", "--raw", *OPTIONS, ".#checks." + system, "--apply", expression],
        text=True,
    ).strip()
    if not re.fullmatch(r"/nix/store/[a-z0-9]{32}-[^/\n]+\.drv", path):
        raise ValueError("Check evaluation did not return a Nix derivation path")
    return path


def run(system: str, *, cores: int = 2) -> int:
    """Discover the current check set and report every build failure."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", system):
        raise ValueError("Invalid Nix system")
    selector = ".#checks." + system
    options = OPTIONS
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
        try:
            target = check_derivation(system, name) + "^*"
        except (subprocess.CalledProcessError, ValueError) as error:
            print(f"Check {name} could not be evaluated: {error}", flush=True)
            failed.append(name)
            print("::endgroup::", flush=True)
            continue
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
                target,
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
