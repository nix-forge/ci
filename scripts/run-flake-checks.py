"""Build every declared native check in separate, memory-bounded evaluations."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from ci_partitioning import load_weights, partition_load, partition_names

OPTIONS = [
    "--option",
    "eval-cores",
    "1",
    "--no-allow-import-from-derivation",
    "--no-write-lock-file",
]


def check_derivation(
    system: str, name: str, *, output: str = "checks", source: str = "."
) -> str:
    """Resolve arbitrary attribute names through Nix, not CLI path quoting."""
    # The outer string is ASCII JSON syntax, also a valid Nix string after
    # escaping interpolation. fromJSON decodes every legal Unicode/control name.
    interpolation = "${"
    literal = json.dumps(json.dumps(name)).replace(interpolation, "\\" + interpolation)
    expression = (
        f"checks: (builtins.getAttr (builtins.fromJSON {literal}) checks).drvPath"
    )
    path = subprocess.check_output(
        [
            "nix",
            "eval",
            "--raw",
            *OPTIONS,
            source + "#" + output + "." + system,
            "--apply",
            expression,
        ],
        text=True,
    ).strip()
    if not re.fullmatch(r"/nix/store/[a-z0-9]{32}-[^/\n]+\.drv", path):
        raise ValueError("Check evaluation did not return a Nix derivation path")
    return path


def resolve_base_source(revision: str | None) -> str | None:
    """Return a local Git flake URL when the requested base commit is available."""
    if not revision or not revision.strip("0"):
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise ValueError("Invalid base revision")

    def available() -> bool:
        return not subprocess.run(
            ["git", "cat-file", "-e", revision + "^{commit}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode

    if not available():
        print("::notice::Fetching the base revision for native check comparison.")
        subprocess.run(
            ["git", "fetch", "--no-tags", "--depth=1", "origin", revision],
            check=False,
        )
    if not available():
        print("::notice::Base revision unavailable; rebuilding every native check.")
        return None
    root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
    return (
        "git+"
        + Path(root).resolve().as_uri()
        + "?shallow=1&submodules=1&rev="
        + revision.lower()
    )


def run(
    system: str,
    *,
    cores: int = 2,
    output: str = "checks",
    base_revision: str | None = None,
    partition_count: int = 1,
    partition_index: int = 0,
    weights_path: Path | None = None,
) -> int:
    """Discover the current check set and report every build failure."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", system):
        raise ValueError("Invalid Nix system")
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]*", output):
        raise ValueError("Invalid flake check output")
    if partition_count < 1:
        raise ValueError("Partition count must be positive")
    if partition_index < 0 or partition_index >= partition_count:
        raise ValueError("Partition index must be within the partition count")
    selector = ".#" + output + "." + system
    options = OPTIONS
    base_source = resolve_base_source(base_revision)
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
    if weights_path is None:
        selected_names = sorted(names)[partition_index::partition_count]
    else:
        default_weight, weights = load_weights(weights_path)
        selected_names = partition_names(
            names, partition_count, default_weight=default_weight, weights=weights
        )[partition_index]
        print(
            f"Estimated partition load: {partition_load(selected_names, default_weight=default_weight, weights=weights):g}",
            flush=True,
        )
    if not selected_names:
        raise ValueError("Partition selects no flake checks")
    if partition_count > 1:
        print(
            f"Selected partition {partition_index + 1}/{partition_count}: "
            + ", ".join(selected_names),
            flush=True,
        )
    failed = []
    for name in selected_names:
        print(f"::group::Check {name}", flush=True)
        try:
            target = check_derivation(system, name, output=output) + "^*"
        except (subprocess.CalledProcessError, ValueError) as error:
            print(f"Check {name} could not be evaluated: {error}", flush=True)
            failed.append(name)
            print("::endgroup::", flush=True)
            continue
        if base_source is not None:
            try:
                base_target = check_derivation(
                    system, name, output=output, source=base_source
                )
            except (subprocess.CalledProcessError, ValueError) as error:
                print(
                    f"Base check {name} could not be evaluated; rebuilding: {error}",
                    flush=True,
                )
            else:
                if target.removesuffix("^*") == base_target:
                    print(
                        f"Check {name} has the same derivation as the base; no rebuild needed.",
                        flush=True,
                    )
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
    parser.add_argument("--output", default="checks")
    parser.add_argument("--cores", type=int, default=2)
    parser.add_argument("--base-revision")
    parser.add_argument("--partition-count", type=int, default=1)
    parser.add_argument("--partition-index", type=int, default=0)
    parser.add_argument("--weights", type=Path)
    args = parser.parse_args()
    if args.cores < 0:
        parser.error("--cores must be nonnegative")
    raise SystemExit(
        run(
            args.system,
            cores=args.cores,
            output=args.output,
            base_revision=args.base_revision,
            partition_count=args.partition_count,
            partition_index=args.partition_index,
            weights_path=args.weights,
        )
    )


if __name__ == "__main__":
    main()
