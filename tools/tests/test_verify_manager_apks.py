from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_manager_apks import ManagerVerificationError, parse_badging, parse_signature

CERT = "a" * 64
SIGNATURE = f"Verified using v2 scheme (APK Signature Scheme v2): true\nSigner #1 certificate SHA-256 digest: {CERT}\n"
PACKAGE = "org.example.manager"
BADGING = f"package: name='{PACKAGE}' versionCode='35000' versionName='3.0.0' compileSdkVersion='36'\n"


class ManagerIdentityTests(unittest.TestCase):
    def test_signature_requires_crypto_verification_and_producer_cert(self):
        self.assertTrue(parse_signature(SIGNATURE, CERT)["signature_verified"])
        with self.assertRaises(ManagerVerificationError):
            parse_signature(SIGNATURE, "b" * 64)
        with self.assertRaises(ManagerVerificationError):
            parse_signature(SIGNATURE.replace(": true", ": false"), CERT)

    def test_multiple_signers_are_not_silently_accepted(self):
        with self.assertRaises(ManagerVerificationError):
            parse_signature(SIGNATURE + f"Signer #2 certificate SHA-256 digest: {CERT}\n", CERT)

    def test_package_name_must_match_the_staged_variant(self):
        self.assertEqual(35000, parse_badging(BADGING, PACKAGE)["version_code"])
        with self.assertRaises(ManagerVerificationError):
            parse_badging(BADGING, "org.other.manager")

    def test_missing_or_invalid_version_code_is_rejected(self):
        for value in ("0", "invalid", ""):
            with self.subTest(value=value), self.assertRaises(ManagerVerificationError):
                parse_badging(BADGING.replace("versionCode='35000'", f"versionCode='{value}'"), PACKAGE)


if __name__ == "__main__":
    unittest.main()
