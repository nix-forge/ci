"""Build every declared native check in separate, memory-bounded evaluations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from base_check_evidence import validated_base
from ci_partitioning import load_weights, partition_load, partition_names

OPTIONS = [
    "--option",
    "eval-cores",
    "1",
    "--no-allow-import-from-derivation",
    "--no-write-lock-file",
]


def check_derivations(
    system: str,
    names: list[str],
    *,
    output: str = "checks",
    source: str = ".",
    timeout: int | None = None,
) -> dict[str, str | None]:
    """Evaluate only a small selected batch; missing/throwing entries are unknown."""
    literal = json.dumps(json.dumps(names)).replace("${", "\\${")
    expression = (
        "checks: builtins.listToAttrs (map (name: { inherit name; value = "
        "if builtins.hasAttr name checks then "
        "let result = builtins.tryEval ((builtins.getAttr name checks).drvPath); "
        "in if result.success then result.value else null "
        "else null; }) (builtins.fromJSON " + literal + "))"
    )
    values = json.loads(
        subprocess.check_output(
            [
                "nix",
                "eval",
                "--json",
                *OPTIONS,
                source + "#" + output + "." + system,
                "--apply",
                expression,
            ],
            text=True,
            timeout=timeout,
        )
    )
    if not isinstance(values, dict) or set(values) != set(names):
        raise ValueError("Check batch did not return every selected name")
    if any(
        value is not None
        and (
            not isinstance(value, str)
            or not re.fullmatch(r"/nix/store/[a-z0-9]{32}-[^/\n]+\.drv", value)
        )
        for value in values.values()
    ):
        raise ValueError("Check batch returned an invalid derivation path")
    return values


def check_derivation(
    system: str,
    name: str,
    *,
    output: str = "checks",
    source: str = ".",
    timeout: int | None = None,
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
        timeout=timeout,
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
    base_validated: bool = False,
    base_eval_timeout: int = 60,
    partition_count: int = 1,
    partition_index: int = 0,
    weights_path: Path | None = None,
    eval_batch_size: int = 1,
    cache_roots: Path | None = None,
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
    if base_eval_timeout < 0:
        raise ValueError("Base evaluation timeout must be nonnegative")
    if not 1 <= eval_batch_size <= 8:
        raise ValueError("Evaluation batch size must be between one and eight")
    if cache_roots is not None:
        if not cache_roots.is_absolute():
            raise ValueError("Cache roots directory must be absolute")
        cache_roots.mkdir(parents=True, exist_ok=True)
    selector = ".#" + output + "." + system
    options = OPTIONS
    base_source = resolve_base_source(base_revision) if base_validated else None
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
    current_batch: dict[str, str | None] = {}
    base_batch: dict[str, str | None] = {}
    for index, name in enumerate(selected_names):
        if eval_batch_size > 1 and index % eval_batch_size == 0:
            chunk = selected_names[index : index + eval_batch_size]
            try:
                current_batch = check_derivations(system, chunk, output=output)
            except (subprocess.CalledProcessError, ValueError) as error:
                print(
                    f"Current batch unavailable; evaluating its checks separately: {error}",
                    flush=True,
                )
                current_batch = {}
            base_batch = {}
            if base_source is not None:
                try:
                    base_batch = check_derivations(
                        system,
                        chunk,
                        output=output,
                        source=base_source,
                        timeout=base_eval_timeout or None,
                    )
                except (
                    subprocess.CalledProcessError,
                    subprocess.TimeoutExpired,
                    ValueError,
                ) as error:
                    # Do not multiply a failed historical batch's deadline by
                    # retrying each member. Unknown entries rebuild below.
                    print(
                        f"Base batch unavailable; rebuilding its current checks: {error}",
                        flush=True,
                    )
        print(f"::group::Check {name}", flush=True)
        try:
            current = current_batch.get(name)
            target = (current or check_derivation(system, name, output=output)) + "^*"
        except (subprocess.CalledProcessError, ValueError) as error:
            print(f"Check {name} could not be evaluated: {error}", flush=True)
            failed.append(name)
            print("::endgroup::", flush=True)
            continue
        if base_source is not None:
            try:
                base_target = (
                    base_batch.get(name)
                    if eval_batch_size > 1
                    else check_derivation(
                        system,
                        name,
                        output=output,
                        source=base_source,
                        timeout=base_eval_timeout or None,
                    )
                )
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                ValueError,
            ) as error:
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
        build_command = [
            "nix",
            "build",
            *options,
            "--max-jobs",
            "1",
            "--cores",
            str(cores),
            "--show-trace",
            "--print-build-logs",
        ]
        if cache_roots is None:
            build_command.append("--no-link")
        else:
            # Attribute names are arbitrary Unicode, never filesystem names.
            root_name = hashlib.sha256(name.encode()).hexdigest()
            build_command.extend(["--out-link", str(cache_roots / root_name)])
        result = subprocess.run([*build_command, target], check=False)
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
    parser.add_argument("--base-success-checks", default="[]")
    parser.add_argument("--base-eval-timeout", type=int, default=60)
    parser.add_argument("--partition-count", type=int, default=1)
    parser.add_argument("--partition-index", type=int, default=0)
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--eval-batch-size", type=int, default=1)
    parser.add_argument("--cache-roots", type=Path)
    args = parser.parse_args()
    if args.cores < 0:
        parser.error("--cores must be nonnegative")
    try:
        required = json.loads(args.base_success_checks)
        if (
            not isinstance(required, list)
            or len(required) > 64
            or any(
                not isinstance(name, str) or not name or len(name) > 200
                for name in required
            )
            or len(set(required)) != len(required)
        ):
            raise ValueError("Expected a bounded unique list of successful base checks")
    except ValueError:
        parser.error("base-success-checks must be a JSON array of unique check names")
    base_validated = bool(args.base_revision) and validated_base(
        os.environ.get("GITHUB_REPOSITORY", ""),
        args.base_revision,
        required,
        os.environ.get("BASE_CHECK_TOKEN", ""),
    )
    os.environ.pop("BASE_CHECK_TOKEN", None)
    if args.base_revision and not base_validated:
        print(
            "::notice::No successful base evidence; realizing current checks.",
            flush=True,
        )
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
            output.write(f"base-validated={str(base_validated).lower()}\n")
    raise SystemExit(
        run(
            args.system,
            cores=args.cores,
            output=args.output,
            base_revision=args.base_revision,
            base_validated=base_validated,
            base_eval_timeout=args.base_eval_timeout,
            partition_count=args.partition_count,
            partition_index=args.partition_index,
            weights_path=args.weights,
            eval_batch_size=args.eval_batch_size,
            cache_roots=args.cache_roots,
        )
    )


if __name__ == "__main__":
    main()
