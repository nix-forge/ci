"""Exercise queue admission and GITHUB_TOKEN dispatch without remote writes."""

from __future__ import annotations

import base64
import importlib.util
import os
import subprocess
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

os.environ.setdefault("GH_REPO", "nix-forge/example")
os.environ.setdefault("QUEUE_WORKFLOWS", '["ci.yml", "dependency-review.yml"]')
spec = importlib.util.spec_from_file_location(
    "queue", Path(__file__).parents[1] / "scripts" / "reconcile-queue.py"
)
if spec is None or spec.loader is None:
    message = "Unable to load the queue reconciler"
    raise ImportError(message)
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)


class ReconcileTests(unittest.TestCase):
    """Keep CI and queue failures observable without executing PR code."""

    def setUp(self) -> None:
        """Reset the in-memory GitHub API fixtures."""
        self.pr: dict[str, Any] = {
            "user": {"login": "dependabot[bot]"},
            "draft": False,
            "number": 7,
            "node_id": "PR_7",
            "head": {
                "sha": "head",
                "ref": "dependabot/example",
                "repo": {"full_name": queue.REPOSITORY},
            },
        }
        self.statuses: list[dict[str, Any]] = []
        self.refs: list[dict[str, Any]] = []
        self.existing_runs = 0
        self.changed_files: list[dict[str, str]] = []
        self.writes: list[tuple[str, Any]] = []

    def api(self, path: str, method: str = "GET", data: object = None) -> Any:
        """Record writes and serve controlled API responses.

        Returns:
            A fixture matching the requested endpoint.

        Raises:
            AssertionError: An unexpected endpoint was requested.

        """
        if method != "GET":
            self.writes.append((path, data))
            return None
        if "/files?" in path:
            return self.changed_files
        if "/pulls?" in path:
            return [self.pr]
        if path.endswith("/status"):
            return {"statuses": self.statuses}
        if "matching-refs" in path:
            return self.refs
        if "/runs?" in path:
            return {
                "total_count": self.existing_runs,
                "workflow_runs": [{"event": "merge_group"}],
            }
        if "/git/commits/" in path:
            return {"parents": [{"sha": "base"}]}
        if "/git/ref/" in path:
            return {"object": {"sha": "group"}}
        raise AssertionError(path)

    def run_queue(self, check_code: int = 0) -> Any:
        """Run reconciliation with a simulated required-check result.

        Returns:
            The GraphQL mock for assertions about admission.

        """
        result = subprocess.CompletedProcess([], check_code, '[{"bucket":"pass"}]')
        with (
            patch.object(queue, "api", side_effect=self.api),
            patch.object(
                queue, "graphql", return_value={"node": {"mergeQueueEntry": None}}
            ) as graphql,
            patch.object(queue.subprocess, "run", return_value=result),
            patch.object(queue.time, "sleep"),
            patch.object(
                queue,
                "front_queue_entry",
                return_value={"headCommit": {"oid": "group"}},
            ),
        ):
            queue.main()
        return graphql

    def test_pending_checks_do_not_poll_or_enqueue(self) -> None:
        """Verify pending checks do not poll or enqueue."""
        graphql = self.run_queue(check_code=8)
        self.assertEqual(graphql.call_count, 1)
        self.assertEqual(self.writes, [])

    def test_failed_checks_do_not_enqueue(self) -> None:
        """Verify failed checks do not enqueue."""
        self.assertEqual(self.run_queue(check_code=1).call_count, 1)
        self.assertEqual(self.writes, [])

    def test_fork_does_not_reach_checks_or_mutations(self) -> None:
        """Verify fork does not reach checks or mutations."""
        self.pr["head"]["repo"]["full_name"] = "attacker/example"
        self.assertEqual(self.run_queue().call_count, 0)
        self.assertEqual(self.writes, [])

    def test_deleted_fork_does_not_abort_reconciliation(self) -> None:
        self.pr["head"]["repo"] = None
        self.assertEqual(self.run_queue().call_count, 0)
        self.assertEqual(self.writes, [])

    def test_package_bot_requires_explicit_branch_opt_in(self) -> None:
        self.pr["user"]["login"] = "github-actions[bot]"
        self.pr["head"]["ref"] = "automation/updates"
        self.assertEqual(self.run_queue().call_count, 0)
        with patch.object(queue, "AUTOMATION_BRANCH", "automation/updates"):
            self.assertEqual(self.run_queue().call_count, 2)
        self.assertEqual(self.writes[0][1]["context"], queue.ADMISSION)

    def test_dispatch_base_sha_is_a_consumer_contract(self) -> None:
        entry = {
            "ref": "refs/heads/gh-readonly-queue/main/pr-7-base",
            "object": {"sha": "group"},
        }
        with (
            patch.object(queue, "api", side_effect=self.api),
            patch.object(queue, "BASE_SHA_WORKFLOWS", ["ci.yml"]),
        ):
            queue.dispatch_entry(entry)
        self.assertEqual(self.writes[0][1]["inputs"], {"base_sha": "base"})

    def test_default_branch_is_not_assumed(self) -> None:
        self.pr["draft"] = True
        entry = {
            "ref": "refs/heads/gh-readonly-queue/release/stable/pr-7-base",
            "object": {"sha": "group"},
        }
        self.refs = [entry]
        with (
            patch.object(queue, "DEFAULT_BRANCH", "release/stable"),
            patch.object(
                queue, "QUEUE_PREFIX", "refs/heads/gh-readonly-queue/release/stable/"
            ),
            patch.object(queue, "api", side_effect=self.api) as api,
        ):
            queue.main()
        self.assertIn("base=release%2Fstable", api.call_args_list[0].args[0])
        self.assertEqual(
            self.writes[0][1]["ref"], entry["ref"].removeprefix("refs/heads/")
        )
        with (
            patch.object(queue, "DEFAULT_BRANCH", "release/stable"),
            patch.object(
                queue, "graphql", return_value={"repository": {"mergeQueue": None}}
            ) as graphql,
        ):
            self.assertIsNone(queue.front_queue_entry())
        self.assertEqual(graphql.call_args.kwargs["branch"], "release/stable")

    def test_automation_changes_require_human_admission(self) -> None:
        for filename in [
            ".github/workflows/ci.yml",
            ".github/actions/setup/action.yml",
            ".github/scripts/reconcile-queue.py",
            "actions/setup-nix/action.yml",
            "scripts/update-packages.py",
            "workflow-templates/nix-checks.yml",
        ]:
            with self.subTest(filename=filename):
                self.changed_files = [{"filename": filename}]
                self.assertEqual(self.run_queue().call_count, 0)
                self.assertEqual(self.writes, [])

    def test_proven_dependabot_pin_update_reaches_required_checks(self) -> None:
        self.changed_files = [
            {"filename": ".github/workflows/ci.yml", "status": "modified"}
        ]
        with patch.object(queue, "safe_pin_update", return_value=True) as proof:
            graphql = self.run_queue()
        proof.assert_called_once_with(self.pr, self.changed_files[0])
        self.assertEqual(graphql.call_count, 2)
        self.assertEqual(graphql.call_args.kwargs["sha"], self.pr["head"]["sha"])

    def test_package_bot_cannot_use_dependabot_pin_exception(self) -> None:
        self.changed_files = [
            {"filename": ".github/workflows/ci.yml", "status": "modified"}
        ]
        self.pr["user"]["login"] = "github-actions[bot]"
        self.pr["head"]["ref"] = "automation/updates"
        with (
            patch.object(queue, "AUTOMATION_BRANCH", "automation/updates"),
            patch.object(queue, "safe_pin_update", return_value=True) as proof,
        ):
            self.assertEqual(self.run_queue().call_count, 0)
        proof.assert_not_called()
        self.assertEqual(self.writes, [])

    def test_renaming_automation_requires_human_admission(self) -> None:
        self.changed_files = [
            {
                "filename": "ordinary.txt",
                "previous_filename": "actions/setup-nix/action.yml",
            }
        ]
        self.assertEqual(self.run_queue().call_count, 0)
        self.assertEqual(self.writes, [])

    def test_changed_file_api_failure_does_not_enqueue(self) -> None:
        with (
            patch.object(queue, "api", side_effect=RuntimeError("API unavailable")),
            patch.object(queue, "graphql") as graphql,
        ):
            with self.assertRaises(RuntimeError):
                queue.admit(self.pr)
            graphql.assert_not_called()

    def test_rejected_head_is_not_requeued_forever(self) -> None:
        """Verify rejected head is not requeued forever."""
        self.statuses = [{"context": queue.ADMISSION}]
        self.assertEqual(self.run_queue().call_count, 1)
        self.assertEqual(self.writes, [])

    def test_admission_is_bound_to_reviewed_head(self) -> None:
        """Verify admission is bound to reviewed head."""
        graphql = self.run_queue()
        self.assertEqual(graphql.call_args.kwargs, {"id": "PR_7", "sha": "head"})
        self.assertEqual(self.writes[0][1]["context"], queue.ADMISSION)

    def test_absent_queue_ref_wait_is_bounded(self) -> None:
        """A delayed or blocked queue must not keep the runner alive indefinitely."""
        with patch.object(queue.time, "sleep") as sleep:
            result = subprocess.CompletedProcess([], 0, '[{"bucket":"pass"}]')
            with (
                patch.object(queue, "api", side_effect=self.api) as api,
                patch.object(
                    queue, "graphql", return_value={"node": {"mergeQueueEntry": None}}
                ),
                patch.object(queue.subprocess, "run", return_value=result),
                patch.object(
                    queue, "front_queue_entry", return_value={"headCommit": None}
                ),
            ):
                queue.main()
            self.assertEqual(sleep.call_count, 11)
            self.assertEqual(
                sum("matching-refs" in call.args[0] for call in api.call_args_list), 12
            )
        self.assertFalse(any(path.endswith("/dispatches") for path, _ in self.writes))

    def test_empty_queue_does_not_wait(self) -> None:
        """Drafts and untrusted PRs do not justify polling for a queue ref."""
        self.pr["draft"] = True
        with (
            patch.object(queue, "api", side_effect=self.api),
            patch.object(queue.time, "sleep") as sleep,
        ):
            queue.main()
        sleep.assert_not_called()
        self.assertEqual(self.writes, [])

    def test_new_admission_waits_for_queue_ref_before_dispatch(self) -> None:
        """GitHub may acknowledge enqueue before publishing its validation ref."""
        entry = {
            "ref": "refs/heads/gh-readonly-queue/main/pr-7-base",
            "object": {"sha": "group"},
        }
        reads = 0

        def delayed_api(path: str, method: str = "GET", data: object = None) -> Any:
            nonlocal reads
            if "matching-refs" in path:
                reads += 1
                return [] if reads == 1 else [entry]
            return self.api(path, method, data)

        result = subprocess.CompletedProcess([], 0, '[{"bucket":"pass"}]')
        with (
            patch.object(queue, "api", side_effect=delayed_api),
            patch.object(
                queue, "graphql", return_value={"node": {"mergeQueueEntry": None}}
            ),
            patch.object(queue.subprocess, "run", return_value=result),
            patch.object(queue.time, "sleep"),
            patch.object(
                queue,
                "front_queue_entry",
                return_value={"headCommit": {"oid": "group"}},
            ),
        ):
            queue.main()
        dispatches = [
            data for path, data in self.writes if path.endswith("/dispatches")
        ]
        self.assertEqual(
            dispatches,
            [
                {"ref": "gh-readonly-queue/main/pr-7-base"},
                {
                    "ref": "gh-readonly-queue/main/pr-7-base",
                    "inputs": {"base_ref": "base", "head_ref": "group"},
                },
            ],
        )

    def test_missing_merge_group_events_dispatch_exact_queue_commit(self) -> None:
        """Verify missing merge group events dispatch exact queue commit."""
        self.pr["draft"] = True
        self.refs = [
            {
                "ref": "refs/heads/gh-readonly-queue/main/pr-7-base",
                "object": {"sha": "group"},
            }
        ]
        self.run_queue()
        self.assertEqual(len(self.writes), 2)
        self.assertEqual(self.writes[0][1], {"ref": "gh-readonly-queue/main/pr-7-base"})
        self.assertEqual(
            self.writes[1][1]["inputs"], {"base_ref": "base", "head_ref": "group"}
        )

    def test_queue_removed_during_discovery_does_not_dispatch_stale_refs(self) -> None:
        """Queue cancellation should end discovery without launching obsolete work."""
        self.refs = [
            {"ref": "refs/heads/gh-readonly-queue/main/old", "object": {"sha": "old"}}
        ]
        with (
            patch.object(queue, "api", side_effect=self.api),
            patch.object(queue, "admit", return_value=True),
            patch.object(queue, "front_queue_entry", return_value=None),
            patch.object(queue, "dispatch_entry") as dispatch,
            patch.object(queue.time, "sleep") as sleep,
        ):
            queue.main()
        sleep.assert_not_called()
        dispatch.assert_not_called()

    def test_old_ref_does_not_hide_delayed_front_entry(self) -> None:
        """A lingering ref must not satisfy readiness for a different queue head."""
        old = {"ref": "refs/heads/gh-readonly-queue/main/old", "object": {"sha": "old"}}
        live = {
            "ref": "refs/heads/gh-readonly-queue/main/live",
            "object": {"sha": "group"},
        }
        reads = iter([[old], [old, live]])

        def delayed_api(path: str, method: str = "GET", data: object = None) -> Any:
            if "matching-refs" in path:
                return next(reads)
            return self.api(path, method, data)

        with (
            patch.object(queue, "api", side_effect=delayed_api),
            patch.object(queue, "admit", return_value=True),
            patch.object(
                queue,
                "front_queue_entry",
                return_value={"headCommit": {"oid": "group"}},
            ),
            patch.object(queue, "dispatch_entry") as dispatch,
            patch.object(queue.time, "sleep") as sleep,
        ):
            queue.main()
        sleep.assert_called_once_with(5)
        self.assertIn(unittest.mock.call(live), dispatch.call_args_list)

    def test_existing_queue_runs_are_not_duplicated(self) -> None:
        """Verify existing queue runs are not duplicated."""
        self.pr["draft"] = True
        self.refs = [
            {
                "ref": "refs/heads/gh-readonly-queue/main/pr-7-base",
                "object": {"sha": "group"},
            }
        ]
        self.existing_runs = 1
        self.run_queue()
        self.assertEqual(self.writes, [])

    def test_admission_error_does_not_stop_dispatch(self) -> None:
        """Verify a GraphQL refusal leaves existing queue work eligible."""
        result = subprocess.CompletedProcess([], 0, '[{"bucket":"pass"}]')
        with (
            patch.object(queue, "api", side_effect=self.api),
            patch.object(
                queue,
                "graphql",
                side_effect=[
                    {"node": {"mergeQueueEntry": None}},
                    RuntimeError("head changed"),
                ],
            ),
            patch.object(queue.subprocess, "run", return_value=result),
            patch.object(queue.time, "sleep"),
            patch.object(
                queue,
                "front_queue_entry",
                return_value={"headCommit": {"oid": "group"}},
            ),
        ):
            queue.main()
        self.assertEqual(self.writes, [])

    def test_disappearing_ref_does_not_stop_other_groups(self) -> None:
        """Verify deleting one queue ref does not starve later groups."""
        self.pr["draft"] = True
        self.refs = [{"ref": "first"}, {"ref": "second"}]
        error = subprocess.CalledProcessError(1, [], stderr="HTTP 404")
        with (
            patch.object(queue, "api", side_effect=self.api),
            patch.object(
                queue, "dispatch_entry", side_effect=[error, None]
            ) as dispatch,
        ):
            queue.main()
        self.assertEqual(dispatch.call_count, len(self.refs))


