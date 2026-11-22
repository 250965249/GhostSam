#!/usr/bin/env python3
"""Verify staged Android managers before attaching them to a release.

Use Android's apksigner for cryptographic verification, then aapt2 for package
and version identity. Merely extracting a declared certificate fingerprint is
not signature verification. Original producer signatures must be preserved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

MANAGERS = {
    "default": {
        "file": "GhostSam-manager-KernelSU.apk",
        "package": "me.weishu.kernelsu",
        "certificate": "c371061b19d8c7d7d6133c6a9bafe198fa944e50c1b31c9d8daa8d7f1fc2d2d6",
    },
    "next": {
        "file": "GhostSam-manager-KernelSU-Next.apk",
        "package": "com.rifsxd.ksunext",
        "certificate": "79e590113c4c4c0c222978e413a5faa801666957b1212a328e46c00c69821bf7",
    },
    "sukisu": {
        "file": "GhostSam-manager-SukiSU.apk",
        "package": "com.sukisu.nova",
        "certificate": "947ae944f3de4ed4c21a7e4f7953ecf351bfa2b36239da37a34111ad29993eef",
    },
}


class ManagerVerificationError(RuntimeError):
    pass


def parse_signature(text: str, expected: str) -> dict:
    certificates = re.findall(r"Signer #\d+ certificate SHA-256 digest:\s*([0-9a-fA-F]{64})", text)
    if len(certificates) != 1 or certificates[0].lower() != expected.lower():
        raise ManagerVerificationError("manager signing certificate differs from the pinned producer")
    if not re.search(r"Verified using v2 scheme[^\r\n]*:\s*true", text):
        raise ManagerVerificationError("manager does not have a verified APK v2 signature")
    return {"certificate_sha256": certificates[0].lower(), "signature_verified": True}


def parse_badging(text: str, expected_package: str) -> dict:
    line = next((line for line in text.splitlines() if line.startswith("package:")), "")
    fields = dict(re.findall(r"([A-Za-z][A-Za-z0-9]*)='([^']*)'", line))
    if fields.get("name") != expected_package:
        raise ManagerVerificationError(f"expected package {expected_package}, got {fields.get('name')!r}")
    code = fields.get("versionCode", "")
    if not code.isdigit() or int(code) <= 0:
        raise ManagerVerificationError("APK versionCode is missing or invalid")
    return {"package": fields["name"], "version_code": int(code), "version_name": fields.get("versionName", "")}


def command_output(args: list[str]) -> str:
    try:
        result = subprocess.run(args, text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ManagerVerificationError(f"Android tool failed: {args[0]}: {exc}") from exc
    if result.returncode:
        raise ManagerVerificationError(f"{args[0]} verification failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def verify_apk(path: Path, spec: dict, apksigner: str, aapt2: str) -> dict:
    if not path.is_file():
        raise ManagerVerificationError(f"manager missing: {path.name}")
    if path.stat().st_size <= 0 or path.stat().st_size > 512 * 1024 * 1024:
        raise ManagerVerificationError("manager APK size is invalid")
    signature = parse_signature(command_output([apksigner, "verify", "--verbose", "--print-certs", str(path)]), spec["certificate"])
    identity = parse_badging(command_output([aapt2, "dump", "badging", str(path)]), spec["package"])
    try:
        with zipfile.ZipFile(path) as archive:
            arm64 = [name for name in archive.namelist() if name.startswith("lib/arm64-v8a/") and name.endswith(".so")]
            if not arm64:
                raise ManagerVerificationError("manager APK has no arm64-v8a native library")
    except zipfile.BadZipFile as exc:
        raise ManagerVerificationError("manager download is not an APK ZIP") from exc
    return {
        **identity, **signature, "abi": "arm64-v8a", "file": path.name,
        "size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "runtime_compatibility": "not-tested",
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--managers", type=Path, required=True)
    parser.add_argument("--apksigner", required=True)
    parser.add_argument("--aapt2", required=True)
    parser.add_argument("--only", choices=sorted(MANAGERS), action="append")
    args = parser.parse_args(argv)
    report_path = args.managers / "managers.json"
    records = json.loads(report_path.read_text(encoding="utf-8"))
    selected = args.only or list(MANAGERS)
    failures = []
    for variant in selected:
        spec = MANAGERS[variant]
        try:
            matches = [r for r in records if r.get("variant") == variant]
            if len(matches) != 1 or matches[0].get("file") != spec["file"]:
                raise ManagerVerificationError("manager provenance record missing or duplicated")
            identity = verify_apk(args.managers / spec["file"], spec, args.apksigner, args.aapt2)
            if matches[0].get("sha256") != identity["sha256"]:
                raise ManagerVerificationError("downloaded manager differs from its provenance record")
            matches[0].update(identity)
            print(f"OK {variant}: {identity['package']} {identity['version_name']} ({identity['version_code']})")
        except (ManagerVerificationError, OSError, ValueError) as exc:
            failures.append(f"{variant}: {exc}")
    if failures:
        for failure in failures:
            print(f"ERROR {failure}", file=sys.stderr)
        return 1
    report_path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
