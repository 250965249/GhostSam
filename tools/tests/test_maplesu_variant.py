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
from fetch_managers import SPEC as FETCH_SPEC
from verify_manager_pairing import OFFICIAL as PAIR_OFFICIAL, PairingError, verify_pair


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

    # ---- t42 (t40-F4/F5): fetch source must point at the writable fork ----
    def test_fetch_source_points_to_fork(self):
        spec = FETCH_SPEC["maplesu"]
        self.assertEqual("250965249/MapleSU", spec["repo"])
        self.assertEqual("ghostsam-build.yml", spec["workflow"])
        self.assertEqual("MapleSU-Manager-ghostsam-test", spec["artifact"])
        self.assertEqual("GhostSam-manager-MapleSU.apk", spec["out"])
        # Artifact upload name has no .apk; prefer must still select the inner .apk.
        self.assertTrue(any(p.search("MapleSU-Manager-ghostsam-test.apk") for p in spec["prefer"]))
        # The other three variants' fetch sources are untouched.
        self.assertEqual("tiann/KernelSU", FETCH_SPEC["default"]["repo"])
        self.assertEqual("KernelSU-Next/KernelSU-Next", FETCH_SPEC["next"]["repo"])
        self.assertEqual("SukiSU-Nova/SukiSU-Nova", FETCH_SPEC["sukisu"]["repo"])

    def test_auto_build_maplesu_fetch_uses_fork(self):
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/auto-build.yml").read_text(encoding="utf-8")
        self.assertIn("fetch_gh 250965249/MapleSU latest-ksud", workflow)
        self.assertIn('ksud-maplesu-aarch64 "$base/ksud-maplesu"', workflow)
        self.assertIn("sidecar maplesu 250965249/MapleSU", workflow)
        self.assertNotIn("fetch_gh FengQiuFQ/MapleSU", workflow)
        self.assertNotIn("sidecar maplesu FengQiuFQ/MapleSU", workflow)

    # ---- t42: pairing accepts the fork Actions manager, other variants unchanged ----
    def test_pairing_accepts_fork_manager_source(self):
        self.assertEqual("250965249/MapleSU", PAIR_OFFICIAL["maplesu"])
        self.assertEqual("tiann/KernelSU", PAIR_OFFICIAL["default"])
        self.assertEqual("KernelSU-Next/KernelSU-Next", PAIR_OFFICIAL["next"])
        self.assertEqual("SukiSU-Nova/SukiSU-Nova", PAIR_OFFICIAL["sukisu"])
        mcommit, kcommit = "a" * 40, "b" * 40
        m = {
            "repo": "250965249/MapleSU", "signature_verified": True,
            "certificate_sha256": MANAGERS["maplesu"]["certificate"],
            "package": MANAGERS["maplesu"]["package"], "file": MANAGERS["maplesu"]["file"],
            "ci": {"repository": "250965249/MapleSU",
                   "workflow": ".github/workflows/ghostsam-build.yml", "commit": mcommit},
            "kind": "ci", "uapi_version": 7, "version_code": 32650,
        }
        k = {"source": "250965249/MapleSU", "commit": kcommit, "version_code": 32650,
             "kernel_version_code": "32650", "merge_auto_resolved": ""}

        def api(path):
            left = path.split("/compare/", 1)[1].split("...", 1)[0]
            return {"behind_by": 0, "merge_base_commit": {"sha": left}}

        result = verify_pair("maplesu", m, k, api, lambda repo, commit: 7)
        self.assertTrue(result["contract_verified"])
        # A manager from the unwritable upstream is rejected by the source whitelist.
        with self.assertRaisesRegex(PairingError, "manager source"):
            verify_pair("maplesu", {**m, "repo": "FengQiuFQ/MapleSU"}, k, api, lambda r, c: 7)
        # A manager whose provenance is not the fork GhostSam build is rejected.
        bad = {**m, "ci": {**m["ci"], "workflow": ".github/workflows/build-manager.yml"}}
        with self.assertRaisesRegex(PairingError, "fork GhostSam CI artifact"):
            verify_pair("maplesu", bad, k, api, lambda r, c: 7)
        # No relaxation for the pre-existing variants: default still rejects a fork repo.
        dm = {"repo": "250965249/MapleSU", "signature_verified": True,
              "certificate_sha256": MANAGERS["default"]["certificate"],
              "package": MANAGERS["default"]["package"], "file": MANAGERS["default"]["file"],
              "ci": {"commit": mcommit}, "kind": "ci", "uapi_version": 6, "version_code": 32735}
        dk = {"source": "250965249/KernelSU", "commit": kcommit, "version_code": 32740,
              "kernel_version_code": "32735", "kernel_uapi_version": "6", "build_kind": "samsung-ci",
              "producer_run_id": "42", "official_ci_commit": mcommit, "merge_auto_resolved": "",
              "producer_artifact_id": "99", "producer_artifact_digest": "sha256:" + "d" * 64,
              "sha256": "c" * 64}
        with self.assertRaisesRegex(PairingError, "manager source"):
            verify_pair("default", dm, dk, api, lambda r, c: 6)


if __name__ == "__main__":
    unittest.main()