class PinAdmissionTests(unittest.TestCase):
    """Allow immutable pin edits without trusting patches or executing PR code."""

    def setUp(self) -> None:
        self.filename = ".github/workflows/ci.yml"
        self.pr = {"base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}
        self.item = {"filename": self.filename, "status": "modified"}
        self.old = (
            "jobs:\n  check:\n    steps:\n      - uses: actions/checkout@"
            + "c" * 40
            + " # v1.0.0\n"
        )
        self.new = self.old.replace("c" * 40, "d" * 40).replace("v1.0.0", "v1.0.1")
        self.mode = "100644"
        self.truncated = False
        self.encoding = "base64"

    def api(self, path: str, method: str = "GET", data: object = None) -> Any:
        self.assertEqual(method, "GET")
        if "/git/trees/" in path:
            old = "a" * 40 in path
            return {
                "truncated": self.truncated,
                "tree": [
                    {
                        "path": self.filename,
                        "mode": self.mode,
                        "type": "blob",
                        "sha": "old" if old else "new",
                    }
                ],
            }
        if "/git/blobs/" in path:
            content = self.old if path.endswith("old") else self.new
            return {
                "encoding": self.encoding,
                "size": len(content.encode()),
                "content": base64.b64encode(content.encode()).decode(),
            }
        raise AssertionError(path)

    def allowed(self) -> bool:
        with patch.object(queue, "api", side_effect=self.api):
            return queue.safe_pin_update(self.pr, self.item)

    def test_complete_blob_pin_edit_is_allowed(self) -> None:
        self.assertTrue(self.allowed())

    def test_action_and_template_metadata_are_allowed(self) -> None:
        for name in [
            "actions/setup/action.yml",
            ".github/actions/setup/action.yaml",
            "workflow-templates/check.yml",
        ]:
            with self.subTest(name=name):
                self.filename = name
                self.item["filename"] = name
                if name.startswith("workflow-templates/"):
                    self.old = (
                        "jobs:\n  check:\n    steps:\n      - uses: actions/checkout@"
                        + "c" * 40
                        + " # v1.0.0\n"
                    )
                    self.new = self.old.replace("c" * 40, "d" * 40).replace(
                        "v1.0.0", "v1.0.1"
                    )
                else:
                    self.old = (
                        "runs:\n  using: composite\n  steps:\n    - uses: actions/checkout@"
                        + "c" * 40
                        + " # v1.0.0\n"
                    )
                    self.new = self.old.replace("c" * 40, "d" * 40).replace(
                        "v1.0.0", "v1.0.1"
                    )
                self.assertTrue(self.allowed())

    def test_reusable_job_pin_is_allowed(self) -> None:
        self.old = (
            "jobs:\n  check:\n    uses: nix-forge/ci/.github/workflows/ci.yml@"
            + "c" * 40
            + " # v1.0.0\n"
        )
        self.new = self.old.replace("c" * 40, "d" * 40).replace("v1.0.0", "v1.0.1")
        self.assertTrue(self.allowed())

    def test_runtime_env_uses_value_is_not_an_action(self) -> None:
        for context in [
            "env:\n  uses: ",
            "jobs:\n  check:\n    env:\n      uses: ",
            "jobs:\n  check:\n    steps:\n      - run: echo safe\n        env:\n          uses: ",
        ]:
            self.old = context + "actions/checkout@" + "c" * 40 + "\n"
            self.new = self.old.replace("c" * 40, "d" * 40)
            self.assertFalse(self.allowed())

    def test_tagged_or_anchored_runtime_scalar_is_not_an_action(self) -> None:
        for scalar in ["&script |", "!!str |", "!!str >-", "!runtime |"]:
            self.old = (
                "jobs:\n  check:\n    steps:\n      - run: "
                + scalar
                + "\n          uses: actions/checkout@"
                + "c" * 40
                + "\n"
            )
            self.new = self.old.replace("c" * 40, "d" * 40)
            self.assertFalse(self.allowed())

    def test_alias_sharing_action_pin_with_runtime_data_is_rejected(self) -> None:
        self.old = (
            "jobs:\n  check:\n    steps:\n      - uses: &pin actions/checkout@"
            + "c" * 40
            + "\n    env:\n      PIN: *pin\n"
        )
        self.new = self.old.replace("c" * 40, "d" * 40)
        self.assertFalse(self.allowed())

    def test_duplicate_action_fields_are_rejected(self) -> None:
        self.old += "        uses: actions/setup-python@" + "e" * 40 + "\n"
        self.new = self.old.replace("c" * 40, "d" * 40)
        self.assertFalse(self.allowed())

    def test_semantic_changes_are_rejected(self) -> None:
        for change in [
            self.new + "permissions: write-all\n",
            self.new.replace("actions/checkout", "attacker/checkout"),
            self.new.replace("uses:", "run:"),
            self.new.replace("d" * 40, "main"),
            self.new.replace("      -", "    -"),
            self.new.replace("# v1.0.1", "# arbitrary policy change"),
            self.new.replace("jobs:", "on:"),
            self.new.replace("\n", "\r\n"),
        ]:
            with self.subTest(change=change):
                self.new = change
                self.assertFalse(self.allowed())

    def test_new_renamed_deleted_or_nonmetadata_files_are_rejected(self) -> None:
        for status in ["added", "removed", "renamed"]:
            self.item["status"] = status
            self.assertFalse(self.allowed())
        self.item["status"] = "modified"
        for name in [
            "scripts/check.py",
            "actions/setup/main.js",
            ".github/workflows/nested/check.yml",
            "workflow-templates/check.properties.json",
            "actions/../check/action.yml",
        ]:
            self.item["filename"] = name
            self.assertFalse(self.allowed())

    def test_missing_binary_oversized_or_mode_changed_blobs_are_rejected(self) -> None:
        self.mode = "100755"
        self.assertFalse(self.allowed())
        self.mode = "120000"
        self.assertFalse(self.allowed())
        self.mode = "100644"
        self.truncated = True
        self.assertFalse(self.allowed())
        self.truncated = False
        self.encoding = "none"
        self.assertFalse(self.allowed())
        self.encoding = "base64"
        self.new = "\0" + self.new
        self.assertFalse(self.allowed())
        self.new = "x" * (1024 * 1024 + 1)
        self.assertFalse(self.allowed())
        self.pr["head"]["sha"] = "main"
        self.assertFalse(self.allowed())

    def test_action_text_inside_runtime_script_is_not_metadata(self) -> None:
        for scalar in ["|", ">-", "|2"]:
            self.old = (
                "steps:\n  - run: "
                + scalar
                + "\n      uses: actions/checkout@"
                + "c" * 40
                + "\n"
            )
            self.new = self.old.replace("c" * 40, "d" * 40)
            self.assertFalse(self.allowed())

    def test_multiline_quoted_runtime_script_is_not_metadata(self) -> None:
        self.old = (
            'steps:\n  - run: "echo start\n      uses: actions/checkout@'
            + "c" * 40
            + '\n      echo end"\n'
        )
        self.new = self.old.replace("c" * 40, "d" * 40)
        self.assertFalse(self.allowed())

    def test_api_failure_never_admits(self) -> None:
        with (
            patch.object(queue, "api", side_effect=RuntimeError("unavailable")),
            self.assertRaises(RuntimeError),
        ):
            queue.safe_pin_update(self.pr, self.item)


class DispatchResultTests(unittest.TestCase):
    """Report real dispatch jobs without promoting missing or stale evidence."""

    def setUp(self) -> None:
        """Create a successful dispatch and one native merge-group run."""
        self.ref = "refs/heads/gh-readonly-queue/main/pr-7-base"
        self.dispatch_run: dict[str, Any] = {
            "id": 1,
            "event": "workflow_dispatch",
            "head_sha": "group",
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 1,
            "queue_workflow": "ci.yml",
        }
        self.other: dict[str, Any] = {
            **self.dispatch_run,
            "id": 2,
            "event": "merge_group",
        }
        self.jobs: list[dict[str, Any]] = [
            {
                "name": "Lint",
                "head_sha": "group",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/job/1",
            }
        ]
        self.statuses: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []
        self.current = self.dispatch_run.copy()
        self.current_sha = "group"

    def api(self, path: str, method: str = "GET", data: object = None) -> Any:
        """Serve run and job metadata and retain published status payloads.

        Returns:
            The requested fixture.

        Raises:
            AssertionError: An unexpected endpoint was requested.

        """
        if method == "POST":
            if not isinstance(data, dict):
                raise AssertionError(path)
            self.writes.append(data)
            return None
        if path.endswith("/status") or "/status?" in path:
            return {"statuses": self.statuses}
        if "/jobs?" in path:
            return {"jobs": self.jobs}
        if "/actions/workflows/ci.yml/runs?" in path:
            return {"workflow_runs": [self.current]}
        if "/git/ref/" in path:
            return {"object": {"sha": self.current_sha}}
        raise AssertionError(path)

    def report(self, runs: list[dict[str, Any]] | None = None) -> None:
        """Run the adapter against the selected API fixtures."""
        with patch.object(queue, "api", side_effect=self.api):
            queue.publish_dispatch_results(
                self.ref,
                "group",
                runs if runs is not None else [self.dispatch_run, self.other],
            )

    def test_callback_is_not_a_validation_status(self) -> None:
        """A failed notification must not invalidate successful validation jobs."""
        self.jobs.append(
            {
                **self.jobs[0],
                "name": "Queue completion callback",
                "conclusion": "failure",
            }
        )
        self.report()
        self.assertEqual([write["context"] for write in self.writes], ["Lint"])

    def test_success_links_the_exact_completed_job(self) -> None:
        """Only the successful dispatch job becomes a linked commit status."""
        self.report()
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.writes[0]["context"], "Lint")
        self.assertEqual(self.writes[0]["state"], "success")
        self.assertEqual(self.writes[0]["target_url"], self.jobs[0]["html_url"])

    def test_missing_workflow_cannot_report_success(self) -> None:
        """An incomplete workflow set leaves validation pending."""
        self.report([self.dispatch_run])
        self.assertEqual(self.writes, [])

    def test_pending_or_failed_workflow_cannot_report_success(self) -> None:
        """Other required validation must finish successfully first."""
        self.other["conclusion"] = "failure"
        self.report()
        self.assertEqual(self.writes, [])

    def test_completed_failed_job_reports_failure(self) -> None:
        """A failed required job must reject the group instead of timing out."""
        self.dispatch_run["conclusion"] = "failure"
        self.current = self.dispatch_run.copy()
        self.jobs[0]["conclusion"] = "failure"
        self.report()
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.writes[0]["state"], "failure")
        self.assertEqual(self.writes[0]["context"], "Lint")
        self.assertEqual(self.writes[0]["target_url"], self.jobs[0]["html_url"])

    def test_failure_does_not_wait_for_unrelated_workflow(self) -> None:
        """Only positive evidence needs the entire configured workflow set."""
        self.dispatch_run["conclusion"] = "failure"
        self.current = self.dispatch_run.copy()
        self.jobs[0]["conclusion"] = "failure"
        self.other.update(status="in_progress", conclusion=None)
        self.report()
        self.assertEqual([write["state"] for write in self.writes], ["failure"])

    def test_failed_run_does_not_promote_its_successful_jobs(self) -> None:
        """The failure path must never bypass the existing success gate."""
        self.dispatch_run["conclusion"] = "failure"
        self.current = self.dispatch_run.copy()
        self.jobs.append({**self.jobs[0], "name": "Build", "conclusion": "failure"})
        self.report()
        self.assertEqual(
            [(write["context"], write["state"]) for write in self.writes],
            [("Build", "failure")],
        )

    def test_stale_failure_is_not_reported_after_rerun(self) -> None:
        """An old failed attempt must not reject a newly running attempt."""
        self.dispatch_run["conclusion"] = "failure"
        self.jobs[0]["conclusion"] = "failure"
        self.current.update(run_attempt=2, status="in_progress", conclusion=None)
        self.report()
        self.assertEqual(self.writes, [])

    def test_cancelled_and_timed_out_jobs_report_failure(self) -> None:
        """Terminal unsuccessful validation should not block the next PR until timeout."""
        for conclusion in ["cancelled", "timed_out"]:
            with self.subTest(conclusion=conclusion):
                self.writes = []
                self.dispatch_run["conclusion"] = conclusion
                self.current = self.dispatch_run.copy()
                self.jobs[0]["conclusion"] = conclusion
                self.report()
                self.assertEqual([write["state"] for write in self.writes], ["failure"])

    def test_skipped_or_missing_jobs_never_become_success(self) -> None:
        """A skipped or absent job provides no passing evidence."""
        self.jobs[0]["conclusion"] = "skipped"
        self.report()
        self.jobs = []
        self.report()
        self.assertEqual(self.writes, [])

    def test_job_from_another_commit_is_rejected(self) -> None:
        """Only the inspected queue commit can supply job results."""
        self.jobs[0]["head_sha"] = "different"
        self.report()
        self.assertEqual(self.writes, [])

    def test_changed_queue_ref_is_not_updated(self) -> None:
        """A changed ref cannot receive stale success statuses."""
        self.current_sha = "replacement"
        self.report()
        self.assertEqual(self.writes, [])

    def test_newer_attempt_invalidates_previous_success(self) -> None:
        """A rerun cannot retain the adapter result of an earlier attempt."""
        self.statuses = [
            {
                "context": "Lint",
                "state": "success",
                "target_url": self.jobs[0]["html_url"],
                "description": "Mirrored queue job result",
            }
        ]
        self.current["run_attempt"] = 2
        self.current["status"] = "in_progress"
        self.report()
        self.assertEqual([item["state"] for item in self.writes], ["pending"])

    def test_separate_newer_run_invalidates_previous_success(self) -> None:
        """A separate newer dispatch cannot inherit an older run's pass."""
        self.statuses = [
            {
                "context": "Lint",
                "state": "success",
                "target_url": self.jobs[0]["html_url"],
                "description": "Mirrored queue job result",
            }
        ]
        self.current["id"] = 3
        self.report()
        self.assertEqual([item["state"] for item in self.writes], ["pending"])

    def test_existing_identical_result_is_not_republished(self) -> None:
        """Repeated recovery calls reuse the same result."""
        self.statuses = [
            {
                "context": "Lint",
                "state": "success",
                "target_url": self.jobs[0]["html_url"],
                "description": "Mirrored queue job result",
            }
        ]
        self.report()
        self.assertEqual(self.writes, [])

    def test_missing_job_invalidates_previous_success(self) -> None:
        """A job absent from the latest attempt must not inherit a pass."""
        self.statuses = [
            {
                "context": "Removed",
                "state": "success",
                "target_url": "https://github.com/job/old",
                "description": "Mirrored queue job result",
            }
        ]
        self.report()
        self.assertEqual(self.writes[0]["context"], "Removed")
        self.assertEqual(self.writes[0]["state"], "pending")


