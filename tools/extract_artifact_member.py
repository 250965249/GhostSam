#!/usr/bin/env python3
"""Extract one member of a GitHub Actions artifact zip by name.

Artifacts wrap their payload in a zip whose member paths vary (sometimes
`libksud_ksu.so`, sometimes `manager/src/main/jniLibs/arm64-v8a/libksud_ksu.so`).
This helper resolves the member by exact path or longest suffix match, refuses
ambiguous matches, and writes the bytes out unchanged.

Usage:
  python3 tools/extract_artifact_member.py <artifact.zip> <member-name> <out-file>
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path


def find_member(names: list[str], wanted: str) -> str:
    exact = [n for n in names if n == wanted]
    if len(exact) == 1:
        return exact[0]
    suffix = [n for n in names if n.endswith("/" + wanted)]
    if len(suffix) == 1:
        return suffix[0]
    raise SystemExit(
        f"expected exactly one {wanted!r} in the artifact, found {len(exact) + len(suffix)} "
        f"of {len(names)} entries: {(exact + suffix)[:5]}"
    )


def main(argv: list[str]) -> int:
    archive, wanted, out = Path(argv[0]), argv[1], Path(argv[2])
    with zipfile.ZipFile(archive) as zf:
        member = find_member(zf.namelist(), wanted)
        with zf.open(member) as src:
            data = src.read()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"extracted {member} -> {out} ({len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
