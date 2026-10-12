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
    trusted = pr["user"]["login"] == "dependabot[bot]" or (
        bool(AUTOMATION_BRANCH)
        and pr["user"]["login"] == "github-actions[bot]"
        and pr["head"]["ref"] == AUTOMATION_BRANCH
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
        return True
    # Once admitted, a rejected head needs a fix or deliberate requeue.
    # Never keep rebuilding the same failing merge group indefinitely.
    statuses = api(f"repos/{REPOSITORY}/commits/{sha}/status")["statuses"]
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


def main() -> None:
    """Reconcile open automation PRs, then start missing queue validation.

    Raises:
        subprocess.CalledProcessError: An API error other than a deleted ref occurred.

    """
    wait_for_source_run(os.environ.get("SOURCE_RUN_ID", ""))

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
    for entry in refs:
        try:
            dispatch_entry(entry)
        except subprocess.CalledProcessError as error:  # ruff: ignore[try-except-in-loop]
            # Each ref can disappear independently while GitHub advances the queue.
            if "HTTP 404" not in (error.stderr or ""):
                raise
            print(f"Queue ref disappeared: {entry['ref']}")


def dispatch_entry(entry: dict[str, Any]) -> None:
    """Start missing workflows for one queue ref if it still exists."""
    ref, sha = entry["ref"], entry["object"]["sha"]
    if not ref.startswith(QUEUE_PREFIX):
        return
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
    publish_dispatch_results(ref, sha, validation_runs)


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


def publish_dispatch_results(ref: str, sha: str, runs: list[dict[str, Any]]) -> None:
    """Expose verified dispatch results to the merge queue's status evaluator."""
    dispatched = [run for run in runs if run["event"] == "workflow_dispatch"]
    if not dispatched:
        return
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
        return
    results = {
        job["name"]: "success" if job["conclusion"] == "success" else "failure"
        for job in jobs
        if job["conclusion"] in failures
        or (all_passed and job["conclusion"] == "success")
    }
    invalidate_results(ref, sha, latest, set(results))
    for job in jobs:
        # Skipped and neutral jobs provide no passing or failing evidence.
        if job["name"] in results:
            publish_status(
                ref,
                sha,
                latest,
                {
                    "context": job["name"],
                    "state": results[job["name"]],
                    "target_url": job["html_url"],
                },
            )


def publish_status(
    ref: str,
    sha: str,
    latest: dict[str, Any],
    result: dict[str, str],
) -> None:
    """Publish one actual job result once, while its queue ref still matches."""
    name, state, target_url = result["context"], result["state"], result["target_url"]
    previous = latest.get(name, {})
    if previous.get("state") == state and previous.get("target_url") == target_url:
        return
    current = api(f"repos/{REPOSITORY}/git/ref/{ref.removeprefix('refs/')}")
    if current["object"]["sha"] != sha:
        return
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


if __name__ == "__main__":
    main()