class CompletionTests(unittest.TestCase):
    """Verify the callback cannot race its unfinished source workflow."""

    def test_waits_for_source_completion(self) -> None:
        """A callback dispatch can arrive before GitHub finishes its source."""
        with (
            patch.object(
                queue,
                "api",
                side_effect=[{"status": "in_progress"}, {"status": "completed"}],
            ) as api,
            patch.object(queue.time, "sleep") as sleep,
        ):
            queue.wait_for_source_run("123")
        self.assertEqual(api.call_count, 2)
        sleep.assert_called_once_with(5)

    def test_wait_is_bounded(self) -> None:
        """A stuck source must not occupy a polling runner indefinitely."""
        with (
            patch.object(queue, "api", return_value={"status": "in_progress"}) as api,
            patch.object(queue.time, "sleep"),
        ):
            queue.wait_for_source_run("123")
        self.assertEqual(api.call_count, 12)

    def test_rejects_invalid_run_id(self) -> None:
        """Dispatch input cannot change the GitHub API path."""
        with patch.object(queue, "api") as api:
            with self.assertRaises(ValueError):
                queue.wait_for_source_run("../other")
            queue.wait_for_source_run("")
        api.assert_not_called()


class PackageSourceTests(unittest.TestCase):
    """Exercise opted-in source proof with real REST response shapes."""

    def setUp(self) -> None:
        self.sha, self.base = "a" * 40, "b" * 40
        self.branch = "automation/package-updates-example"
        self.pr = {
            "state": "open",
            "draft": False,
            "number": 7,
            "node_id": "PR_7",
            "user": {"login": "github-actions[bot]", "type": "Bot", "id": 41898282},
            "head": {
                "sha": self.sha,
                "ref": self.branch,
                "repo": {"full_name": queue.REPOSITORY},
            },
            "base": {
                "ref": queue.DEFAULT_BRANCH,
                "repo": {"full_name": queue.REPOSITORY},
            },
        }
        self.checks = {"ci.yml": ["Build", "Lint"], "dco.yml": ["DCO sign-off"]}
        self.runs = {}
        self.jobs = {}
        for index, workflow in enumerate(self.checks, 1):
            self.runs[workflow] = [
                {
                    "id": index,
                    "workflow_id": index,
                    "path": f".github/workflows/{workflow}",
                    "head_sha": self.sha,
                    "head_branch": self.branch,
                    "event": "workflow_dispatch",
                    "repository": {"full_name": queue.REPOSITORY},
                    "head_repository": {"full_name": queue.REPOSITORY},
                    "status": "completed",
                    "conclusion": "success",
                    "run_attempt": 1,
                }
            ]
            self.jobs[index] = [
                {
                    "id": index * 10 + number,
                    "run_id": index,
                    "run_attempt": 1,
                    "name": name,
                    "head_sha": self.sha,
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": f"https://github.com/{queue.REPOSITORY}/actions/runs/{index}/job/{index * 10 + number}",
                }
                for number, name in enumerate(self.checks[workflow])
            ]
        self.paths = [
            {"filename": "pkgs/by-name/ex/example/source.nix", "status": "modified"},
            {"filename": "docs/catalog.md", "status": "modified"},
        ]
        self.parents = [{"sha": self.base}]
        self.ancestry = "ahead"
        self.tree = {
            "truncated": False,
            "tree": [
                {
                    "path": f".github/workflows/{workflow}",
                    "sha": "c" * 40,
                    "mode": "100644",
                    "type": "blob",
                }
                for workflow in self.checks
            ],
        }
        self.drift = False
        self.head = self.sha
        self.statuses = []
        self.writes = []
        self.fail_jobs_api = False
        self.new_attempt = False
        self.new_run = False
        self.history_reads = 0
        self.workflow_reads = {}

    def _api(self, path: str, method: str = "GET", data: object = None) -> Any:
        if method != "GET":
            self.writes.append((path, data))
            return None
        if "/git/commits/" in path:
            return {"parents": self.parents}
        if f"compare/{self.base}...{self.base}" in path:
            return {"status": self.ancestry}
        if "/compare/" in path:
            return {"status": "ahead", "total_commits": 1, "files": self.paths}
        if "/git/trees/" in path:
            if self.drift and self.sha not in path:
                return {"truncated": False, "tree": []}
            return self.tree
        if "/git/ref/heads/" in path:
            return {"object": {"sha": self.head if self.branch in path else self.base}}
        if path.endswith(f"/pulls/{self.pr['number']}"):
            return self.pr
        if "/status?" in path or path.endswith("/status"):
            return {"statuses": self.statuses}
        if "/files?" in path:
            return self.paths
        for index, workflow in enumerate(self.checks, 1):
            if path.endswith(f"/workflows/{workflow}"):
                return {
                    "id": index,
                    "path": f".github/workflows/{workflow}",
                    "state": "active",
                }
            if f"/workflows/{workflow}/runs?" in path:
                self.history_reads += 1
                self.workflow_reads[workflow] = self.workflow_reads.get(workflow, 0) + 1
                if self.new_attempt and self.workflow_reads[workflow] == 2:
                    self.runs[workflow][0] = {
                        **self.runs[workflow][0],
                        "run_attempt": 2,
                        "status": "in_progress",
                        "conclusion": None,
                    }
                if self.new_run and self.workflow_reads[workflow] == 2:
                    self.runs[workflow].insert(
                        0,
                        {
                            **self.runs[workflow][0],
                            "id": 3,
                            "status": "in_progress",
                            "conclusion": None,
                        },
                    )
                    self.jobs[3] = []
                return {
                    "total_count": len(self.runs[workflow]),
                    "workflow_runs": self.runs[workflow],
                }
        if "/jobs?" in path:
            if self.fail_jobs_api:
                raise subprocess.CalledProcessError(1, ["gh", "api"])
            run_id = int(path.split("/runs/")[1].split("/")[0])
            return {"total_count": len(self.jobs[run_id]), "jobs": self.jobs[run_id]}
        if "/actions/runs/" in path:
            run_id = int(path.rsplit("/", 1)[1])
            for runs in self.runs.values():
                for run in runs:
                    if run["id"] == run_id:
                        return {**run, "run_attempt": 2} if self.new_attempt else run
        raise AssertionError(path)

    def _report(self) -> bool:
        with (
            patch.object(queue, "api", side_effect=self._api),
            patch.object(queue, "SOURCE_CACHE", {}),
            patch.object(queue, "SOURCE_EVIDENCE", {}),
            patch.object(queue, "SOURCE_CHECKS", self.checks),
            patch.object(queue, "WORKFLOWS", list(self.checks)),
        ):
            return queue.reconcile_source(self.pr)

    def test_real_required_jobs_become_linked_results(self) -> None:
        self.assertTrue(self._report())
        self.assertEqual(
            [data["context"] for _, data in self.writes],
            ["Build", "Lint", "DCO sign-off"],
        )
        self.assertTrue(all(data["state"] == "success" for _, data in self.writes))
        self.assertEqual(self.writes[-1][1]["target_url"], self.jobs[2][0]["html_url"])

    def test_actions_source_admission_requires_explicit_opt_in(self) -> None:
        result = subprocess.CompletedProcess([], 0, '[{"bucket":"pass"}]')
        with (
            patch.object(queue, "api", side_effect=self._api),
            patch.object(queue, "SOURCE_CACHE", {}),
            patch.object(queue, "SOURCE_EVIDENCE", {}),
            patch.object(queue, "SOURCE_CHECKS", self.checks),
            patch.object(queue, "WORKFLOWS", list(self.checks)),
            patch.object(
                queue, "graphql", return_value={"node": {"mergeQueueEntry": None}}
            ) as graphql,
            patch.object(queue.subprocess, "run", return_value=result),
        ):
            self.assertTrue(queue.admit(self.pr))
            self.assertEqual(graphql.call_count, 2)
            self.assertEqual(self.writes[-1][1]["context"], queue.ADMISSION)
        self.writes.clear()
        with (
            patch.object(queue, "SOURCE_CHECKS", {}),
            patch.object(queue, "api") as api,
        ):
            self.assertFalse(queue.admit(self.pr))
            api.assert_not_called()

    def test_wrong_author_fork_draft_and_namespace_are_ineligible(self) -> None:
        for key, value in (("id", 9), ("type", "User"), ("login", "someone")):
            with self.subTest(key=key):
                old = self.pr["user"][key]
                self.pr["user"][key] = value
                self.assertFalse(queue.source_identity(self.pr))
                self.pr["user"][key] = old
        self.pr["head"]["repo"]["full_name"] = "attacker/example"
        self.assertFalse(queue.source_identity(self.pr))
        self.pr["head"]["repo"]["full_name"] = queue.REPOSITORY
        self.pr["draft"] = True
        self.assertFalse(queue.source_identity(self.pr))
        self.pr["draft"] = False
        self.pr["head"]["ref"] = "automation/package-updates/example"
        self.assertFalse(queue.source_identity(self.pr))

    def test_wrong_parent_ancestry_scope_and_workflow_are_rejected(self) -> None:
        for invalid in (
            "parent",
            "ancestry",
            "scope",
            "rename",
            "workflow",
            "truncated",
        ):
            with self.subTest(invalid=invalid):
                self.setUp()
                if invalid == "parent":
                    self.parents.append({"sha": "d" * 40})
                if invalid == "ancestry":
                    self.ancestry = "diverged"
                if invalid == "scope":
                    self.paths.append({"filename": ".github/workflows/dco.yml"})
                if invalid == "rename":
                    self.paths[0]["status"] = "renamed"
                if invalid == "workflow":
                    self.drift = True
                if invalid == "truncated":
                    self.tree["truncated"] = True
                self.assertFalse(self._report())
                self.assertEqual(self.writes, [])

    def test_real_failed_pending_skipped_missing_and_wrong_head_jobs_never_pass(
        self,
    ) -> None:
        for invalid in (
            "failure",
            "pending",
            "skipped",
            "missing",
            "head",
            "url",
            "attempt",
        ):
            with self.subTest(invalid=invalid):
                self.setUp()
                if invalid == "failure":
                    self.runs["dco.yml"][0]["conclusion"] = "failure"
                if invalid == "pending":
                    self.runs["dco.yml"][0]["status"] = "in_progress"
                if invalid == "skipped":
                    self.jobs[2][0]["conclusion"] = "skipped"
                if invalid == "missing":
                    self.jobs[2] = []
                if invalid == "head":
                    self.jobs[2][0]["head_sha"] = "other"
                if invalid == "url":
                    self.jobs[2][0]["html_url"] = "https://attacker.example/job"
                if invalid == "attempt":
                    self.new_attempt = True
                if invalid == "new_run":
                    self.new_run = True
                if invalid == "job_attempt":
                    self.jobs[2][0]["run_attempt"] = 2
                if invalid == "job_run":
                    self.jobs[2][0]["run_id"] = 9
                self.assertFalse(self._report())
                self.assertEqual(self.writes, [])

    def test_zero_job_approval_run_does_not_suppress_real_dispatch(self) -> None:
        self.runs["dco.yml"].insert(
            0,
            {
                **self.runs["dco.yml"][0],
                "id": 3,
                "event": "pull_request",
                "conclusion": "action_required",
            },
        )
        self.jobs[3] = []
        self.assertTrue(self._report())
        self.jobs[3] = [{**self.jobs[2][0], "conclusion": "failure"}]
        self.writes.clear()
        self.assertFalse(self._report())
        self.assertEqual(self.writes, [])

    def test_api_error_does_not_write_or_enqueue(self) -> None:
        self.fail_jobs_api = True
        with self.assertRaises(subprocess.CalledProcessError):
            self._report()
        self.assertEqual(self.writes, [])

    def test_failed_new_attempt_invalidates_previous_source_success(self) -> None:
        self.statuses = [
            {
                "context": "DCO sign-off",
                "state": "success",
                "target_url": self.jobs[2][0]["html_url"],
                "description": f"{queue.SOURCE_REPORT} result",
            }
        ]
        self.runs["dco.yml"][0]["conclusion"] = "failure"
        self.assertFalse(self._report())
        self.assertEqual([data["state"] for _, data in self.writes], ["pending"])

    def _attest(self) -> None:
        self.statuses = [
            {
                "context": job["name"],
                "state": "success",
                "target_url": job["html_url"],
                "description": f"{queue.SOURCE_REPORT} result; attempt 1",
                "creator": {"id": 41898282},
            }
            for jobs in self.jobs.values()
            for job in jobs
        ]

    def test_real_failed_new_job_replaces_old_success_with_actual_failure_url(
        self,
    ) -> None:
        self._attest()
        self.runs["dco.yml"][0].update(run_attempt=2, conclusion="failure")
        self.jobs[2][0].update(
            id=25,
            run_attempt=2,
            conclusion="failure",
            html_url=f"https://github.com/{queue.REPOSITORY}/actions/runs/2/job/25",
        )
        self.assertFalse(self._report())
        actual = {data["context"]: data for _, data in self.writes}
        self.assertEqual(actual["DCO sign-off"]["state"], "failure")
        self.assertEqual(
            actual["DCO sign-off"]["target_url"], self.jobs[2][0]["html_url"]
        )
        self.assertEqual(actual["Build"]["state"], "pending")

    def test_producer_rerun_after_collection_invalidates_greens_and_refuses_admission(
        self,
    ) -> None:
        self._attest()
        self.new_attempt = True
        with (
            patch.object(queue, "api", side_effect=self._api),
            patch.object(queue, "SOURCE_CACHE", {}),
            patch.object(queue, "SOURCE_EVIDENCE", {}),
            patch.object(queue, "SOURCE_CHECKS", self.checks),
            patch.object(queue, "WORKFLOWS", list(self.checks)),
            patch.object(queue, "graphql") as graphql,
            patch.object(queue.subprocess, "run") as command,
        ):
            self.assertFalse(queue.admit(self.pr))
        self.assertEqual([data["state"] for _, data in self.writes], ["pending"] * 3)
        graphql.assert_not_called()
        command.assert_not_called()

    def test_rerun_during_status_publication_is_bounded_and_invalidates_new_greens(
        self,
    ) -> None:
        def api(path: str, method: str = "GET", data: object = None) -> Any:
            result = self._api(path, method, data)
            if method == "POST":
                self.statuses.append({**data, "creator": {"id": 41898282}})
                if len(self.writes) == 1:
                    self.runs["ci.yml"][0].update(
                        run_attempt=2, status="in_progress", conclusion=None
                    )
            return result

        with (
            patch.object(queue, "api", side_effect=api),
            patch.object(queue, "SOURCE_CACHE", {}),
            patch.object(queue, "SOURCE_EVIDENCE", {}),
            patch.object(queue, "SOURCE_CHECKS", self.checks),
            patch.object(queue, "WORKFLOWS", list(self.checks)),
        ):
            self.assertFalse(queue.reconcile_source(self.pr))
        self.assertEqual(
            [data["state"] for _, data in self.writes],
            ["success"] * 3 + ["pending"] * 3,
        )

    def test_untrusted_cached_status_cannot_replace_actual_job_evidence(self) -> None:
        self._attest()
        self.statuses[-1]["creator"]["id"] = 9
        self.fail_jobs_api = True
        with self.assertRaises(subprocess.CalledProcessError):
            self._report()
        self.assertEqual(self.writes, [])

    def test_changed_ref_and_missing_dco_configuration_do_not_report(self) -> None:
        self.head = "d" * 40
        self.assertFalse(self._report())
        self.assertEqual(self.writes, [])
        self.setUp()
        self.checks["dco.yml"] = ["other"]
        self.assertFalse(self._report())
        self.assertEqual(self.writes, [])


