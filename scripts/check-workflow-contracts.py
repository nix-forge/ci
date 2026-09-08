"""Check shared CI invariants in real consumer workflows and onboarding templates."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml


def validate(root: Path) -> list[str]:
    """Return actionable policy errors without contacting GitHub."""
    errors = []
    pins = set()
    workflows = sorted((root / ".github/workflows").glob("*.y*ml"))
    templates = sorted((root / "workflow-templates").glob("*.y*ml"))
    actions = sorted(root.glob("actions/*/action.yml"))
    actions += sorted(root.glob(".github/actions/*/action.yml"))
    for path in workflows + templates + actions:
        data = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        label = str(path.relative_to(root))

        def reject(message: str, label: str = label) -> None:
            errors.append(f"{label}: {message}")

        def reference(value: str) -> None:
            if value.startswith("./"):
                return
            if not re.fullmatch(r"[^@]+@[0-9a-f]{40}", value):
                reject(f"external reference must use a full commit SHA: {value}")
            if value.startswith("nix-forge/ci/"):
                pins.add(value.rsplit("@", 1)[-1])

        if path in actions:
            jobs = {"composite": {"steps": data.get("runs", {}).get("steps", [])}}
        else:
            if data.get("permissions") != {}:
                reject("workflow permissions must default to {}")
            events = data.get("on", {})
            if path.name == "ci.yml" and "workflow_call" not in events:
                if not {"pull_request", "merge_group"}.issubset(events):
                    reject("CI must run on pull_request and merge_group")
                for event in ("pull_request", "merge_group"):
                    config = events.get(event) or {}
                    if isinstance(config, dict) and (
                        {"paths", "paths-ignore"} & config.keys()
                    ):
                        reject("required CI cannot use workflow-level path filters")
            jobs = data.get("jobs", {})
        for name, job in jobs.items():
            if job.get("secrets") == "inherit":
                reject(f"{name}: pass only explicitly named secrets")
            if "uses" in job:
                reference(job["uses"])
                continue
            if path not in actions and "timeout-minutes" not in job:
                reject(f"{name}: set an explicit timeout")
            steps = job.get("steps", [])
            checkout = None
            for step in steps:
                uses = step.get("uses", "")
                if uses:
                    reference(uses)
                if uses.startswith("actions/checkout@"):
                    checkout = step.get("with", {})
                    if checkout.get("persist-credentials") != "false":
                        reject(f"{name}: checkout must disable persisted credentials")
                if uses.startswith("nix-forge/ci/actions/repository-checks@") and (
                    checkout is None or checkout.get("fetch-depth") != "0"
                ):
                    reject(f"{name}: repository-checks needs a full-history checkout")
            if (
                path not in actions
                and checkout is not None
                and ({"pull_request_target", "workflow_run"} & set(data.get("on", {})))
            ):
                reject(f"{name}: privileged metadata workflows must not check out code")
        if path in templates:
            metadata = path.with_suffix(".properties.json")
            if not metadata.exists():
                reject("template is missing matching .properties.json")
            else:
                properties = json.loads(metadata.read_text())
                for key in ("name", "description"):
                    if (
                        not isinstance(properties.get(key), str)
                        or not properties[key].strip()
                    ):
                        reject(f"template metadata needs a nonempty {key}")
    for metadata in (root / "workflow-templates").glob("*.properties.json"):
        stem = metadata.name.removesuffix(".properties.json")
        if not any(path.stem == stem for path in templates):
            errors.append(f"{metadata.name}: orphaned template metadata")
    if len(pins) > 1:
        errors.append(
            "shared CI references must use one reviewed release commit per repository"
        )
    reconciler = root / ".github/workflows/reconcile-merge-queue.yml"
    if reconciler.exists():
        data = yaml.load(reconciler.read_text(), Loader=yaml.BaseLoader)
        for job in data.get("jobs", {}).values():
            for step in job.get("steps", []):
                if not step.get("uses", "").startswith(
                    "nix-forge/ci/actions/reconcile-queue@"
                ):
                    continue
                for filename in json.loads(step["with"]["workflows"]):
                    path = root / ".github/workflows" / filename
                    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
                    events = workflow.get("on", {})
                    if not {"merge_group", "workflow_dispatch"}.issubset(events):
                        errors.append(
                            f"{filename}: queue validation needs native and dispatch triggers"
                        )
                    jobs = workflow.get("jobs", {})
                    callback = jobs.get("queue-completion", {})
                    if callback.get("name") != "Queue completion callback":
                        errors.append(f"{filename}: preserve the queue callback name")
                    if set(callback.get("needs", [])) != set(jobs) - {
                        "queue-completion"
                    }:
                        errors.append(
                            f"{filename}: queue callback must wait for all validation jobs"
                        )
                    condition = callback.get("if", "")
                    if not all(
                        term in condition
                        for term in (
                            "always()",
                            "workflow_dispatch",
                            "refs/heads/gh-readonly-queue/main/",
                        )
                    ):
                        errors.append(
                            f"{filename}: callback must be restricted to queue dispatches"
                        )
    return errors


if __name__ == "__main__":
    failures = validate(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
    for failure in failures:
        print(failure, file=sys.stderr)
    sys.exit(bool(failures))
