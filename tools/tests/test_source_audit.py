from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_upstream_ci import BASELINES, audit, collect_delta, compare_histories, main as audit_main
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
    def source_report(self, reader, manager_uapi=6):
        records = [
            {'variant': variant, 'pairing': 'not-verified', 'signature_verified': True,
             'uapi_version': manager_uapi, 'ci': {'repository': spec['official'], 'commit': OFFICIAL}}
            for variant, spec in BASELINES.items()
        ]
        def histories(*args):
            return {'official_commits_missing': 1, 'source_caught_up': False, 'patch_semantics_reviewed': False}
        with patch('audit_upstream_ci.compare_histories', side_effect=histories):
            return audit(records, lambda path: {'object': {'sha': PRODUCER}}, reader)

    def test_missing_compatible_build_is_not_a_network_failure(self):
        report = self.source_report(lambda repo, commit: 5)
        self.assertTrue(report['audit_complete'])
        self.assertEqual([], report['retrieval_errors'])
        for entry in report['variants'].values():
            self.assertEqual('different_contract', entry['uapi_status'])
            self.assertIn('uapi_contract_mismatch', entry['not_ready_reasons'])
            self.assertFalse(entry['release_ready'])

    def test_header_retrieval_failure_is_an_incomplete_audit(self):
        def failed(repo, commit):
            raise RuntimeError('header download failed')
        report = self.source_report(failed)
        self.assertFalse(report['audit_complete'])
        self.assertEqual(2, len(report['retrieval_errors']))
        for entry in report['variants'].values():
            self.assertEqual('unavailable', entry['uapi_status'])
            self.assertIsNone(entry['uapi_compatible'])
            self.assertIn('uapi_verification_unavailable', entry['not_ready_reasons'])

    def test_incomplete_audit_cli_returns_failure_not_success(self):
        def failed(repo, commit):
            raise RuntimeError('header download failed')
        report = self.source_report(failed)
        with patch.object(Path, 'read_text', return_value='[]'), patch.object(Path, 'write_text') as write, \
             patch('audit_upstream_ci.audit', return_value=report), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1, audit_main(['--managers', 'manager.json', '--out', 'audit.json']))
            write.assert_called_once()

    def test_smaller_manager_uapi_is_not_automatically_a_reviewed_contract(self):
        report = self.source_report(lambda repo, commit: 6, manager_uapi=5)
        self.assertTrue(report['audit_complete'])
        self.assertFalse(report['variants']['default']['uapi_compatible'])

    def test_matching_numeric_uapi_does_not_prove_device_or_receipt_readiness(self):
        report = self.source_report(lambda repo, commit: 6)
        entry = report['variants']['default']
        self.assertTrue(entry['uapi_compatible'])
        self.assertEqual('equal_contract', entry['uapi_status'])
        self.assertFalse(entry['release_ready'])
        self.assertIn('producer_receipt_not_verified', entry['not_ready_reasons'])

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
