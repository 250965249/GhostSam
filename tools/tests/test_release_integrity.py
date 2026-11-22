from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch_managers import main as fetch_main
from report_payloads import blocking_problems


class ReleaseIntegrityTests(unittest.TestCase):
    def test_only_precise_upstream_lag_may_be_overridden(self):
        lag = 'next: producer is 2 upstream commit(s) behind owner/repo - the scheduled sync has not caught up'
        self.assertEqual([], blocking_problems({'problems': [lag]}))
        for problem in (
            'next: sidecar sha256 does not match the downloaded binary',
            'next: sidecar version_code 33355 != ELF stamp 33404',
            'next: no ksud-next.version published by owner/repo',
            'default: payload ksud is missing',
            'next: unknown validation failure',
        ):
            with self.subTest(problem=problem):
                self.assertEqual([problem], blocking_problems({'problems': [lag, problem]}))

    def test_age_warning_is_not_misreported_as_identity_failure(self):
        self.assertEqual([], blocking_problems({'problems': [], 'warnings': ['older than 48h']}))

    def test_null_list_or_invalid_variants_contract_cannot_fetch_latest(self):
        for data in (None, [], {'variants': None}, {'variants': []}):
            with self.subTest(data=data), patch.object(Path, 'read_text', return_value=json.dumps(data)), \
                 patch.object(Path, 'mkdir') as mkdir, patch('fetch_managers.fetch_ci') as fetch, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(1, fetch_main(['--payload-lock', 'contract.json', '--only', 'next']))
                fetch.assert_not_called()
                mkdir.assert_not_called()

    def test_missing_official_ci_snapshot_is_rejected_before_network_fetch(self):
        contract = {'variants': {'next': {'version_name': '3.4.2'}}}
        with patch.object(Path, 'read_text', return_value=json.dumps(contract)), \
             patch.object(Path, 'mkdir'), patch.object(Path, 'write_text'), \
             patch('fetch_managers.fetch_ci') as fetch, \
             contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(1, fetch_main(['--payload-lock', 'contract.json', '--only', 'next']))
            fetch.assert_not_called()

    def test_exact_shared_snapshot_is_passed_to_the_ci_resolver(self):
        sha = 'a' * 40
        contract = {'variants': {'next': {'version_name': '3.4.2', 'official_ci_commit': sha}}}
        with patch.object(Path, 'read_text', return_value=json.dumps(contract)), \
             patch.object(Path, 'mkdir'), patch.object(Path, 'write_text'), \
             patch('fetch_managers.fetch_ci', return_value={'variant': 'next'}) as fetch, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, fetch_main(['--payload-lock', 'contract.json', '--only', 'next']))
            self.assertEqual(sha, fetch.call_args.kwargs['expected_revision'])

    def test_catch_up_excludes_drafts_and_publication_requires_valid_inputs(self):
        workflow = (Path(__file__).resolve().parents[2] / '.github/workflows/auto-build.yml').read_text(encoding='utf-8')
        self.assertIn('release list -R "$GITHUB_REPOSITORY" --exclude-drafts', workflow)
        self.assertIn("steps.manifest.outputs.invalid == 'true'", workflow)
        self.assertIn("needs.build.outputs.invalid != 'true'", workflow)
        self.assertIn("needs.managers.result == 'success'", workflow)
        self.assertLess(workflow.index('Check all release inputs before any publication'), workflow.index('Stage the complete release as a draft'))
        self.assertLess(workflow.index('Stage the complete release as a draft'), workflow.index('Publish only after every asset was uploaded'))


if __name__ == '__main__':
    unittest.main()
