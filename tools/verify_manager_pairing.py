#!/usr/bin/env python3
"""Reject unverified or mismatched managers before a GhostSam release is public.

This validates the build contract, not a device. No kernel, APK or root daemon
is executed. New KernelSU/Next CI managers require an explicit Samsung producer
receipt; a generic official ksud or an intermediate fork's version label does
not satisfy that contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fetch_managers import api_json, source_uapi
from gen_payload_versions import VARIANTS
from github_ci import full_revision
from verify_manager_apks import MANAGERS

OFFICIAL = {"default": "tiann/KernelSU", "next": "KernelSU-Next/KernelSU-Next",
            "sukisu": "SukiSU-Nova/SukiSU-Nova", "maplesu": "FengQiuFQ/MapleSU"}


class PairingError(RuntimeError):
    pass


def positive_integer(value, label: str) -> int:
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        return int(value)
    raise PairingError(f"{label} is missing or invalid")


def samsung_source(repository: str) -> bool:
    return isinstance(repository, str) and (
        bool(re.fullmatch(r"250965249/[A-Za-z0-9_.-]+", repository))
        or repository == "snothin/KernelSU"
    )


def producer_payload_digest(repository: str, artifact_id: int, expected_digest: str) -> str:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest or ""):
        raise PairingError("producer artifact digest is missing or invalid")
    with tempfile.TemporaryFile(dir=Path.cwd()) as archive_file, tempfile.TemporaryFile(dir=Path.cwd()) as stderr:
        result = subprocess.run(
            ["gh", "api", f"repos/{repository}/actions/artifacts/{artifact_id}/zip"],
            stdout=archive_file, stderr=stderr, timeout=300,
        )
        if result.returncode:
            stderr.seek(0)
            raise PairingError(f"producer artifact download failed: {stderr.read().decode('utf-8', errors='replace')}")
        archive_file.seek(0)
        if expected_digest != f"sha256:{hashlib.file_digest(archive_file, 'sha256').hexdigest()}":
            raise PairingError("producer archive digest differs from GitHub metadata")
        archive_file.seek(0)
        with zipfile.ZipFile(archive_file) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir() and Path(entry.filename).name == 'ksud']
            if len(entries) != 1 or entries[0].file_size > 512 * 1024 * 1024:
                raise PairingError("producer archive has no unique bounded ksud entry")
            with archive.open(entries[0]) as data:
                return hashlib.file_digest(data, 'sha256').hexdigest()


def verify_pair(variant: str, manager: dict, kernel: dict, api=api_json, uapi_reader=source_uapi, artifact_reader=producer_payload_digest) -> dict:
    expected = MANAGERS[variant]
    if manager.get("signature_verified") is not True:
        raise PairingError(f"{variant}: APK signature was not verified")
    if manager.get("certificate_sha256") != expected["certificate"] or manager.get("package") != expected["package"]:
        raise PairingError(f"{variant}: manager identity differs from the trusted producer")
    if manager.get("repo") != OFFICIAL[variant]:
        raise PairingError(f"{variant}: manager source is not the configured official repository")
    repository = kernel.get("source")
    if not samsung_source(repository):
        raise PairingError(f"{variant}: generic or unknown kernel source is not a Samsung producer")
    kernel_commit = full_revision(kernel.get("commit"))
    manager_commit = full_revision((manager.get("ci") or {}).get("commit") or manager.get("source_commit"))
    manager_version = positive_integer(manager.get("version_code"), "manager version code")
    kernel_version = kernel.get("kernel_version_code")
    if kernel_version is not None:
        kernel_version = positive_integer(kernel_version, "kernel version code")
    manager_uapi = positive_integer(manager.get("uapi_version"), "manager UAPI")
    kernel_uapi = positive_integer(uapi_reader(repository, kernel_commit), "kernel source UAPI")
    if manager_uapi != kernel_uapi:
        raise PairingError(f"{variant}: UAPI mismatch (manager={manager_uapi}, kernel={kernel_uapi})")

    # Nova's existing pinned pair remains supported. A new KernelSU/Next CI
    # source must carry a real producer receipt rather than a guessed version.
    if variant != "sukisu":
        if kernel.get("build_kind") != "samsung-ci":
            raise PairingError(f"{variant}: Samsung CI producer receipt is missing")
        declared_uapi = positive_integer(kernel.get("kernel_uapi_version"), "producer kernel UAPI")
        if declared_uapi != kernel_uapi:
            raise PairingError(f"{variant}: producer UAPI does not match its source revision")
        positive_integer(kernel.get("kernel_version_code"), "kernel version code")
        run_id = positive_integer(kernel.get("producer_run_id"), "producer run id")
        if full_revision(kernel.get("official_ci_commit")) != manager_commit:
            raise PairingError(f"{variant}: kernel and manager did not use the same official CI snapshot")
        run = api(f"repos/{repository}/actions/runs/{run_id}")
        if (run.get("status") != "completed" or run.get("conclusion") != "success"
            or run.get("event") not in ("push", "workflow_dispatch", "schedule")
            or (run.get("head_repository") or {}).get("full_name") != repository
            or run.get("head_branch") != "samsung"
            or run.get("path", "").split("@")[0] != ".github/workflows/auto-sync-build.yml"):
            raise PairingError(f"{variant}: producer run is not the configured immutable Samsung build")
        # The daily sync run merges the official branch and pushes the merge in the
        # same run: its head_sha is the pre-merge branch tip frozen when the run
        # started, while the kernel commit is the post-merge result, so equality
        # never holds on that normal path. Require the run tip to be an ancestor of
        # (or equal to) the kernel commit instead: the run was built from the direct
        # history of this tree. Byte-level binding stays the artifact digest ==
        # payload sha256 below; a compare 404 (unknown commit) fails via api().
        run_head = full_revision(run.get("head_sha"))
        ancestry = api(f"repos/{repository}/compare/{run_head}...{kernel_commit}")
        if (ancestry.get("behind_by") != 0
                or (ancestry.get("merge_base_commit") or {}).get("sha") != run_head):
            raise PairingError(f"{variant}: producer run is not the configured immutable Samsung build")
        artifact_id = positive_integer(kernel.get("producer_artifact_id"), "producer artifact id")
        digest = kernel.get("producer_artifact_digest")
        artifact = api(f"repos/{repository}/actions/artifacts/{artifact_id}")
        identity = artifact.get("workflow_run") or {}
        # The artifact belongs to this run, so its workflow_run.head_sha must equal
        # the run's own head_sha (the pre-merge tip), not the merged kernel commit.
        if (artifact.get("id") != artifact_id or artifact.get("name") != "ksud-aarch64-linux-android" or artifact.get("expired") is not False
            or identity.get("id") != run_id or identity.get("head_sha") != run_head
            or not isinstance(digest, str) or artifact.get("digest") != digest):
            raise PairingError(f"{variant}: producer artifact identity differs from the receipt")
        if artifact_reader(repository, artifact_id, digest) != kernel.get("sha256"):
            raise PairingError(f"{variant}: APK payload is not the ksud from the verified producer artifact")
    elif (repository != "250965249/SukiSU-Ultra" or manager.get("kind") != "release"
          or manager.get("tag") != "v3.0.0"):
        raise PairingError("sukisu: the existing pinned pair was changed without review")

    conflicts = kernel.get("merge_auto_resolved") or ""
    if not isinstance(conflicts, str) or any(p.startswith("kernel/") for p in conflicts.split()):
        raise PairingError(f"{variant}: kernel merge conflicts need review; ancestry alone is not enough")
    comparison = api(f"repos/{repository}/compare/{manager_commit}...{kernel_commit}")
    if comparison.get("behind_by") != 0 or (comparison.get("merge_base_commit") or {}).get("sha") != manager_commit:
        raise PairingError(f"{variant}: kernel source does not contain the manager's official baseline")
    return {
        "variant": variant, "manager_package": expected["package"],
        "manager_commit": manager_commit, "kernel_commit": kernel_commit,
        "manager_uapi": manager_uapi, "kernel_source_uapi": kernel_uapi,
        "manager_version_code": manager_version,
        "ksud_version_code": kernel.get("version_code"),
        "kernel_version_code": kernel_version,
        "contract_verified": True, "runtime_compatibility": "not-tested",
    }


def verify_files(apk: Path, managers: Path, variant: str, manager: dict, kernel: dict) -> None:
    filename = MANAGERS[variant]["file"]
    if manager.get("file") != filename:
        raise PairingError(f"{variant}: unexpected manager filename")
    path = managers / filename
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != manager.get("sha256"):
        raise PairingError(f"{variant}: manager changed after signature verification")
    asset = f"assets/payloads/ksud/{VARIANTS[variant]['file']}"
    with zipfile.ZipFile(apk) as archive:
        entries = [entry for entry in archive.infolist() if entry.filename == asset]
        if len(entries) != 1:
            raise PairingError(f"{variant}: APK contains a missing or duplicate payload asset")
        data = archive.read(entries[0])
    if hashlib.sha256(data).hexdigest() != kernel.get("sha256"):
        raise PairingError(f"{variant}: APK payload differs from the shared build contract")
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01" or int.from_bytes(data[18:20], "little") != 183:
        raise PairingError(f"{variant}: APK payload is not a little-endian ARM64 ELF")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--managers", type=Path, required=True)
    parser.add_argument("--only", choices=sorted(MANAGERS), action="append")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        lock = json.loads(args.lock.read_text(encoding="utf-8"))
        records = json.loads((args.managers / "managers.json").read_text(encoding="utf-8"))
        results = []
        for variant in args.only or list(MANAGERS):
            matches = [record for record in records if record.get("variant") == variant]
            if len(matches) != 1 or variant not in lock.get("variants", {}):
                raise PairingError(f"{variant}: build contract or manager record missing/duplicated")
            kernel = lock["variants"][variant]
            verify_files(args.apk, args.managers, variant, matches[0], kernel)
            result = verify_pair(variant, matches[0], kernel)
            results.append(result)
            print(f"OK {variant}: manager and kernel source UAPI={result['manager_uapi']}; contract verified, device untested")
        args.out.write_text(json.dumps({
            "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pairs": results, "runtime_compatibility": "not-tested",
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (RuntimeError, ValueError, KeyError, OSError, zipfile.BadZipFile) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
