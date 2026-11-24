#!/usr/bin/env python3
"""Check Samsung producer metadata before packaging embedded KSU modules.

A newer official ksud is not interchangeable with the Samsung build: ksud
contains the LKM that late-load installs. Require the actual producer sidecar
and bind its variant, revision and version to the downloaded bytes. This check
is static provenance validation; device compatibility still needs a device run.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

from gen_payload_versions import PROVISIONAL, VARIANTS, read_sidecar
from probe_payload_version import probe

PRODUCERS = {
    "default": "250965249/KernelSU",
    "next": "250965249/KernelSU-Next",
    "sukisu": "250965249/SukiSU-Ultra",
    # Fourth slot, registered ahead of its producer. 250965249/MapleSU (the
    # Samsung-side fork of FengQiuFQ/MapleSU) publishes no `latest-ksud`
    # release, no `ksud-maplesu-aarch64` asset and no `.version` sidecar yet, so
    # the whole contract below is unenforceable today. Keeping the key in
    # PRODUCERS makes the slot addressable in one table, while PROVISIONAL keeps
    # a real build of the three live variants alive instead of failing it on an
    # artefact that cannot exist. The moment a MapleSU payload lands the full
    # contract (sidecar variant/commit/sha256/version_code/version_name plus the
    # .rodata stamp) is enforced with no further edit here.
    "maplesu": "250965249/MapleSU",
}

# `PROVISIONAL` is imported from gen_payload_versions: that module owns the
# variant tables, so the "registered but the producer is offline" set has exactly
# one definition. A variant listed there is skipped only while *both* its payload
# and its sidecar are absent; a half-published pair is a defect and is reported.


def verify(payloads: Path, sidecars: Path) -> list[str]:
    problems: list[str] = []
    for variant, producer in PRODUCERS.items():
        # The payload file name is not always ``ksud-<variant>`` (default ships as
        # plain ``ksud``); the sidecar always lands as ``ksud-<variant>.version``.
        payload_file = VARIANTS[variant]["file"]
        binary = payloads / payload_file
        sidecar_path = sidecars / f"ksud-{variant}.version"
        prefix = f"{variant} ({producer})"
        if variant in PROVISIONAL and not binary.is_file() and not sidecar_path.is_file():
            print(
                f"NOTICE {prefix}: provisional variant - the producer has published "
                f"neither {payload_file} nor {sidecar_path.name} yet; skipped",
                file=sys.stderr,
            )
            continue
        sidecar = read_sidecar(sidecar_path)
        if not binary.is_file():
            problems.append(f"{prefix}: payload missing")
            continue
        if not sidecar:
            problems.append(f"{prefix}: Samsung producer sidecar missing")
            continue
        if sidecar.get("source") == "official-release":
            problems.append(f"{prefix}: official-release metadata is not a Samsung build")
        if sidecar.get("variant") != variant:
            problems.append(f"{prefix}: sidecar variant mismatch")
        if not re.fullmatch(r"[0-9a-f]{40}", sidecar.get("commit", "")):
            problems.append(f"{prefix}: full producer commit missing")
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        if sidecar.get("sha256") != digest:
            problems.append(f"{prefix}: producer SHA-256 does not match payload")
        info = probe(binary, variant)
        problems.extend(f"{prefix}: {problem}" for problem in info["problems"])
        if str(info["version_code"]) != sidecar.get("version_code"):
            problems.append(f"{prefix}: producer version_code does not match payload")
        if not sidecar.get("version_name"):
            problems.append(f"{prefix}: producer version_name missing")
        elif info["version_name"] and info["version_name"] != sidecar["version_name"]:
            problems.append(f"{prefix}: producer version_name does not match payload")
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payloads", type=Path, required=True)
    parser.add_argument("--sidecars", type=Path, required=True)
    args = parser.parse_args(argv)
    problems = verify(args.payloads, args.sidecars)
    for problem in problems:
        print(f"ERROR {problem}", file=sys.stderr)
    if problems:
        return 1
    print("Samsung ksud payloads match their producer revisions and checksums")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
