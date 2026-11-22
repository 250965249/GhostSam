from __future__ import annotations

import hashlib
import io
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_manager_apks import MANAGERS
from verify_manager_pairing import OFFICIAL, PairingError, positive_integer, verify_files, verify_pair

MANAGER_COMMIT = 'a' * 40
KERNEL_COMMIT = 'b' * 40


def manager(variant='default'):
    expected = MANAGERS[variant]
    return {
        'repo': OFFICIAL[variant], 'signature_verified': True,
        'certificate_sha256': expected['certificate'], 'package': expected['package'],
        'file': expected['file'], 'ci': {'commit': MANAGER_COMMIT},
        'kind': 'ci', 'uapi_version': 6, 'version_code': 32735,
    }


def kernel():
    return {
        'source': '250965249/KernelSU', 'commit': KERNEL_COMMIT, 'version_code': 32740,
        'kernel_version_code': '32735', 'kernel_uapi_version': '6',
        'build_kind': 'samsung-ci', 'producer_run_id': '42',
        'official_ci_commit': MANAGER_COMMIT, 'merge_auto_resolved': '',
        'producer_artifact_id': '99', 'producer_artifact_digest': 'sha256:' + 'd' * 64,
        'sha256': 'c' * 64,
    }


def api(path):
    if '/actions/runs/' in path:
        return {'status': 'completed', 'conclusion': 'success', 'event': 'push',
                'head_repository': {'full_name': '250965249/KernelSU'}, 'head_sha': KERNEL_COMMIT,
                'head_branch': 'samsung', 'path': '.github/workflows/auto-sync-build.yml'}
    if '/actions/artifacts/' in path:
        return {'id': 99, 'name': 'ksud-aarch64-linux-android', 'expired': False,
                'digest': 'sha256:' + 'd' * 64,
                'workflow_run': {'id': 42, 'head_sha': KERNEL_COMMIT}}
    return {'behind_by': 0, 'merge_base_commit': {'sha': MANAGER_COMMIT}}


