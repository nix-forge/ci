"""Exercise success evidence independently of derivation equality or network state."""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import base_check_evidence as evidence

REVISION = "a" * 40


def check(
    *,
    ident=1,
    status="completed",
    conclusion="success",
    revision=REVISION,
    app="github-actions",
    name="Repository tests",
):
    return {
        "id": ident,
        "name": name,
        "head_sha": revision,
        "status": status,
        "conclusion": conclusion,
        "app": {"slug": app},
    }


class BaseEvidenceTests(unittest.TestCase):
    def test_exact_success_from_owning_actions_is_required(self):
        self.assertTrue(
            evidence.successful_checks([check()], REVISION, ["Repository tests"])
        )
        for changed in [
            check(conclusion="failure"),
            check(conclusion="skipped"),
            check(status="in_progress"),
            check(revision="b" * 40),
            check(app="other-app"),
            check(name="Unrelated"),
        ]:
            self.assertFalse(
                evidence.successful_checks([changed], REVISION, ["Repository tests"])
            )
        self.assertFalse(evidence.successful_checks([check()], REVISION, []))
        self.assertFalse(
            evidence.successful_checks(
                [check()], REVISION, ["Repository tests", "Other required check"]
            )
        )

    def test_newer_failed_or_pending_attempt_invalidates_old_success(self):
        for newer in [
            check(ident=2, conclusion="failure"),
            check(ident=2, status="in_progress", conclusion=None),
        ]:
            for entries in [[check(), newer], [newer, check()]]:
                self.assertFalse(
                    evidence.successful_checks(entries, REVISION, ["Repository tests"])
                )

    @mock.patch.object(evidence, "urlopen")
    def test_bounded_api_read_and_auth_header_are_separate_from_url(self, request):
        request.return_value = io.StringIO(json.dumps({"check_runs": [check()]}))
        self.assertTrue(
            evidence.validated_base(
                "nix-forge/nix-conf", REVISION, ["Repository tests"], "synthetic-token"
            )
        )
        actual = request.call_args.args[0]
        self.assertTrue(
            actual.full_url.startswith(
                "https://api.github.com/repos/nix-forge/nix-conf/commits/" + REVISION
            )
        )
        self.assertNotIn("synthetic-token", actual.full_url)
        self.assertEqual(actual.get_header("Authorization"), "Bearer synthetic-token")
        self.assertEqual(request.call_args.kwargs["timeout"], 10)

    @mock.patch.object(evidence, "urlopen")
    def test_unavailable_malformed_or_truncated_inventory_never_authorizes_skip(
        self, request
    ):
        for failure in [
            TimeoutError(),
            HTTPError("https://api.github.com", 403, "forbidden", {}, None),
        ]:
            request.side_effect = failure
            self.assertFalse(
                evidence.validated_base(
                    "nix-forge/nix-conf", REVISION, ["Repository tests"]
                )
            )
        request.side_effect = None
        for payload in [
            {"check_runs": None},
            {"check_runs": []},
            {"check_runs": [{"id": "bad"}]},
        ]:
            request.return_value = io.StringIO(json.dumps(payload))
            self.assertFalse(
                evidence.validated_base(
                    "nix-forge/nix-conf", REVISION, ["Repository tests"]
                )
            )
        request.side_effect = [
            io.StringIO(json.dumps({"check_runs": [check()] * 100})) for _ in range(5)
        ]
        request.reset_mock()
        self.assertFalse(
            evidence.validated_base(
                "nix-forge/nix-conf", REVISION, ["Repository tests"]
            )
        )
        self.assertEqual(request.call_count, 5)

    @mock.patch.object(evidence, "urlopen")
    def test_invalid_source_or_unconfigured_owner_does_not_contact_api(self, request):
        for repository, revision, checks in [
            ("../repo", REVISION, ["Repository tests"]),
            ("nix-forge/nix-conf", "main", ["Repository tests"]),
            ("nix-forge/nix-conf", REVISION, []),
        ]:
            self.assertFalse(evidence.validated_base(repository, revision, checks))
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
