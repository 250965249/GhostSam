from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import emit_samsung_sidecar as emitter

CODE = 31007
NAME = "1.4.0-12-gb3e5057c"
BLOB = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8 + b"KSU_NO_NEW_PRIVS" + str(CODE).encode() + NAME.encode() + b"\x00"
ARTIFACT_DIGEST = "sha256:" + "a" * 64
COMMIT = "b3e5057c99b3fba5901cbed6a4140a7d59462d6f"


class SidecarEmitterTests(unittest.TestCase):
    """The emitter is the consumer-side stand-in for a producer `.version`, so it
    must reproduce the producer's field set exactly and must never invent a value."""

    def setUp(self):
        self.written: dict[str, str] = {}
        self.blob = BLOB
        payload = Path("ksud-maplesu")
        self.payload = payload

        def fake_is_file(p):
            return p.name in {payload.name, "ksud-maplesu.version"}

        def fake_read_bytes(p):
            if p.name != payload.name:
                raise FileNotFoundError(p)
            return self.blob

        def fake_write_text(p, text, **kwargs):
            self.written[p.name] = text
            return len(text)

        def fake_read_text(p, **kwargs):
            return self.written[p.name]

        def fake_mkdir(p, **kwargs):
            return None

        for mock in (
            patch.object(Path, "is_file", fake_is_file),
            patch.object(Path, "read_bytes", fake_read_bytes),
            patch.object(Path, "write_text", fake_write_text),
            patch.object(Path, "read_text", fake_read_text),
            patch.object(Path, "mkdir", fake_mkdir),
            patch("emit_samsung_sidecar.probe", lambda path, variant=None: {
                "version_code": CODE, "version_name": NAME, "problems": [],
            }),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def argv(self, **over):
        args = [
            "--payload", str(self.payload), "--variant", "maplesu",
            "--out", "tools/sidecars/ksud-maplesu.version",
            "--commit", COMMIT, "--built-at", "2026-11-25T01:21:02Z",
            "--kernel-uapi", "6", "--run-id", "45523572248",
            "--artifact-id", "14980758843", "--artifact-digest", ARTIFACT_DIGEST,
            "--behind-upstream", "1",
            "--require", "variant,commit,sha256,version_code,version_name,build_kind,"
                         "producer_run_id,producer_artifact_id,producer_artifact_digest,kernel_uapi_version",
        ]
        for key, value in over.items():
            args += [f"--{key.replace('_', '-')}", str(value)]
        return args

    def test_emits_the_producer_field_set_in_order(self):
        self.assertEqual(0, emitter.main(self.argv()))
        body = self.written["ksud-maplesu.version"]
        keys = [line.split("=", 1)[0] for line in body.strip().splitlines()]
        self.assertEqual(emitter.ORDER, keys)
        fields = dict(line.split("=", 1) for line in body.strip().splitlines())
        self.assertEqual("maplesu", fields["variant"])
        self.assertEqual(str(CODE), fields["version_code"])
        self.assertEqual(NAME, fields["version_name"])
        self.assertEqual(hashlib.sha256(BLOB).hexdigest(), fields["sha256"])
        self.assertEqual(COMMIT, fields["commit"])
        self.assertEqual(COMMIT[:8], fields["commit_short"])
        self.assertEqual("samsung-ci", fields["build_kind"])
        self.assertEqual("6", fields["kernel_uapi_version"])
        self.assertEqual("45523572248", fields["producer_run_id"])
        self.assertEqual("14980758843", fields["producer_artifact_id"])
        self.assertEqual(ARTIFACT_DIGEST, fields["producer_artifact_digest"])
        self.assertEqual("1", fields["behind_upstream"])
        # values this script cannot measure stay empty instead of being guessed
        self.assertEqual("", fields["official_ci_commit"])
        self.assertEqual("", fields["kernel_version_code"])

    def test_refuses_to_write_when_a_required_field_is_empty(self):
        self.assertEqual(1, emitter.main(self.argv(artifact_digest="")))
        self.assertEqual({}, self.written)

    def test_check_mode_accepts_the_emitted_receipt_and_rejects_other_bytes(self):
        self.assertEqual(0, emitter.main(self.argv()))
        self.assertEqual(0, emitter.main([
            "--check", "--payload", str(self.payload), "--variant", "maplesu",
            "--out", "tools/sidecars/ksud-maplesu.version",
            "--require", "variant,sha256,version_code",
        ]))
        self.blob = BLOB + b"different build"
        self.assertEqual(1, emitter.main([
            "--check", "--payload", str(self.payload), "--variant", "maplesu",
            "--out", "tools/sidecars/ksud-maplesu.version",
            "--require", "variant,sha256,version_code",
        ]))


if __name__ == "__main__":
    unittest.main()
