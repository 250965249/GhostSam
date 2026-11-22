from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_upstream_ci import collect_delta, compare_histories
from github_ci import CiSourceError

ANCHOR = "a" * 40
OFFICIAL = "b" * 40
PRODUCER = "c" * 40


def delta(commits, **overrides):
    result = {
        "merge_base_commit": {"sha": ANCHOR}, "behind_by": 0,
        "ahead_by": len(commits), "commits": [{"sha": c} for c in commits],
    }
    result.update(overrides)
    return result


class SourceAuditTests(unittest.TestCase):
    def test_intermediate_fork_sync_does_not_equal_official_sync(self):
        missing = "d" * 40
        local = "e" * 40
        def api(path):
            return delta([OFFICIAL, missing]) if "/upstream/repo/" in path else delta([OFFICIAL, local])
        result = compare_histories(api, "upstream/repo", "owner/fork", ANCHOR, OFFICIAL, PRODUCER)
        self.assertEqual(1, result["official_commits_missing"])
        self.assertEqual([missing], result["missing_commits"])
        self.assertFalse(result["source_caught_up"])
        self.assertFalse(result["patch_semantics_reviewed"])

    def test_complete_ancestry_still_does_not_claim_patch_review(self):
        def api(path):
            return delta([OFFICIAL]) if "/upstream/repo/" in path else delta([OFFICIAL, PRODUCER])
        result = compare_histories(api, "upstream/repo", "owner/fork", ANCHOR, OFFICIAL, PRODUCER)
        self.assertTrue(result["source_caught_up"])
        self.assertFalse(result["patch_semantics_reviewed"])

    def test_wrong_common_ancestor_is_rejected(self):
        def api(path):
            return delta([OFFICIAL], merge_base_commit={"sha": "f" * 40})
        with self.assertRaises(CiSourceError):
            collect_delta(api, "upstream/repo", ANCHOR, OFFICIAL)

    def test_truncated_history_cannot_report_zero_missing(self):
        def api(path):
            return delta([OFFICIAL], ahead_by=200)
        with self.assertRaises(CiSourceError):
            collect_delta(api, "upstream/repo", ANCHOR, OFFICIAL)

    def test_paginated_commits_are_complete_and_unique(self):
        commits = [f"{n:040x}" for n in range(200)]
        def api(path):
            return delta(commits[:100] if path.endswith("page=1") else commits[100:], ahead_by=200)
        self.assertEqual(set(commits), collect_delta(api, "upstream/repo", ANCHOR, OFFICIAL))


if __name__ == "__main__":
    unittest.main()
