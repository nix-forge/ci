"""Admit passing bot PRs and dispatch CI suppressed by GITHUB_TOKEN.

GitHub API metadata and immutable workflow blobs prove pin-only changes.
No PR code, artifacts, or commands are executed.
"""

# Fixed GitHub CLI invocations use argv without a shell; progress goes to Actions logs.
# ruff: file-ignore[suspicious-subprocess-import, subprocess-without-shell-equals-true, print]
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
import subprocess
import time
from typing import Any
from urllib.parse import quote

import yaml

REPOSITORY = os.environ["GH_REPO"]
WORKFLOWS = json.loads(os.environ["QUEUE_WORKFLOWS"])
DEFAULT_BRANCH = os.environ.get("DEFAULT_BRANCH", "main")
AUTOMATION_BRANCH = os.environ.get("AUTOMATION_BRANCH", "")
BASE_SHA_WORKFLOWS = json.loads(os.environ.get("BASE_SHA_WORKFLOWS", "[]"))
QUEUE_PREFIX = f"refs/heads/gh-readonly-queue/{DEFAULT_BRANCH}/"
ADMISSION = "Merge queue admission"
SOURCE_CHECKS = json.loads(os.environ.get("PACKAGE_SOURCE_CHECKS", "{}"))
SOURCE_REPORT = "Mirrored package source job"
SOURCE_HEAD = os.environ.get("SOURCE_HEAD_SHA", "")
SOURCE_ACTIVITY = os.environ.get("SOURCE_ACTIVITY", "")
SOURCE_CACHE: dict[str, Any] = {}
SOURCE_EVIDENCE: dict[str, dict[str, Any]] = {}
PAGE_SIZE = 100
GH = shutil.which("gh") or "/usr/bin/gh"


