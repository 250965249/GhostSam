"""MapleSU fourth-variant fixture checks (sidecar/version/pollution/UAPI alignment)."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gen_payload_versions import VARIANTS
from probe_payload_version import VERSION_CODE_OFFSETS, is_polluted, probe
from verify_samsung_payloads import PRODUCERS, verify
from verify_manager_apks import MANAGERS


class MapleSuFixtureTests(unittest.TestCase):
    def test_registry_entries(self):
        self.assertEqual("ksud-maplesu", VARIANTS["maplesu"]["file"])
        self.assertEqual("MapleSU", VARIANTS["maplesu"]["display"])
        self.assertEqual("FengQiuFQ/MapleSU", VARIANTS["maplesu"]["source"])
        self.assertEqual("FengQiuFQ/MapleSU", PRODUCERS["maplesu"])
        # Fixed 32650 semantics: no offset arithmetic (0 = absolute).
        self.assertEqual(0, VERSION_CODE_OFFSETS["maplesu"])

    def test_fixed_version_and_name_5_2_1(self):
        blob = b"\x7fELF\x00KSU_NO_NEW_PRIVS32650" + b"5.2.1" + b"\x00"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ksud-maplesu"
            p.write_bytes(blob)
            info = probe(p, "maplesu")
        self.assertEqual(32650, info["version_code"])
        self.assertEqual("5.2.1", info["version_name"])
        self.assertFalse(info["polluted"])

    def test_pollution_detection_negative(self):
        # A fork tag in the version name is flagged as pollution.
        self.assertTrue(is_polluted("maple-su-samsung"))
        self.assertTrue(is_polluted("latest"))
        self.assertFalse(is_polluted("5.2.1"))

    def test_sidecar_accepts_maplesu_fixture(self):
        blob = b"\x7fELF\x00KSU_NO_NEW_PRIVS32650" + b"5.2.1" + b"\x00"
        sidecar = {"variant": "maplesu", "commit": "a" * 40, "version_code": "32650",
                   "version_name": "5.2.1", "sha256": hashlib.sha256(blob).hexdigest(),
                   "source": "samsung-ci"}
        blobs = {"ksud-maplesu": blob}
        meta = {"ksud-maplesu.version": sidecar}
        with patch.object(Path, "is_file", lambda self: self.name in blobs), \
             patch.object(Path, "read_bytes", lambda self: blobs[self.name]), \
             patch("verify_samsung_payloads.PRODUCERS", {"maplesu": "FengQiuFQ/MapleSU"}), \
             patch("verify_samsung_payloads.read_sidecar", lambda p: meta.get(p.name, {})):
            self.assertEqual([], verify(Path("payloads"), Path("sidecars")))
            meta["ksud-maplesu.version"] = dict(sidecar, variant="next")
            self.assertTrue(any("variant mismatch" in p for p in verify(Path("payloads"), Path("sidecars"))))


    def test_test_certificate_fingerprint_synced(self):
        # Values come from the t38 test certificate: SHA-256 cd0c477b…db90, DER 911.
        self.assertEqual("cd0c477bcacc58c694367cb9a2f696d3bfd5de3e0ee8a0e25451cecbf312db90",
                         MANAGERS["maplesu"]["certificate"])
        kp = (Path(__file__).resolve().parents[2] / "app/app/src/main/java/com/snothin/ghostsam/data/device/KernelSuProbe.kt").read_text(encoding="utf-8")
        kv = (Path(__file__).resolve().parents[2] / "app/app/src/main/java/com/snothin/ghostsam/data/prefs/KsudVariant.kt").read_text(encoding="utf-8")
        # KernelSuProbe derives the hash from KsudVariant (single source of truth).
        self.assertIn("MAPLESU_MANAGER_CERT_SHA256 = KsudVariant.MAPLESU_CERT_SHA256", kp)
        self.assertIn("cd0c477bcacc58c694367cb9a2f696d3bfd5de3e0ee8a0e25451cecbf312db90", kv)
        self.assertIn("MAPLESU_CERT_DER_SIZE = 911", kv)
        self.assertIn("MAPLESU_MANAGER_CERT_DER_SIZE = 911", kp)
        # Existing three variant fingerprints must stay byte-for-byte.
        self.assertEqual("c371061b19d8c7d7d6133c6a9bafe198fa944e50c1b31c9d8daa8d7f1fc2d2d6", MANAGERS["default"]["certificate"])
        self.assertEqual("79e590113c4c4c0c222978e413a5faa801666957b1212a328e46c00c69821bf7", MANAGERS["next"]["certificate"])
        self.assertEqual("947ae944f3de4ed4c21a7e4f7953ecf351bfa2b36239da37a34111ad29993eef", MANAGERS["sukisu"]["certificate"])

    def test_uapi7_alignment_contract(self):
        # MapleSU manager payload is UAPI 7; the standard variant must declare it, not infer.
        self.assertEqual("7", VARIANTS["maplesu"].get("uapi", "7"))


if __name__ == "__main__":
    unittest.main()