class PairingTests(unittest.TestCase):
    def pair(self, m=None, k=None, api_reader=api, uapi=6):
        return verify_pair('default', m or manager(), k or kernel(), api_reader, lambda repo, commit: uapi,
                           lambda repo, artifact_id, digest: 'c' * 64)

    def test_same_uapi_and_verified_build_receipt_are_required(self):
        result = self.pair()
        self.assertTrue(result['contract_verified'])
        self.assertEqual(32740, result['ksud_version_code'])
        self.assertEqual(32735, result['kernel_version_code'])
        self.assertEqual('not-tested', result['runtime_compatibility'])

    def test_uapi_6_manager_cannot_be_published_with_uapi_5_kernel(self):
        with self.assertRaisesRegex(PairingError, 'UAPI mismatch'):
            self.pair(uapi=5)

    def test_unsigned_or_wrong_identity_is_rejected(self):
        for key, value in (('signature_verified', False), ('package', 'wrong.package'), ('certificate_sha256', 'c' * 64), ('repo', 'some/fork')):
            m = manager()
            m[key] = value
            with self.subTest(key=key), self.assertRaises(PairingError):
                self.pair(m=m)

    def test_generic_official_binary_is_not_a_samsung_build(self):
        k = kernel()
        k['source'] = 'tiann/KernelSU'
        with self.assertRaisesRegex(PairingError, 'generic or unknown'):
            self.pair(k=k)

    def test_missing_producer_receipt_is_rejected(self):
        k = kernel()
        del k['build_kind']
        with self.assertRaisesRegex(PairingError, 'receipt is missing'):
            self.pair(k=k)

    def test_receipt_uapi_and_official_ci_snapshot_must_match(self):
        for key, value in (('kernel_uapi_version', '5'), ('official_ci_commit', 'd' * 40)):
            k = kernel()
            k[key] = value
            with self.subTest(key=key), self.assertRaises(PairingError):
                self.pair(k=k)

    def test_running_failed_pr_or_wrong_sha_producer_is_rejected(self):
        for change in ({'status': 'in_progress'}, {'conclusion': 'failure'}, {'event': 'pull_request'}, {'head_sha': 'd' * 40}, {'path': '.github/workflows/lint-only.yml'}, {'head_branch': 'not-samsung'}):
            def changed(path):
                result = api(path)
                if '/actions/runs/' in path:
                    result.update(change)
                return result
            with self.subTest(change=change), self.assertRaises(PairingError):
                self.pair(api_reader=changed)

    def test_wrong_artifact_receipt_or_payload_digest_is_rejected(self):
        for change in ({'id': 100}, {'name': 'lint-report'}, {'expired': True}, {'digest': 'sha256:' + 'e' * 64},
                       {'workflow_run': {'id': 43, 'head_sha': KERNEL_COMMIT}}):
            def changed(path):
                result = api(path)
                if '/actions/artifacts/' in path:
                    result.update(change)
                return result
            with self.subTest(change=change), self.assertRaises(PairingError):
                self.pair(api_reader=changed)
        with self.assertRaisesRegex(PairingError, 'APK payload is not'):
            verify_pair('default', manager(), kernel(), api, lambda repo, commit: 6,
                        lambda repo, artifact_id, digest: 'e' * 64)

    def test_missing_ancestry_and_auto_resolved_kernel_conflicts_are_rejected(self):
        with self.assertRaises(PairingError):
            self.pair(api_reader=lambda path: api(path) if '/actions/runs/' in path else {'behind_by': 1})
        k = kernel()
        k['merge_auto_resolved'] = 'kernel/core/init.c'
        with self.assertRaisesRegex(PairingError, 'merge conflicts'):
            self.pair(k=k)

    def test_existing_pinned_nova_pair_does_not_invent_a_kernel_version(self):
        m = manager('sukisu')
        del m['ci']
        m.update(kind='release', tag='v3.0.0', source_commit=MANAGER_COMMIT, version_code=35000)
        k = {'source': '250965249/SukiSU-Ultra', 'commit': KERNEL_COMMIT, 'version_code': 35046}
        result = verify_pair('sukisu', m, k, api, lambda repo, commit: 6)
        self.assertIsNone(result['kernel_version_code'])
        self.assertEqual(35046, result['ksud_version_code'])

    def test_booleans_and_invalid_numbers_are_not_versions_or_run_ids(self):
        for value in (True, False, 0, -1, '', '0', '1x'):
            with self.subTest(value=value), self.assertRaises(PairingError):
                positive_integer(value, 'field')

    def test_apk_payload_and_manager_digests_bind_the_contract_to_files(self):
        data = bytearray(64)
        data[:6] = b'\x7fELF\x02\x01'
        data[18:20] = (183).to_bytes(2, 'little')
        manager_data = b'signed manager fixture'
        m = manager('sukisu')
        m['sha256'] = hashlib.sha256(manager_data).hexdigest()
        k = {'sha256': hashlib.sha256(data).hexdigest()}
        apk = io.BytesIO()
        with zipfile.ZipFile(apk, 'w') as archive:
            archive.writestr('assets/payloads/ksud/ksud-sukisu', data)
        with patch.object(Path, 'is_file', return_value=True), patch.object(Path, 'read_bytes', return_value=manager_data):
            verify_files(apk, Path('managers'), 'sukisu', m, k)
            with self.assertRaisesRegex(PairingError, 'payload differs'):
                verify_files(apk, Path('managers'), 'sukisu', m, {'sha256': '0' * 64})
            with self.assertRaisesRegex(PairingError, 'manager changed'):
                verify_files(apk, Path('managers'), 'sukisu', {**m, 'sha256': '0' * 64}, k)


if __name__ == '__main__':
    unittest.main()
