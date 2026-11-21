from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify_samsung_payloads import verify


class SamsungPayloadTests(unittest.TestCase):
    def setUp(self):
        # In-memory producer fixtures keep these policy tests independent of the
        # host's temporary-directory permissions. The CLI also checks real files.
        self.payloads = Path("payloads")
        self.sidecars = Path("sidecars")
        self.metadata = {}
        self.blobs = {}
        for variant, code in (("sukisu", 35046), ("bakasu", 35291)):
            name = "3.0.0-1-g1234567"
            blob = b"\x7fELF\x00KSU_NO_NEW_PRIVS" + str(code).encode() + name.encode() + b"\x00"
            self.blobs[f"ksud-{variant}"] = blob
            self.metadata[f"ksud-{variant}.version"] = {
                "variant": variant,
                "commit": "1" * 40,
                "version_code": str(code),
                "version_name": name,
                "sha256": hashlib.sha256(blob).hexdigest(),
            }
        for mock in (
            patch.object(Path, "is_file", lambda path: path.name in self.blobs),
            patch.object(Path, "read_bytes", lambda path: self.blobs[path.name]),
            patch("verify_samsung_payloads.read_sidecar", lambda path: self.metadata.get(path.name, {})),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def test_accepts_matching_samsung_producer_pair(self):
        self.assertEqual([], verify(self.payloads, self.sidecars))

    def test_official_release_cannot_replace_samsung_payload(self):
        self.metadata["ksud-sukisu.version"]["source"] = "official-release"
        self.assertTrue(any("not a Samsung build" in p for p in verify(self.payloads, self.sidecars)))

    def test_missing_sidecar_cannot_fall_back_to_version_label(self):
        del self.metadata["ksud-bakasu.version"]
        self.assertTrue(any("sidecar missing" in p for p in verify(self.payloads, self.sidecars)))

    def test_swapped_variant_is_rejected(self):
        self.metadata["ksud-bakasu.version"]["variant"] = "sukisu"
        self.assertTrue(any("variant mismatch" in p for p in verify(self.payloads, self.sidecars)))

    def test_reuploaded_binary_with_old_sidecar_is_rejected(self):
        self.blobs["ksud-sukisu"] += b"different build"
        self.assertTrue(any("SHA-256" in p for p in verify(self.payloads, self.sidecars)))

    def test_wrong_version_is_rejected_even_when_digest_matches(self):
        self.metadata["ksud-sukisu.version"]["version_code"] = "30000"
        self.assertTrue(any("version_code" in p for p in verify(self.payloads, self.sidecars)))

    def test_revision_must_be_a_full_commit(self):
        self.metadata["ksud-sukisu.version"]["commit"] = "1234567"
        self.assertTrue(any("producer commit missing" in p for p in verify(self.payloads, self.sidecars)))


if __name__ == "__main__":
    unittest.main()
