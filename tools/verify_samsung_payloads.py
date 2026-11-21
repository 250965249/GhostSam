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

from gen_payload_versions import read_sidecar
from probe_payload_version import probe

PRODUCERS = {
    "sukisu": "250965249/SukiSU-Ultra",
}


def verify(payloads: Path, sidecars: Path) -> list[str]:
    problems: list[str] = []
    for variant, producer in PRODUCERS.items():
        filename = f"ksud-{variant}"
        binary = payloads / filename
        sidecar = read_sidecar(sidecars / f"{filename}.version")
        prefix = f"{variant} ({producer})"
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