def api(path: str, method: str = "GET", data: object = None) -> Any:  # ruff: ignore[any-type]
    """Decode an arbitrary GitHub REST or GraphQL JSON response.

    Returns:
        The decoded response, or None for an empty response.

    """
    command = [GH, "api", path, "--method", method]
    if data is not None:
        command += ["--input", "-"]
    result = subprocess.run(
        command,
        input=json.dumps(data) if data is not None else None,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def graphql(query: str, **variables: str) -> dict[str, Any]:
    """Send a parameterized GraphQL operation and reject API errors.

    Returns:
        The data object returned by GitHub.

    Raises:
        RuntimeError: GitHub rejected the operation.

    """
    result = api("graphql", "POST", {"query": query, "variables": variables})
    if result.get("errors"):
        message = json.dumps(result["errors"])
        raise RuntimeError(message)
    return result["data"]


def admit(pr: dict[str, Any]) -> bool:
    """Admit one eligible PR once its required checks pass.

    Returns:
        Whether this trusted PR is now queued and may need a validation ref.

    """
    source = bool(SOURCE_CHECKS) and source_identity(pr)
    source_statuses = None
    if source:
        sha = pr["head"]["sha"]
        if SOURCE_HEAD and SOURCE_HEAD != sha:
            return False
        source_statuses = read_statuses(sha)
        if not SOURCE_HEAD and any(
            status["context"] == ADMISSION for status in source_statuses.values()
        ):
            state = graphql(
                "query($id:ID!) { node(id:$id) { ... on PullRequest { mergeQueueEntry { id } } } }",
                id=pr["node_id"],
            )["node"]
            return bool(state["mergeQueueEntry"])
        if not reconcile_source(pr, source_statuses):
            if SOURCE_HEAD == sha and any(
                status["context"] == ADMISSION and status.get("state") == "success"
                for status in source_statuses.values()
            ):
                fresh = api(f"repos/{REPOSITORY}/pulls/{pr['number']}")
                current = api(
                    f"repos/{REPOSITORY}/git/ref/heads/{quote(pr['head']['ref'], safe='/')}"
                )
                if (
                    source_identity(fresh)
                    and fresh["head"]["sha"] == sha
                    and current["object"]["sha"] == sha
                ):
                    api(
                        f"repos/{REPOSITORY}/statuses/{sha}",
                        "POST",
                        {
                            "state": "pending",
                            "context": ADMISSION,
                            "description": "Source validation changed; current successful jobs required",
                        },
                    )
            return False
    trusted = (
        source
        or pr["user"]["login"] == "dependabot[bot]"
        or (
            bool(AUTOMATION_BRANCH)
            and pr["user"]["login"] == "github-actions[bot]"
            and pr["head"]["ref"] == AUTOMATION_BRANCH
        )
    )
    if (
        not trusted
        or pr["draft"]
        or (pr["head"]["repo"] or {}).get("full_name") != REPOSITORY
    ):
        return False
    number, sha = pr["number"], pr["head"]["sha"]
    # Workflow and privileged automation updates require a human decision.
    page = 1
    while True:
        files = api(
            f"repos/{REPOSITORY}/pulls/{number}/files?per_page={PAGE_SIZE}&page={page}"
        )
        if any(
            automation_file(item)
            and not (
                pr["user"]["login"] == "dependabot[bot]" and safe_pin_update(pr, item)
            )
            for item in files
        ):
            print(f"PR #{number}: automation changes require maintainer review")
            return False
        if len(files) < PAGE_SIZE:
            break
        page += 1
    state = graphql(
        "query($id:ID!) { node(id:$id) { ... on PullRequest { mergeQueueEntry { id } } } }",
        id=pr["node_id"],
    )["node"]
    if state["mergeQueueEntry"]:
        if (
            source
            and SOURCE_HEAD == sha
            and any(
                status["context"] == ADMISSION and status.get("state") != "success"
                for status in (source_statuses or {}).values()
            )
        ):
            if not source_runs_current(pr) or source_contract(pr) is None:
                reconcile_source(pr, allow_success=False)
                return False
            api(
                f"repos/{REPOSITORY}/statuses/{sha}",
                "POST",
                {
                    "state": "success",
                    "context": ADMISSION,
                    "description": "This PR head remains queued with current successful source jobs",
                },
            )
        return True
    # Once admitted, a rejected head needs a fix or deliberate requeue.
    # Never keep rebuilding the same failing merge group indefinitely.
    statuses = (
        list(source_statuses.values())
        if source_statuses is not None
        else api(f"repos/{REPOSITORY}/commits/{sha}/status")["statuses"]
    )
    if any(status["context"] == ADMISSION for status in statuses):
        print(f"PR #{number}: already admitted this head; leave rejection for review")
        return False
    checks = subprocess.run(
        [
            GH,
            "pr",
            "checks",
            str(number),
            "--repo",
            REPOSITORY,
            "--required",
            "--json",
            "bucket",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    # CLI exit 1 also covers permission/API errors. Only a real check list
    # can mean a failing check; surface malformed output instead of hiding it.
    if not checks.stdout.strip().startswith("["):
        checks.check_returncode()
    if checks.returncode in {1, 8}:
        print(f"PR #{number}: required checks are not passing yet")
        return False
    checks.check_returncode()
    required = json.loads(checks.stdout)
    if not required or any(
        check["bucket"] not in {"pass", "skipping"} for check in required
    ):
        return False
    try:
        graphql(
            "mutation($id:ID!,$sha:GitObjectID!) { enqueuePullRequest(input:"
            "{pullRequestId:$id,expectedHeadOid:$sha}) { mergeQueueEntry { id } } }",
            id=pr["node_id"],
            sha=sha,
        )
    except (subprocess.CalledProcessError, RuntimeError) as error:
        # GitHub also enforces review, conflict, and branch rules here.
        print(
            f"PR #{number}: admission refused: {getattr(error, 'stderr', None) or str(error)}"
        )
        return False
    api(
        f"repos/{REPOSITORY}/statuses/{sha}",
        "POST",
        {
            "state": "success",
            "context": ADMISSION,
            "description": "This PR head was admitted to the merge queue",
        },
    )
    print(f"PR #{number}: admitted {sha}")

    return True


def automation_file(item: dict[str, str]) -> bool:
    """Require human admission for automation, including rename source paths."""
    paths = (item["filename"], item.get("previous_filename", ""))
    prefixes = (".github/", "actions/", "scripts/", "workflow-templates/")
    return any(path.startswith(prefixes) for path in paths)


MAX_PIN_FILE_BYTES = 1024 * 1024
PIN_LINE = re.compile(
    r"(?P<prefix> +(?:- +)?uses: +)(?P<action>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)"
    r"@(?P<sha>[0-9a-f]{40})(?P<comment> *(?:#[^\r\n]*)?)(?P<ending>\n?)"
)
VERSION_COMMENT = re.compile(r" +# v?[0-9]+(?:\.[0-9]+)*(?:[-+][A-Za-z0-9.-]+)?")


def safe_pin_update(pr: dict[str, Any], item: dict[str, str]) -> bool:
    """Prove a modified metadata file changes only existing external SHA pins.

    Read complete immutable Git blobs, never execute PR code or trust the
    possibly truncated diff returned by the pull-request files endpoint.
    API errors abort reconciliation before any admission.

    Returns:
        Whether every changed byte is an allowed SHA or version comment.

    """
    filename = item["filename"]
    if item.get("status") != "modified" or item.get("previous_filename"):
        return False
    if any(part in {"", ".", ".."} for part in filename.split("/")):
        return False
    if not re.fullmatch(
        r"(?:\.github/workflows/[^/]+\.ya?ml|workflow-templates/[^/]+\.ya?ml|"
        r"(?:\.github/)?actions/(?:[^/]+/)+action\.ya?ml)",
        filename,
    ):
        return False
    contents = []
    for side in ("base", "head"):
        ref = pr.get(side, {}).get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", ref):
            return False
        tree = api(f"repos/{REPOSITORY}/git/trees/{ref}?recursive=1")
        if tree.get("truncated") is not False:
            return False
        entries = [entry for entry in tree.get("tree", []) if entry["path"] == filename]
        if len(entries) != 1:
            return False
        entry = entries[0]
        if entry.get("mode") != "100644" or entry.get("type") != "blob":
            return False
        blob = api(f"repos/{REPOSITORY}/git/blobs/{entry['sha']}")
        size = blob.get("size")
        if (
            blob.get("encoding") != "base64"
            or not isinstance(size, int)
            or not 0 < size <= MAX_PIN_FILE_BYTES
            or not isinstance(blob.get("content"), str)
            or len(blob["content"]) > MAX_PIN_FILE_BYTES * 2
        ):
            return False
        try:
            raw = base64.b64decode("".join(blob["content"].split()), validate=True)
            text = raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return False
        if len(raw) != size or "\0" in text or "\r" in text:
            return False
        contents.append(text.splitlines(keepends=True))
    before, after = contents
    if len(before) != len(after):
        return False
    old_lines = action_pin_lines("".join(before), filename)
    new_lines = action_pin_lines("".join(after), filename)
    if old_lines is None or new_lines is None:
        return False
    changed = False
    for index, (old, new) in enumerate(zip(before, after, strict=True)):
        if old == new:
            continue
        if index not in old_lines or index not in new_lines:
            return False
        old_pin, new_pin = PIN_LINE.fullmatch(old), PIN_LINE.fullmatch(new)
        if old_pin is None or new_pin is None:
            return False
        if any(old_pin[key] != new_pin[key] for key in ("prefix", "action", "ending")):
            return False
        if old_pin["sha"] == new_pin["sha"]:
            return False
        if old_pin["comment"] != new_pin["comment"] and not all(
            VERSION_COMMENT.fullmatch(pin["comment"]) for pin in (old_pin, new_pin)
        ):
            return False
        changed = True
    return changed


def action_pin_lines(text: str, filename: str) -> set[int] | None:
    """Locate actual job and step action fields without constructing YAML objects.

    Returns:
        Scalar value line numbers, or None for ambiguous or invalid YAML.

    """

    def fields(node: yaml.Node) -> dict[str, yaml.Node]:
        if not isinstance(node, yaml.MappingNode):
            raise TypeError
        result = {}
        for key, value in node.value:
            if not isinstance(key, yaml.ScalarNode) or key.value in result:
                raise ValueError
            result[key.value] = value
        return result

    lines: set[int] = set()

    def action(mapping: dict[str, yaml.Node]) -> None:
        node = mapping.get("uses")
        if (
            isinstance(node, yaml.ScalarNode)
            and "run" not in mapping
            and node.start_mark.line == node.end_mark.line
            and re.fullmatch(
                r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*@[0-9a-f]{40}",
                node.value,
            )
        ):
            lines.add(node.start_mark.line)

    def steps(node: yaml.Node | None) -> None:
        if node is None:
            return
        if not isinstance(node, yaml.SequenceNode):
            raise TypeError
        for step in node.value:
            action(fields(step))

    try:
        # Aliases may share an action scalar with runtime env or command data.
        # Reject their documents instead of assuming the scalar has one use.
        if any(
            isinstance(token, (yaml.AnchorToken, yaml.AliasToken))
            for token in yaml.scan(text)
        ):
            return None
        root = yaml.compose(text, Loader=yaml.BaseLoader)
        mapping = fields(root)
        if filename.endswith(("/action.yml", "/action.yaml")):
            runs = fields(mapping["runs"])
            using = runs.get("using")
            if not isinstance(using, yaml.ScalarNode) or using.value != "composite":
                return None
            steps(runs.get("steps"))
        else:
            for job in fields(mapping["jobs"]).values():
                definition = fields(job)
                action(definition)
                steps(definition.get("steps"))
    except (yaml.YAMLError, ValueError, TypeError, KeyError, RecursionError):
        return None
    return lines


def wait_for_source_run(run_id: str) -> None:
    """Wait briefly for the dispatch callback's own workflow to finish.

    Raises:
        ValueError: The supplied run ID is not an ASCII integer.

    """
    if not run_id:
        return
    if not run_id.isascii() or not run_id.isdecimal():
        message = "Source run ID must be an ASCII integer"
        raise ValueError(message)
    for _ in range(12):
        run = api(f"repos/{REPOSITORY}/actions/runs/{run_id}")
        if run["status"] == "completed":
            return
        time.sleep(5)
    print(f"Source run {run_id} is still running; reconcile other ready entries")


class ObsoleteSourceNotification(RuntimeError):
    """A verified producer callback no longer describes current source work."""


def source_notification(
    run_id: str, attempt: str, activity: str
) -> tuple[str, str] | None:
    """Bind an explicit source callback to its real current producer identity.

    Returns:
        Exact producer head and current activity, or None for legacy queue callbacks.

    Raises:
        ValueError: Source notification metadata is missing, tampered or stale.
        TypeError: Producer workflow path is not a string.
        ObsoleteSourceNotification: A verified attempt or branch has advanced or vanished.

    """
    if not run_id or not SOURCE_CHECKS:
        return None
    if not run_id.isascii() or not run_id.isdecimal() or int(run_id) <= 0:
        raise ValueError("Source run ID must be a positive ASCII integer")
    run = api(f"repos/{REPOSITORY}/actions/runs/{run_id}")
    branch = run.get("head_branch", "")
    if not isinstance(branch, str) or not branch.startswith(
        "automation/package-updates-"
    ):
        if activity or attempt:
            raise ValueError(
                "Explicit source callback requires a package updater branch"
            )
        return None
    if (
        activity not in {"in_progress", "completed"}
        or not attempt.isascii()
        or not attempt.isdecimal()
        or int(attempt) <= 0
        or re.fullmatch(r"automation/package-updates-[a-z0-9][a-z0-9+._-]*", branch)
        is None
    ):
        raise ValueError("Invalid source callback activity, attempt or branch")
    path = run.get("path", "")
    if not isinstance(path, str):
        raise TypeError("Source callback workflow path is invalid")
    workflow = path.removeprefix(".github/workflows/")
    if workflow not in WORKFLOWS or workflow not in SOURCE_CHECKS:
        raise ValueError("Source callback workflow is outside the required contract")
    metadata = source_read(f"repos/{REPOSITORY}/actions/workflows/{workflow}")

    def valid(producer: dict[str, Any]) -> bool:
        return (
            type(producer.get("id")) is int
            and producer["id"] == int(run_id)
            and type(producer.get("workflow_id")) is int
            and type(metadata.get("id")) is int
            and producer["workflow_id"] == metadata.get("id")
            and metadata.get("state") == "active"
            and metadata.get("path") == f".github/workflows/{workflow}"
            and producer.get("path") == metadata.get("path")
            and producer.get("repository", {}).get("full_name") == REPOSITORY
            and producer.get("head_repository", {}).get("full_name") == REPOSITORY
            and producer.get("event") == "workflow_dispatch"
            and producer.get("head_branch") == branch
            and isinstance(producer.get("head_sha"), str)
            and re.fullmatch(r"[0-9a-f]{40}", producer["head_sha"]) is not None
            and type(producer.get("run_attempt")) is int
            and producer["run_attempt"] > 0
            and producer.get("status")
            in {"queued", "in_progress", "completed", "waiting", "pending", "requested"}
        )

    if not valid(run):
        raise ValueError("Source callback producer identity does not match")
    sha = run["head_sha"]
    if run["run_attempt"] < int(attempt):
        raise ValueError("Source callback names a future producer attempt")
    if run["run_attempt"] > int(attempt):
        original = api(f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}")
        if (
            not valid(original)
            or original["run_attempt"] != int(attempt)
            or original["head_sha"] != sha
        ):
            raise ValueError(
                "Older source callback does not match its immutable producer"
            )
        raise ObsoleteSourceNotification("Producer has a newer validated attempt")
    if activity == "completed":
        wait_for_source_run(run_id)
        run = api(f"repos/{REPOSITORY}/actions/runs/{run_id}")
        if not valid(run) or run["head_sha"] != sha:
            raise ValueError(
                "Source callback producer changed while awaiting completion"
            )
        if run["run_attempt"] > int(attempt):
            raise ObsoleteSourceNotification(
                "Producer attempt advanced during completion wait"
            )
        if run["run_attempt"] != int(attempt):
            raise ValueError("Source callback producer attempt moved backwards")
    try:
        ref = api(f"repos/{REPOSITORY}/git/ref/heads/{quote(branch, safe='/')}")
    except subprocess.CalledProcessError as error:
        if "HTTP 404" not in (error.stderr or ""):
            raise
        raise ObsoleteSourceNotification(
            "Verified source producer branch no longer exists"
        ) from error
    current_sha = ref.get("object", {}).get("sha")
    if (
        not isinstance(current_sha, str)
        or re.fullmatch(r"[0-9a-f]{40}", current_sha) is None
    ):
        raise ValueError("Source producer branch has invalid current metadata")
    if current_sha != sha:
        raise ObsoleteSourceNotification("Verified source producer branch advanced")
    # A delayed start callback must not invalidate an already completed attempt.
    return sha, "completed" if run["status"] == "completed" else "in_progress"


def front_queue_entry() -> dict[str, Any] | None:
    """Read the live front entry without assuming the queue ref naming scheme.

    Returns:
        The entry, whose head may still be pending, or None for an empty queue.

    """
    owner, name = REPOSITORY.split("/", 1)
    repository = graphql(
        "query($owner:String!,$name:String!,$branch:String!) { repository(owner:$owner,name:$name) { "
        "mergeQueue(branch:$branch) { entries(first:1) { nodes { headCommit { oid } } } } } }",
        owner=owner,
        name=name,
        branch=DEFAULT_BRANCH,
    )["repository"]
    merge_queue = repository["mergeQueue"]
    entries = merge_queue["entries"]["nodes"] if merge_queue else []
    return entries[0] if entries else None


def source_identity(pr: dict[str, Any]) -> bool:
    """Limit opt-in source reconciliation to the package updater's bot PRs.

    Returns:
        Whether identity and namespace are eligible for immutable inspection.

    """
    return (
        pr.get("state") == "open"
        and not pr.get("draft", True)
        and pr.get("user", {}).get("id") == 41898282
        and pr["user"].get("type") == "Bot"
        and pr["user"].get("login") == "github-actions[bot]"
        and (pr.get("head", {}).get("repo") or {}).get("full_name") == REPOSITORY
        and (pr.get("base", {}).get("repo") or {}).get("full_name") == REPOSITORY
        and pr["base"].get("ref") == DEFAULT_BRANCH
        and re.fullmatch(
            r"automation/package-updates-[a-z0-9][a-z0-9+._-]*",
            pr["head"].get("ref", ""),
        )
        is not None
        and re.fullmatch(r"[0-9a-f]{40}", pr["head"].get("sha", "")) is not None
    )


def source_read(path: str) -> Any:  # ruff: ignore[any-type]
    """Cache immutable source objects and stable workflow identity within one pass.

    Returns:
        The authoritative response; current refs and latest runs are never cached.

    """
    if path not in SOURCE_CACHE:
        SOURCE_CACHE[path] = api(path)
    return SOURCE_CACHE[path]


def source_contract(pr: dict[str, Any]) -> str | None:
    """Prove the real source parent, owned paths and current workflow identities.

    Returns:
        The exact protected base, or None for an untrusted or stale source.

    """
    sha = pr["head"]["sha"]
    commit = source_read(f"repos/{REPOSITORY}/git/commits/{sha}")
    parents = commit.get("parents", [])
    if len(parents) != 1:
        return None
    base = parents[0]["sha"]
    if re.fullmatch(r"[0-9a-f]{40}", base) is None:
        return None
    protected = api(
        f"repos/{REPOSITORY}/git/ref/heads/{quote(DEFAULT_BRANCH, safe='')}"
    )["object"]["sha"]
    comparison = source_read(f"repos/{REPOSITORY}/compare/{base}...{protected}")
    if comparison.get("status") not in {"ahead", "identical"}:
        return None
    changes = source_read(f"repos/{REPOSITORY}/compare/{base}...{sha}")
    files = changes.get("files", [])
    package = pr["head"]["ref"].removeprefix("automation/package-updates-")
    prefix = f"pkgs/by-name/{package[:2]}/{package}/"
    if (
        changes.get("status") != "ahead"
        or changes.get("total_commits") != 1
        or not files
        or len(files) >= 300
        or not any(item["filename"].startswith(prefix) for item in files)
        or any(
            item.get("status") == "renamed"
            or not (
                item["filename"].startswith(prefix)
                or item["filename"] == "docs/catalog.md"
            )
            for item in files
        )
    ):
        return None
    trees = []
    for revision in (sha, protected):
        tree = source_read(f"repos/{REPOSITORY}/git/trees/{revision}?recursive=1")
        if tree.get("truncated") is not False:
            return None
        trees.append({item["path"]: item for item in tree["tree"]})
    for workflow in WORKFLOWS:
        path = f".github/workflows/{workflow}"
        old, current = (tree.get(path, {}) for tree in trees)
        if (
            not old.get("sha")
            or old.get("sha") != current.get("sha")
            or old.get("mode") != "100644"
            or current.get("mode") != "100644"
            or old.get("type") != "blob"
            or current.get("type") != "blob"
        ):
            return None
    return base


def source_run(
    pr: dict[str, Any], workflow: str
) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    """Select the latest real exact-head run, proving approval-only exclusions.

    Returns:
        Current run metadata and optional already-read jobs, or None.

    """
    sha, branch = pr["head"]["sha"], pr["head"]["ref"]
    metadata = source_read(f"repos/{REPOSITORY}/actions/workflows/{workflow}")
    path = f".github/workflows/{workflow}"
    if metadata.get("path") != path or metadata.get("state") != "active":
        return None
    response = api(
        f"repos/{REPOSITORY}/actions/workflows/{workflow}/runs?head_sha={sha}&per_page={PAGE_SIZE}"
    )
    if (
        not isinstance(response.get("total_count"), int)
        or not 0 <= response["total_count"] <= PAGE_SIZE
        or not isinstance(response.get("workflow_runs"), list)
    ):
        return None
    for run in response["workflow_runs"]:
        if not (
            run.get("workflow_id") == metadata["id"]
            and run.get("path") == path
            and run.get("head_sha") == sha
            and run.get("head_branch") == branch
            and run.get("repository", {}).get("full_name") == REPOSITORY
            and run.get("head_repository", {}).get("full_name") == REPOSITORY
            and run.get("event") in {"pull_request", "workflow_dispatch"}
            and isinstance(run.get("id"), int)
            and run["id"] > 0
            and isinstance(run.get("run_attempt"), int)
            and run["run_attempt"] > 0
        ):
            continue
        batch = None
        if (
            run.get("status") == "completed"
            and run.get("conclusion") == "action_required"
        ):
            batch = api(
                f"repos/{REPOSITORY}/actions/runs/{run['id']}/jobs?filter=latest&per_page={PAGE_SIZE}"
            )
            if batch.get("total_count") == 0 and batch.get("jobs") == []:
                continue
        return run.copy(), batch
    return None


def source_runs_current(pr: dict[str, Any]) -> bool:
    """Reject any producer run or attempt that changed after collection.

    Returns:
        Whether every collected run still has its exact inspected identity/state.

    """
    for workflow, expected in SOURCE_EVIDENCE[pr["head"]["sha"]]["runs"].items():
        selected = source_run(pr, workflow)
        if selected is None or any(
            selected[0].get(key) != expected.get(key)
            for key in ("id", "run_attempt", "head_sha", "status", "conclusion")
        ):
            return False
    return True


def source_jobs(
    pr: dict[str, Any], statuses: dict[str, Any] | None = None
) -> list[dict[str, Any]] | None:
    """Collect actual current jobs, or reuse our verified unchanged-run attestation.

    Returns:
        All required successful jobs, or None for incomplete/failed evidence.

    """
    sha = pr["head"]["sha"]
    evidence: dict[str, Any] = {"runs": {}, "failures": []}
    SOURCE_EVIDENCE[sha] = evidence
    jobs: list[dict[str, Any]] = []
    failures = {"failure", "timed_out", "cancelled"}
    for workflow in WORKFLOWS:
        selected = source_run(pr, workflow)
        if selected is None:
            return None
        run, batch = selected
        evidence["runs"][workflow] = run
        if run.get("status") != "completed":
            return None
        previous = (
            [statuses.get(context, {}) for context in SOURCE_CHECKS[workflow]]
            if statuses
            else []
        )
        description = f"{SOURCE_REPORT} result; attempt {run['run_attempt']}"
        prefix = f"https://github.com/{REPOSITORY}/actions/runs/{run['id']}/job/"
        if (
            run.get("conclusion") == "success"
            and previous
            and all(
                status.get("state") == "success"
                and status.get("description") == description
                and status.get("creator", {}).get("id") == 41898282
                and re.fullmatch(
                    re.escape(prefix) + r"[0-9]+", status.get("target_url", "")
                )
                for status in previous
            )
        ):
            jobs.extend(
                {
                    "name": status["context"],
                    "html_url": status["target_url"],
                    "attempt": run["run_attempt"],
                }
                for status in previous
            )
            continue
        if batch is None:
            batch = api(
                f"repos/{REPOSITORY}/actions/runs/{run['id']}/jobs?filter=latest&per_page={PAGE_SIZE}"
            )
        if (
            not isinstance(batch.get("total_count"), int)
            or not 0 <= batch["total_count"] <= PAGE_SIZE
            or not isinstance(batch.get("jobs"), list)
            or batch["total_count"] != len(batch["jobs"])
        ):
            return None
        selected_jobs = batch["jobs"]
        if any(
            job.get("head_sha") != sha
            or job.get("status") != "completed"
            or job.get("run_id") != run["id"]
            or job.get("run_attempt") != run["run_attempt"]
            for job in selected_jobs
        ):
            return None
        current_jobs = []
        for context in SOURCE_CHECKS[workflow]:
            matches = [job for job in selected_jobs if job.get("name") == context]
            if len(matches) != 1:
                return None
            job = matches[0]
            if (
                not isinstance(job.get("id"), int)
                or job["id"] <= 0
                or job.get("html_url") != f"{prefix}{job['id']}"
            ):
                return None
            record = {**job, "attempt": run["run_attempt"]}
            current_jobs.append(record)
            if job.get("conclusion") in failures:
                evidence["failures"].append(record)
        if run.get("conclusion") != "success" or any(
            job.get("conclusion") != "success" for job in current_jobs
        ):
            return None
        jobs.extend(current_jobs)
    return jobs


def reconcile_source(
    pr: dict[str, Any],
    statuses: dict[str, Any] | None = None,
    *,
    allow_success: bool = True,
) -> bool:
    """Report real source outcomes and keep producer races from admission.

    Returns:
        Whether complete required source evidence remained successful and current.

    """
    if (
        set(SOURCE_CHECKS) != set(WORKFLOWS)
        or "DCO sign-off" not in SOURCE_CHECKS.get("dco.yml", [])
        or not source_identity(pr)
    ):
        return False
    sha, ref = pr["head"]["sha"], f"refs/heads/{pr['head']['ref']}"
    statuses = read_statuses(sha) if statuses is None else statuses
    SOURCE_EVIDENCE[sha] = {"runs": {}, "failures": []}
    initial_event = SOURCE_HEAD == sha and SOURCE_ACTIVITY in {
        "requested",
        "in_progress",
    }
    if initial_event and not any(
        status.get("description", "").startswith(SOURCE_REPORT)
        for status in statuses.values()
    ):
        return False
    jobs = (
        source_jobs(pr, statuses)
        if not initial_event and source_contract(pr) is not None
        else None
    )
    if SOURCE_EVIDENCE[sha]["runs"] and not source_runs_current(pr):
        source_jobs(pr, statuses)
        if SOURCE_EVIDENCE[sha]["runs"] and not source_runs_current(pr):
            SOURCE_EVIDENCE[sha]["failures"] = []
        jobs = None
    if not allow_success:
        jobs = None
    if jobs and source_contract(pr) is None:
        jobs = None
        SOURCE_EVIDENCE[sha]["failures"] = []
    failed = SOURCE_EVIDENCE[sha]["failures"]
    results = {
        job["name"]: {
            "context": job["name"],
            "state": "success",
            "target_url": job["html_url"],
            "attempt": job["attempt"],
        }
        for job in jobs or []
    }
    results.update(
        {
            job["name"]: {
                "context": job["name"],
                "state": "failure",
                "target_url": job["html_url"],
                "attempt": job["attempt"],
            }
            for job in failed
        }
    )
    for status in statuses.values():
        if (
            status.get("description", "").startswith(SOURCE_REPORT)
            and not jobs
            and status["context"] not in results
        ):
            results[status["context"]] = {
                "context": status["context"],
                "state": "pending",
                "target_url": status["target_url"],
                "attempt": 0,
            }
    wrote = False
    for result in results.values():
        previous = statuses.get(result["context"], {})
        description = f"{SOURCE_REPORT} result; attempt {result['attempt']}"
        if (
            previous.get("state") == result["state"]
            and previous.get("target_url") == result["target_url"]
            and previous.get("description") == description
        ):
            continue
        fresh = api(f"repos/{REPOSITORY}/pulls/{pr['number']}")
        if not source_identity(fresh) or fresh["head"]["sha"] != sha:
            return False
        current = api(f"repos/{REPOSITORY}/git/ref/{ref.removeprefix('refs/')}")
        if current["object"]["sha"] != sha:
            return False
        wrote = True
        api(
            f"repos/{REPOSITORY}/statuses/{sha}",
            "POST",
            {
                "context": result["context"],
                "state": result["state"],
                "target_url": result["target_url"],
                "description": description,
            },
        )
    if jobs and wrote and (not source_runs_current(pr) or source_contract(pr) is None):
        reconcile_source(pr, allow_success=False)
        return False
    return bool(jobs)


def main() -> None:
    """Reconcile open automation PRs, then start missing queue validation.

    Raises:
        subprocess.CalledProcessError: An API error other than a deleted ref occurred.

    """
    global SOURCE_HEAD, SOURCE_ACTIVITY
    SOURCE_CACHE.clear()
    SOURCE_EVIDENCE.clear()
    run_id = os.environ.get("SOURCE_RUN_ID", "")
    try:
        notification = source_notification(
            run_id,
            os.environ.get("SOURCE_RUN_ATTEMPT", ""),
            os.environ.get("SOURCE_NOTIFICATION_ACTIVITY", ""),
        )
    except ObsoleteSourceNotification as error:
        print(f"::warning::Ignoring obsolete verified source callback: {error}")
        return
    if notification is None:
        wait_for_source_run(run_id)
    else:
        SOURCE_HEAD, SOURCE_ACTIVITY = notification

    # Pagination is bounded by GitHub's repository PR limit, not a waiting loop.
    queue_expected = False
    page = 1
    while True:
        pulls = api(
            f"repos/{REPOSITORY}/pulls?state=open&base={quote(DEFAULT_BRANCH, safe='')}"
            f"&per_page={PAGE_SIZE}&page={page}"
        )
        for pr in pulls:
            queue_expected = admit(pr) or queue_expected
        if len(pulls) < PAGE_SIZE:
            break
        page += 1

    # GITHUB_TOKEN enqueue events do not start merge_group workflows. Explicit
    # dispatch is supported. Discover queue refs and validate their exact SHA.
    # Enqueue acknowledgement can precede ref creation. GITHUB_TOKEN will not
    # trigger another workflow for us, and scheduled runs can be delayed.
    for attempt in range(12):
        refs = api(
            f"repos/{REPOSITORY}/git/matching-refs/{QUEUE_PREFIX.removeprefix('refs/')}"
        )
        if not queue_expected:
            break
        # Old refs can linger after a merge. Wait for the live front entry,
        # not merely any ref in the queue namespace. PRs behind it wait for
        # its completion callback rather than consuming another polling loop.
        front = front_queue_entry()
        if front is None:
            refs = []
            break
        head = front["headCommit"]
        if head and any(entry["object"]["sha"] == head["oid"] for entry in refs):
            break
        if attempt == 11:
            print(
                "::warning::Queued PR has no visible validation ref after 55 seconds; "
                "a later reconciliation must retry discovery"
            )
            break
        time.sleep(5)
    published_passes = set()
    for entry in refs:
        try:
            if dispatch_entry(entry):
                published_passes.add(entry["object"]["sha"])
        except subprocess.CalledProcessError as error:  # ruff: ignore[try-except-in-loop]
            # Each ref can disappear independently while GitHub advances the queue.
            if "HTTP 404" not in (error.stderr or ""):
                raise
            print(f"Queue ref disappeared: {entry['ref']}")
    if published_passes:
        advance_after_publication(published_passes)


def advance_after_publication(published: set[str]) -> None:
    """Wait once for GitHub's asynchronous merge to expose its next live front."""
    for attempt in range(12):
        front = front_queue_entry()
        if front is None:
            return
        head = front.get("headCommit")
        if head and head.get("oid") not in published:
            refs = api(
                f"repos/{REPOSITORY}/git/matching-refs/{QUEUE_PREFIX.removeprefix('refs/')}"
            )
            matching = [
                entry
                for entry in refs
                if entry["ref"].startswith(QUEUE_PREFIX)
                and entry["object"]["sha"] == head["oid"]
            ]
            if len(matching) == 1:
                try:
                    dispatch_entry(matching[0])
                except subprocess.CalledProcessError as error:
                    if "HTTP 404" not in (error.stderr or ""):
                        raise
                    print("Next queue ref disappeared before validation dispatch")
                return
        if attempt < 11:
            time.sleep(5)
    print(
        "::warning::No new verified front ref after publishing success; recovery discovery is bounded"
    )


def dispatch_entry(entry: dict[str, Any]) -> bool:
    """Start missing queue workflows and report whether success was published.

    Returns:
        Whether fresh passing results justify one bounded next-front discovery.

    """
    ref, sha = entry["ref"], entry["object"]["sha"]
    if not ref.startswith(QUEUE_PREFIX):
        return False
    base = api(f"repos/{REPOSITORY}/git/commits/{sha}")["parents"][0]["sha"]
    validation_runs: list[dict[str, Any]] = []
    for workflow in WORKFLOWS:
        runs = api(
            f"repos/{REPOSITORY}/actions/workflows/{workflow}/runs?head_sha={sha}&per_page=1"
        )
        if runs["total_count"]:
            latest_run = runs["workflow_runs"][0]
            latest_run["queue_workflow"] = workflow
            validation_runs.append(latest_run)
            continue
        # The ref can disappear when another group merges. Do not dispatch
        # against a replacement commit after inspecting a different SHA.
        current = api(f"repos/{REPOSITORY}/git/ref/{ref.removeprefix('refs/')}")
        if current["object"]["sha"] != sha:
            break
        payload = {"ref": ref.removeprefix("refs/heads/")}
        if workflow == "dependency-review.yml":
            payload["inputs"] = {"base_ref": base, "head_ref": sha}
        if workflow in BASE_SHA_WORKFLOWS:
            payload["inputs"] = {"base_sha": base}
        api(
            f"repos/{REPOSITORY}/actions/workflows/{workflow}/dispatches",
            "POST",
            payload,
        )
        print(f"Dispatched {workflow} for {sha}")
    return publish_dispatch_results(ref, sha, validation_runs)


def read_statuses(sha: str) -> dict[str, Any]:
    """Read the latest commit status for every context.

    Returns:
        Statuses indexed by their exact context names.

    """
    statuses: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = api(
            f"repos/{REPOSITORY}/commits/{sha}/status?per_page={PAGE_SIZE}&page={page}"
        )["statuses"]
        statuses.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    return {status["context"]: status for status in reversed(statuses)}


def workflow_set_passed(sha: str, runs: list[dict[str, Any]]) -> bool:
    """Require the complete configured workflow set on the inspected commit.

    Returns:
        Whether every workflow finished successfully on this SHA.

    """
    return len(runs) == len(WORKFLOWS) and all(
        run["head_sha"] == sha
        and run["status"] == "completed"
        and run["conclusion"] == "success"
        for run in runs
    )


def latest_dispatch_jobs(
    sha: str, runs: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    """Read dispatch jobs and reject newer runs or attempts.

    Returns:
        Completed jobs on this commit, or None when the evidence changed.

    """
    jobs: list[dict[str, Any]] = []
    for run in runs:
        page = 1
        while True:
            batch = api(
                f"repos/{REPOSITORY}/actions/runs/{run['id']}/jobs"
                f"?filter=latest&per_page={PAGE_SIZE}&page={page}"
            )["jobs"]
            jobs.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            page += 1
        # A separate dispatch or rerun can replace the evidence being inspected.
        fresh = api(
            f"repos/{REPOSITORY}/actions/workflows/{run['queue_workflow']}/runs"
            f"?head_sha={sha}&per_page=1"
        )["workflow_runs"]
        current = fresh[0] if fresh else {}
        expected = {
            key: run[key]
            for key in ("id", "run_attempt", "status", "conclusion", "head_sha")
        }
        if any(current.get(key) != value for key, value in expected.items()):
            return None
    if any(job["head_sha"] != sha or job["status"] != "completed" for job in jobs):
        return None
    return [job for job in jobs if job["name"] != "Queue completion callback"]


def invalidate_results(
    ref: str, sha: str, latest: dict[str, Any], reported: set[str] | None = None
) -> None:
    """Invalidate old adapter results absent from the latest verified jobs."""
    for status in latest.values():
        if status.get("description", "").startswith("Mirrored queue job") and (
            reported is None or status["context"] not in reported
        ):
            publish_status(
                ref,
                sha,
                latest,
                {
                    "context": status["context"],
                    "state": "pending",
                    "target_url": status["target_url"],
                },
            )


def publish_dispatch_results(ref: str, sha: str, runs: list[dict[str, Any]]) -> bool:
    """Expose verified dispatch results and identify newly published complete passes.

    Returns:
        Whether new actual success results may let GitHub advance the live queue.

    """
    dispatched = [run for run in runs if run["event"] == "workflow_dispatch"]
    if not dispatched:
        return False
    latest = read_statuses(sha)
    all_passed = workflow_set_passed(sha, runs)
    failures = {"failure", "timed_out", "cancelled"}
    evidence = (
        dispatched
        if all_passed
        else [
            run
            for run in dispatched
            if run["head_sha"] == sha
            and run["status"] == "completed"
            and run["conclusion"] in failures
        ]
    )
    # Failure can reject the group before unrelated workflows finish, but a
    # success still requires the complete configured workflow set to pass.
    jobs = latest_dispatch_jobs(sha, evidence) if evidence else None
    if not jobs:
        invalidate_results(ref, sha, latest)
        return False
    results = {
        job["name"]: "success" if job["conclusion"] == "success" else "failure"
        for job in jobs
        if job["conclusion"] in failures
        or (all_passed and job["conclusion"] == "success")
    }
    invalidate_results(ref, sha, latest, set(results))
    published_success = False
    for job in jobs:
        # Skipped and neutral jobs provide no passing or failing evidence.
        if job["name"] in results:
            changed = publish_status(
                ref,
                sha,
                latest,
                {
                    "context": job["name"],
                    "state": results[job["name"]],
                    "target_url": job["html_url"],
                },
            )
            published_success |= changed and results[job["name"]] == "success"
    source_required = {name for names in SOURCE_CHECKS.values() for name in names}
    return (
        published_success
        and all_passed
        and all(state == "success" for state in results.values())
        and source_required.issubset(results)
    )


def publish_status(
    ref: str,
    sha: str,
    latest: dict[str, Any],
    result: dict[str, str],
) -> bool:
    """Publish one actual job result once, while its queue ref still matches.

    Returns:
        Whether this exact current queue commit received a new result.

    """
    name, state, target_url = result["context"], result["state"], result["target_url"]
    previous = latest.get(name, {})
    if previous.get("state") == state and previous.get("target_url") == target_url:
        return False
    current = api(f"repos/{REPOSITORY}/git/ref/{ref.removeprefix('refs/')}")
    if current["object"]["sha"] != sha:
        return False
    api(
        f"repos/{REPOSITORY}/statuses/{sha}",
        "POST",
        {
            "context": name,
            "state": state,
            "target_url": target_url,
            "description": "Mirrored queue job awaiting validation"
            if state == "pending"
            else "Mirrored queue job result",
        },
    )
    print(f"Reported {name}: {state} for {sha}")
    return True


if __name__ == "__main__":
    main()
