from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify_samsung_payloads import verify
from gen_payload_versions import VARIANTS, build_report, render_manifest
from render_release_notes import changes_section, sync_cell

VARIANT = "sukisu"
CODE = 35046
NEXT_VARIANT = "next"
NEXT_CODE = 33000
DEFAULT_VARIANT = "default"
DEFAULT_CODE = 32680
# Fourth slot: registered while its producer is still offline, so it is exercised
# both with a simulated payload (full contract) and without one (skipped).
MAPLESU_VARIANT = "maplesu"
MAPLESU_CODE = 31000


class SamsungPayloadTests(unittest.TestCase):
    def setUp(self):
        # In-memory producer fixtures keep these policy tests independent of the
        # host's temporary-directory permissions. The CLI also checks real files.
        self.payloads = Path("payloads")
        self.sidecars = Path("sidecars")
        self.blobs = {}
        self.metadata = {}

        def add_fixture(variant: str, code: int, name: str) -> None:
            blob = b"\x7fELF\x00KSU_NO_NEW_PRIVS" + str(code).encode() + name.encode() + b"\x00"
            # Payload file is VARIANTS[variant]["file"] (default is plain "ksud");
            # the producer sidecar always lands as "ksud-<variant>.version".
            self.blobs[VARIANTS[variant]["file"]] = blob
            self.metadata[f"ksud-{variant}.version"] = {
                "variant": variant,
                "commit": "1" * 40,
                "version_code": str(code),
                "version_name": name,
                "behind_upstream": "0",
                "sha256": hashlib.sha256(blob).hexdigest(),
            }

        # verify() iterates PRODUCERS, which now covers default, next, sukisu and
        # maplesu; a fixture is required for each producer or verify() reports
        # the missing one.
        add_fixture(VARIANT, CODE, "3.0.0-1-g1234567")
        add_fixture(NEXT_VARIANT, NEXT_CODE, "3.4.2-1-g89abcde")
        add_fixture(DEFAULT_VARIANT, DEFAULT_CODE, "3.3.1-1-gaabbccd")
        add_fixture(MAPLESU_VARIANT, MAPLESU_CODE, "1.0.0-1-g0123456")

        for mock in (
            # A path exists when either half of the pair is present, so a
            # published sidecar without its payload (and the reverse) is
            # expressible here instead of being untestable.
            patch.object(
                Path, "is_file",
                lambda path: path.name in self.blobs or path.name in self.metadata,
            ),
            patch.object(Path, "read_bytes", lambda path: self.blobs[path.name]),
            patch("verify_samsung_payloads.read_sidecar", lambda path: self.metadata.get(path.name, {})),
            # build_report()'s own collaborators, patched at their module
            # namespace so the lock-marker tests below stay in-memory too.
            patch("gen_payload_versions.read_sidecar", lambda path: self.metadata.get(path.name, {})),
            patch("gen_payload_versions.probe", self._fake_probe),
            patch("gen_payload_versions.sha256", lambda path: hashlib.sha256(self.blobs[path.name]).hexdigest()),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def _fake_probe(self, path, variant=None):
        meta = self.metadata.get(f"ksud-{variant}.version") or {}
        return {
            "path": str(path),
            "size": 1,
            "variant": variant,
            "version_code": int(meta["version_code"]) if meta.get("version_code") else None,
            "version_name": meta.get("version_name"),
            "tag": None,
            "distance": None,
            "commit": None,
            "commit_count": None,
            "polluted": False,
            "checksum": None,
            "problems": [],
        }

    def _report(self):
        return build_report(self.payloads, self.sidecars, 48.0)

    def test_accepts_matching_samsung_producer(self):
        self.assertEqual([], verify(self.payloads, self.sidecars))

    def test_provisional_variant_is_skipped_while_its_producer_is_offline(self):
        # 250965249/MapleSU publishes no payload and no sidecar yet: the slot is
        # registered, so verify() must not fail the live variants over it.
        del self.blobs[VARIANTS[MAPLESU_VARIANT]["file"]]
        del self.metadata[f"ksud-{MAPLESU_VARIANT}.version"]
        self.assertEqual([], verify(self.payloads, self.sidecars))

    def test_provisional_variant_is_enforced_as_soon_as_a_payload_exists(self):
        # Half a pair is a defect, not a pending slot: a payload without its
        # producer sidecar must still be reported.
        del self.metadata[f"ksud-{MAPLESU_VARIANT}.version"]
        problems = verify(self.payloads, self.sidecars)
        self.assertTrue(
            any(
                p.startswith(f"{MAPLESU_VARIANT} (250965249/MapleSU): Samsung producer sidecar missing")
                for p in problems
            ),
            problems,
        )

    def test_provisional_variant_is_enforced_when_only_its_sidecar_exists(self):
        # The other half of a half-published pair: the producer published the
        # sidecar but not the payload. That is a defect, not a pending slot, so it
        # must fail closed instead of being skipped as "producer offline".
        del self.blobs[VARIANTS[MAPLESU_VARIANT]["file"]]
        problems = verify(self.payloads, self.sidecars)
        self.assertTrue(
            any(
                p.startswith(f"{MAPLESU_VARIANT} (250965249/MapleSU): payload missing")
                for p in problems
            ),
            problems,
        )

    def test_skipped_provisional_variant_is_marked_in_the_lock(self):
        # The lock must carry an explicit marker for a registered-but-unpublished
        # slot, and the manifest must stay byte-identical while it is offline.
        del self.blobs[VARIANTS[MAPLESU_VARIANT]["file"]]
        del self.metadata[f"ksud-{MAPLESU_VARIANT}.version"]
        report = self._report()
        self.assertIs(report["variants"][MAPLESU_VARIANT]["provisional"], True)
        self.assertIn(MAPLESU_VARIANT, report["provisional"])
        self.assertIn("publishes neither", report["provisional"][MAPLESU_VARIANT])
        self.assertNotIn(f"{MAPLESU_VARIANT}=", render_manifest(report))
        self.assertEqual([], report["problems"])

    def test_sync_cell_reads_the_marker_not_the_repository_name(self):
        # Four complete variants: the slot is live, so the notes must report the
        # producer's own sync state instead of a permanent "not online" label.
        report = self._report()
        entry = report["variants"][MAPLESU_VARIANT]
        self.assertIs(entry["provisional"], False)
        self.assertEqual({}, report["provisional"])
        self.assertNotIn("provisional", sync_cell(entry))
        self.assertEqual("生产方报告已同步", sync_cell(entry))
        # ... and the same function reports the skipped slot from the marker.
        offline = dict(entry, provisional=True)
        self.assertIn("本次未交付", sync_cell(offline))

    def test_changes_section_never_reports_a_provisional_stub_as_new(self):
        # A provisional slot has version_code=None by construction. Without the
        # provisional short-circuit, changes_section() announced "MapleSU 新增" in
        # every release after the first, because the previous lock also carried the
        # stub. Nothing changed here, so the "no payload movement" sentence stands.
        del self.blobs[VARIANTS[MAPLESU_VARIANT]["file"]]
        del self.metadata[f"ksud-{MAPLESU_VARIANT}.version"]
        lock = self._report()
        self.assertIs(lock["variants"][MAPLESU_VARIANT]["provisional"], True)

        # (a) previous release built with the same stub in it
        previous = json.loads(json.dumps(lock))
        rows = changes_section(lock, previous)
        self.assertEqual(["本次没有载荷变化（上游未推送新提交）。"], rows)
        self.assertNotIn("新增", "\n".join(rows))

        # (b) previous release published before the slot was registered at all
        pre_merge = json.loads(json.dumps(lock))
        del pre_merge["variants"][MAPLESU_VARIANT]
        rows = changes_section(lock, pre_merge)
        self.assertNotIn("新增", "\n".join(rows))
        self.assertEqual(["本次没有载荷变化（上游未推送新提交）。"], rows)

    def test_changes_section_still_reports_a_real_version_movement(self):
        # The provisional guard must not silence the published variants.
        lock = self._report()
        previous = json.loads(json.dumps(lock))
        previous["variants"][VARIANT]["version_code"] = CODE - 1
        previous["variants"][VARIANT]["version_name"] = "3.0.0-0-g0000000"
        rows = changes_section(lock, previous)
        self.assertTrue(any("SukiSU-Nova" in row and "→" in row for row in rows), rows)
        self.assertNotIn("新增", "\n".join(rows))

    def test_official_release_cannot_replace_samsung_payload(self):
        self.metadata[f"ksud-{VARIANT}.version"]["source"] = "official-release"
        self.assertTrue(any("not a Samsung build" in p for p in verify(self.payloads, self.sidecars)))

    def test_missing_sidecar_cannot_fall_back_to_version_label(self):
        del self.metadata[f"ksud-{VARIANT}.version"]
        self.assertTrue(any("sidecar missing" in p for p in verify(self.payloads, self.sidecars)))

    def test_swapped_variant_is_rejected(self):
        self.metadata[f"ksud-{VARIANT}.version"]["variant"] = "next"
        self.assertTrue(any("variant mismatch" in p for p in verify(self.payloads, self.sidecars)))

    def test_reuploaded_binary_with_old_sidecar_is_rejected(self):
        self.blobs[f"ksud-{VARIANT}"] += b"different build"
        self.assertTrue(any("SHA-256" in p for p in verify(self.payloads, self.sidecars)))

    def test_wrong_version_is_rejected_even_when_digest_matches(self):
        self.metadata[f"ksud-{VARIANT}.version"]["version_code"] = "30000"
        self.assertTrue(any("version_code" in p for p in verify(self.payloads, self.sidecars)))

    def test_revision_must_be_a_full_commit(self):
        self.metadata[f"ksud-{VARIANT}.version"]["commit"] = "1234567"
        self.assertTrue(any("producer commit missing" in p for p in verify(self.payloads, self.sidecars)))


if __name__ == "__main__":
    unittest.main()
