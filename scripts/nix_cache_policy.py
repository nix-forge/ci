"""Derive cache identities and trusted writer policy without evaluating caller code."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit


def cache_policy(env: dict[str, str], runtime: str) -> dict[str, str]:
    """Separate immutable workload identities from explicit trusted write intent."""
    mode = env.get("CACHE_WRITE_MODE", "auto")
    if mode not in {"auto", "read-only", "seed"}:
        raise ValueError("Cache write mode must be auto, read-only or seed")
    scope = env.get("CACHE_SCOPE", "build")
    partition = env.get("CACHE_PARTITION", "")
    for value in [scope, *([partition] if partition else [])]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value):
            raise ValueError("Cache scope/partition must be short identifiers")
    lock_hash = env.get("CACHE_LOCK_HASH", "")
    if lock_hash and not re.fullmatch(r"[0-9a-fA-F]{64}", lock_hash):
        raise ValueError("Invalid lockfile hash")
    sha = env.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", sha):
        raise ValueError("Invalid source revision")
    default = env.get("DEFAULT_BRANCH", "")
    on_default = bool(default) and env.get("GITHUB_REF") == "refs/heads/" + default
    event = env.get("GITHUB_EVENT_NAME", "")
    allowed = on_default and event in {"push", "workflow_dispatch", "schedule"}
    if mode == "seed" and not allowed:
        raise ValueError("Cache seeds require a trusted default-branch event")
    save = (
        env.get("CACHE_ENABLED") == "true"
        and allowed
        and mode != "read-only"
        and (mode == "seed" or event != "schedule")
    )
    # Use actual installed runtime identity, not a stale installer-version label.
    runtime_id = hashlib.sha256(runtime.encode()).hexdigest()[:16]
    platform = [env.get("RUNNER_OS", ""), env.get("RUNNER_ARCH", "")]
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value) for value in platform):
        raise ValueError("Invalid runner platform")
    # Hash the complete tuple: neither separators inside a scope nor a scope
    # prefix can alias a distinct workload/partition's compatible restore key.
    workload = hashlib.sha256(json.dumps([scope, partition]).encode()).hexdigest()
    prefix = "-".join(["nix-v2", *platform, runtime_id, workload]) + "-"
    locked = prefix + (lock_hash.lower() or "no-lockfiles") + "-"
    return {
        "key": locked + sha.lower(),
        "restore-prefix": locked,
        "compatible-prefix": prefix
        if env.get("CACHE_RESTORE_COMPATIBLE") == "true"
        else "",
        "save": str(save).lower(),
        "started": str(time.monotonic()),
    }


def effective_providers(settings: dict) -> dict:
    """Report only provider origins and public key names, never tokens or URL auth."""

    def values(name):
        value = settings.get(name, {}).get("value", [])
        return value if isinstance(value, list) else []

    providers = []
    for value in values("substituters"):
        parsed = urlsplit(value)
        if parsed.scheme in {"https", "http"} and parsed.hostname:
            providers.append(parsed.scheme + "://" + parsed.hostname)
        else:
            providers.append(parsed.scheme or "local")
    return {
        "substituters": providers,
        "keyNames": [value.split(":", 1)[0] for value in values("trusted-public-keys")],
    }


def main() -> None:
    runtime = subprocess.check_output(["nix", "--version"], text=True).strip()
    result = cache_policy(dict(os.environ), runtime)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.writelines(f"{name}={value}\n" for name, value in result.items())
    settings = json.loads(
        subprocess.check_output(["nix", "config", "show", "--json"], text=True)
    )
    print(
        json.dumps(
            {
                "runtime": runtime,
                "writeEligible": result["save"],
                **effective_providers(settings),
            }
        )
    )


if __name__ == "__main__":
    main()
