from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from github_ci import CiSourceError, provenance, resolve_ci, select_artifact, select_run

REPO = "upstream/root-manager"
SHA = "a" * 40


def run(**overrides):
    result = {
        "id": 100, "head_branch": "main", "head_sha": SHA,
        "head_repository": {"full_name": REPO}, "event": "push",
        "status": "completed", "conclusion": "success",
        "path": ".github/workflows/build-manager.yml",
        "created_at": "2026-11-21T08:00:00Z", "updated_at": "2026-11-21T08:10:00Z",
        "pull_requests": [], "run_attempt": 1,
        "html_url": "https://github.com/upstream/root-manager/actions/runs/100",
    }
    result.update(overrides)
    return result


def artifact(**overrides):
    result = {
        "id": 200, "name": "manager", "expired": False, "size_in_bytes": 123456,
        "digest": "sha256:" + "b" * 64,
        "workflow_run": {"id": 100, "head_sha": SHA},
    }
    result.update(overrides)
    return result


class CiIdentityTests(unittest.TestCase):
    def select(self, runs, **kwargs):
        return select_run(runs, REPO, "main", "build-manager.yml", **kwargs)

    def test_resolve_ci_branch_query_keeps_the_slash_literal(self):
        # GitHub's workflow-runs `branch` filter never matches a %2F-encoded
        # slash (ghostsam/adapter: zero runs encoded, twelve literal). The
        # listing URL must carry the branch's slash unencoded.
        captured: list[str] = []

        def api(path: str):
            captured.append(path)
            if "/workflows/" in path:
                return {"workflow_runs": [run(head_branch="ghostsam/adapter",
                                              head_repository={"full_name": "250965249/MapleSU"},
                                              path=".github/workflows/ghostsam-build.yml")],
                        "total_count": 1}
            return {"artifacts": [artifact()], "total_count": 1}

        resolve_ci(api, "250965249/MapleSU", "ghostsam/adapter", "ghostsam-build.yml", "manager")
        self.assertTrue(captured)
        self.assertIn("branch=ghostsam/adapter&", captured[0])
        self.assertNotIn("ghostsam%2Fadapter", captured[0])

    def test_newest_trusted_completed_run_is_selected(self):
        old = run(id=90, created_at="2026-11-20T08:00:00Z")
        current = run()
        self.assertEqual(current, self.select([old, current]))

    def test_pr_run_is_never_a_release_source(self):
        with self.assertRaises(CiSourceError):
            self.select([run(event="pull_request")])

    def test_downstream_pr_associations_do_not_reject_upstream_push(self):
        upstream_push = run(pull_requests=[{"number": 1, "url": "https://api.github.com/repos/downstream/fork/pulls/1"}])
        self.assertEqual(upstream_push, self.select([upstream_push]))

    def test_fork_branch_and_workflow_are_checked(self):
        for change in (
            {"head_repository": {"full_name": "attacker/fork"}},
            {"head_branch": "dev"}, {"path": ".github/workflows/unrelated.yml"},
        ):
            with self.subTest(change=change), self.assertRaises(CiSourceError):
                self.select([run(**change)])

    def test_failed_or_unfinished_build_is_not_a_success(self):
        for change in ({"conclusion": "failure"}, {"status": "in_progress"}):
            with self.subTest(change=change), self.assertRaises(CiSourceError):
                self.select([run(**change)])

    def test_exact_revision_never_falls_back_to_newer_manager(self):
        exact = run(id=90, created_at="2026-11-20T08:00:00Z")
        later = run(head_sha="c" * 40)
        self.assertEqual(exact, self.select([exact, later], expected_revision=SHA))
        with self.assertRaises(CiSourceError):
            self.select([later], expected_revision=SHA)

    def test_short_revision_is_not_an_identity(self):
        with self.assertRaises(CiSourceError):
            self.select([run()], expected_revision="abcdef0")

    def test_missing_timezone_is_rejected(self):
        with self.assertRaises(CiSourceError):
            self.select([run(created_at="2026-11-21T08:00:00")])

    def test_expired_artifact_is_not_replaced_with_an_older_run(self):
        calls = []
        def api(path):
            calls.append(path)
            if "/artifacts?" in path:
                return {"artifacts": [artifact(expired=True)]}
            return {"workflow_runs": [run(), run(id=90, created_at="2026-11-20T08:00:00Z")]}
        with self.assertRaises(CiSourceError):
            resolve_ci(api, REPO, "main", "build-manager.yml", "manager")
        self.assertTrue(any("/runs/100/artifacts?" in p for p in calls))
        self.assertFalse(any("/runs/90/artifacts?" in p for p in calls))

    def test_artifact_sha_and_run_must_match(self):
        for identity in ({"id": 101, "head_sha": SHA}, {"id": 100, "head_sha": "c" * 40}):
            with self.subTest(identity=identity), self.assertRaises(CiSourceError):
                select_artifact([artifact(workflow_run=identity)], "manager", run())

    def test_duplicate_or_empty_artifact_is_rejected(self):
        with self.assertRaises(CiSourceError):
            select_artifact([artifact(), artifact(id=201)], "manager", run())
        with self.assertRaises(CiSourceError):
            select_artifact([artifact(size_in_bytes=0)], "manager", run())

    def test_provenance_does_not_claim_runtime_validation(self):
        result = provenance(REPO, "build-manager.yml", run(), artifact())
        self.assertEqual(SHA, result["commit"])
        self.assertEqual(100, result["run_id"])
        self.assertEqual(200, result["artifact_id"])
        self.assertEqual("not-tested", result["runtime_compatibility"])

    def test_pagination_finds_an_exact_revision_without_truncation_claim(self):
        calls = []
        def api(path):
            calls.append(path)
            if "/artifacts?" in path:
                return {"artifacts": [artifact()]}
            if path.endswith("page=1"):
                return {"workflow_runs": [run(id=101, head_sha="c" * 40)] * 100}
            return {"workflow_runs": [run()]}
        found, asset = resolve_ci(api, REPO, "main", "build-manager.yml", "manager", expected_revision=SHA)
        self.assertEqual(100, found["id"])
        self.assertEqual(200, asset["id"])
        self.assertTrue(any("page=2" in p for p in calls))

    def test_pagination_cap_reports_incomplete_coverage(self):
        def api(path):
            return {"workflow_runs": [run(head_sha="c" * 40)] * 100}
        with self.assertRaisesRegex(CiSourceError, "older history was not inspected"):
            resolve_ci(api, REPO, "main", "build-manager.yml", "manager", expected_revision=SHA, max_pages=1)


if __name__ == "__main__":
    unittest.main()