class SourceBudgetTests(unittest.TestCase):
    """Count API requests for six callers with the actual sixteen-job contract."""

    def _fixtures(self) -> list[PackageSourceTests]:
        checks = {
            "ci.yml": [
                "Build (x86_64-linux)",
                "Build (aarch64-linux)",
                "Build (aarch64-darwin)",
                "Lint and security",
                "Flake checks (x86_64-linux)",
                "Flake checks (aarch64-linux)",
                "Flake checks (aarch64-darwin)",
                "NUR compatibility / NUR (locked)",
                "NUR compatibility / NUR (unstable)",
                "Flake lock health",
                "Build (x86_64-linux 2/2)",
                "Build (aarch64-linux 2/2)",
            ],
            "codeql.yml": ["Analyze (python) / analyze"],
            "dependency-review.yml": ["dependency-review / review"],
            "dco.yml": ["DCO sign-off"],
            "fuzz.yml": ["Fuzz font path validation"],
        }
        fixtures = []
        for number in range(7, 13):
            fixture = PackageSourceTests()
            fixture.setUp()
            fixture.checks = checks
            fixture.sha = f"{number:040x}"
            fixture.head = fixture.sha
            fixture.branch = f"automation/package-updates-example{number}"
            fixture.pr["number"] = number
            fixture.pr["head"].update(sha=fixture.sha, ref=fixture.branch)
            fixture.paths[0]["filename"] = f"pkgs/by-name/ex/example{number}/source.nix"
            fixture.tree["tree"] = [
                {
                    "path": f".github/workflows/{workflow}",
                    "sha": "c" * 40,
                    "mode": "100644",
                    "type": "blob",
                }
                for workflow in checks
            ]
            fixture.runs, fixture.jobs = {}, {}
            for workflow_id, workflow in enumerate(checks, 1):
                run_id = number * 10 + workflow_id
                fixture.runs[workflow] = [
                    {
                        "id": run_id,
                        "workflow_id": workflow_id,
                        "path": f".github/workflows/{workflow}",
                        "head_sha": fixture.sha,
                        "head_branch": fixture.branch,
                        "event": "workflow_dispatch",
                        "repository": {"full_name": queue.REPOSITORY},
                        "head_repository": {"full_name": queue.REPOSITORY},
                        "status": "completed",
                        "conclusion": "success",
                        "run_attempt": 1,
                    }
                ]
                fixture.jobs[run_id] = [
                    {
                        "id": run_id * 100 + index,
                        "run_id": run_id,
                        "run_attempt": 1,
                        "name": name,
                        "head_sha": fixture.sha,
                        "status": "completed",
                        "conclusion": "success",
                        "html_url": f"https://github.com/{queue.REPOSITORY}/actions/runs/{run_id}/job/{run_id * 100 + index}",
                    }
                    for index, name in enumerate(checks[workflow])
                ]
            fixture._attest()
            fixtures.append(fixture)
        return fixtures

    def _case(self, mode: str) -> dict[str, int]:
        fixtures = self._fixtures()
        counts = {"get": 0, "write": 0, "jobs": 0}
        if mode in {"queued", "admitted_event"}:
            for fixture in fixtures:
                fixture.statuses.append(
                    {"context": queue.ADMISSION, "state": "success"}
                )
        if mode == "initial_event":
            fixtures[0].statuses = []

        def api(path: str, method: str = "GET", data: object = None) -> Any:
            counts["get" if method == "GET" else "write"] += 1
            if "/jobs?" in path:
                counts["jobs"] += 1
            for fixture in fixtures:
                if (
                    fixture.sha in path
                    or fixture.branch in path
                    or path.endswith(f"/pulls/{fixture.pr['number']}")
                    or f"/pulls/{fixture.pr['number']}/files?" in path
                ):
                    return fixture._api(path, method, data)
                for run_id in fixture.jobs:
                    if f"/runs/{run_id}/" in path or path.endswith(f"/runs/{run_id}"):
                        return fixture._api(path, method, data)
            return fixtures[0]._api(path, method, data)

        def graphql(query: str, **variables: str) -> dict[str, Any]:
            counts["get"] += 1
            return {"node": {"mergeQueueEntry": {"id": "queue"}}}

        head = fixtures[0].sha if mode.endswith("event") else ""
        activity = (
            "in_progress"
            if mode in {"admitted_event", "initial_event"}
            else "completed"
        )
        with (
            patch.object(queue, "api", side_effect=api),
            patch.object(queue, "graphql", side_effect=graphql),
            patch.object(queue, "SOURCE_CACHE", {}),
            patch.object(queue, "SOURCE_EVIDENCE", {}),
            patch.object(queue, "SOURCE_CHECKS", fixtures[0].checks),
            patch.object(queue, "WORKFLOWS", list(fixtures[0].checks)),
            patch.object(queue, "SOURCE_HEAD", head),
            patch.object(queue, "SOURCE_ACTIVITY", activity),
        ):
            for fixture in fixtures:
                if mode == "idle":
                    self.assertTrue(queue.reconcile_source(fixture.pr))
                else:
                    queue.admit(fixture.pr)
        return counts

    def test_six_idle_source_prs_fit_hourly_recovery_budget(self) -> None:
        counts = self._case("idle")
        self.assertLessEqual(counts["get"], 110)
        self.assertEqual(counts["write"], 0)
        self.assertEqual(counts["jobs"], 0)

    def test_six_admitted_sources_skip_unrelated_polling(self) -> None:
        self.assertEqual(self._case("queued"), {"get": 12, "write": 0, "jobs": 0})

    def test_source_event_inspects_only_its_exact_head(self) -> None:
        counts = self._case("completed_event")
        self.assertLessEqual(counts["get"], 25)
        self.assertEqual(counts["write"], 0)
        self.assertEqual(counts["jobs"], 0)

    def test_rerun_event_invalidates_even_an_admitted_source(self) -> None:
        counts = self._case("admitted_event")
        self.assertEqual(counts, {"get": 33, "write": 16, "jobs": 0})

    def test_initial_requested_event_with_no_attestation_stops_immediately(
        self,
    ) -> None:
        self.assertEqual(self._case("initial_event"), {"get": 1, "write": 0, "jobs": 0})


if __name__ == "__main__":
    unittest.main()
