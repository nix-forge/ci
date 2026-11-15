"""Require successful GitHub Actions evidence before reusing base check results."""

from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def successful_checks(checks: list[dict], revision: str, required: list[str]) -> bool:
    """Accept only the latest owning Actions checks for the exact base commit."""
    if not required:
        return False
    latest = {}
    for check in checks:
        if (
            not isinstance(check, dict)
            or check.get("head_sha") != revision
            or not isinstance(check.get("id"), int)
            or not isinstance(check.get("app"), dict)
            or check["app"].get("slug") != "github-actions"
        ):
            continue
        name = check.get("name")
        if name not in required:
            continue
        if name not in latest or check["id"] > latest[name]["id"]:
            latest[name] = check
    return all(
        name in latest
        and latest[name].get("status") == "completed"
        and latest[name].get("conclusion") == "success"
        for name in required
    )


def validated_base(
    repository: str, revision: str, required: list[str], token: str = ""
) -> bool:
    """Read bounded GitHub evidence; unknown, failed and incomplete bases rebuild."""
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9_.-]+", repository)
        or repository.rsplit("/", 1)[-1] in {".", ".."}
        or not re.fullmatch(r"[0-9a-fA-F]{40}", revision)
        or not required
    ):
        return False
    revision = revision.lower()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    checks = []
    try:
        # A truncated inventory cannot establish that the latest attempt passed.
        for page in range(1, 6):
            url = f"https://api.github.com/repos/{repository}/commits/{revision}/check-runs?filter=latest&per_page=100&page={page}"
            with urlopen(Request(url, headers=headers), timeout=10) as response:
                payload = json.load(response)
            entries = payload.get("check_runs")
            if not isinstance(entries, list):
                return False
            checks.extend(entries)
            if len(entries) < 100:
                return successful_checks(checks, revision, required)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError, AttributeError):
        print(
            "::notice::Base success evidence unavailable; realizing current checks.",
            flush=True,
        )
    return False
