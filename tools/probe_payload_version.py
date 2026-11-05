#!/usr/bin/env python3
"""Recover the version metadata that ksud stamped into itself at build time.

Every ksud variant derives its version from git *inside* build.rs:

    SukiSU-Ultra     VERSION_CODE = 40000 - 2815 + `git rev-list --count HEAD`
    KernelSU-Next    VERSION_CODE = 30000 + `git rev-list --count HEAD`
    BakaSU           VERSION_CODE = 30000 + 700 + `git rev-list --count HEAD`
    KernelSU         VERSION_CODE = 20000 + `git rev-list --count HEAD`

    VERSION_NAME = `git describe --tags --always`  (leading 'v' stripped)

Both end up as `env!("VERSION_CODE")` / `env!("VERSION_NAME")` string literals,
and the compiler lays the two literals out back to back directly after the
`KSU_NO_NEW_PRIVS` literal in `.rodata`:

    KSU_NO_NEW_PRIVS<5 digits><version name>\0 ... /proc/thread-self ...

Reading that run recovers the exact version a device would report from
`ksud -v` / `ksud -V` without having to execute an aarch64 Android binary.

Usage:
    python3 tools/probe_payload_version.py <ksud-binary> [--json] [--strict]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ELF_MAGIC = b"\x7fELF"

#: Per-project `VERSION_CODE` offset, copied from each project's build.rs.
VERSION_CODE_OFFSETS = {
    "sukisu": 40000 - 2815,
    "next": 30000,
    "bakasu": 30000 + 700,
    "default": 20000,
}

#: <anchor><5 digit code><version name>
_STAMP = re.compile(rb"KSU_NO_NEW_PRIVS(\d{5})([0-9A-Za-z._:+~-]*)")

#: `git describe` output shapes, split into (tag, distance, commit).
_DESCRIBE = re.compile(r"^(?P<tag>.+?)(?:-(?P<distance>\d+)-g(?P<commit>[0-9a-f]{7,40}))?$")

#: Fork release tags that pollute `git describe --tags`.
POLLUTING_TAGS = ("latest-ksud", "latest-bakasu", "manager-ci", "latest")


class ProbeError(RuntimeError):
    pass


def parse_describe(name: str) -> dict:
    """Split a `git describe --tags --always` string into its parts."""
    name = name.strip().lstrip("v")
    m = _DESCRIBE.match(name)
    if not m:
        return {"tag": name, "distance": None, "commit": None}
    distance = m.group("distance")
    return {
        "tag": m.group("tag"),
        "distance": int(distance) if distance is not None else 0,
        "commit": m.group("commit"),
    }


def is_polluted(name: str) -> bool:
    """True when `name` came from a fork release tag instead of a version tag."""
    base = name.strip()
    for tag in POLLUTING_TAGS:
        if base == tag or base.startswith(tag + "-"):
            return True
    # A real version tag always starts with a number, e.g. 4.3.0 / 3.4.2.
    return not re.match(r"^\d+\.\d+", base)


def probe(path: Path, variant: str | None = None) -> dict:
    """Return the version stamp recovered from a ksud ELF."""
    blob = path.read_bytes()
    info: dict = {
        "path": str(path),
        "size": len(blob),
        "variant": variant,
        "version_code": None,
        "version_name": None,
        "tag": None,
        "distance": None,
        "commit": None,
        "commit_count": None,
        "polluted": None,
        "checksum": None,
        "problems": [],
    }

    if not blob.startswith(ELF_MAGIC):
        info["problems"].append("not an ELF file")
        return info

    matches = _STAMP.findall(blob)
    if not matches:
        info["problems"].append(
            "no KSU_NO_NEW_PRIVS<code><name> stamp found — not a ksud payload, "
            "or the binary was built with build.rs version stamping disabled"
        )
        return info
    if len({m[0] for m in matches}) > 1:
        info["problems"].append(
            "conflicting version stamps in one binary: "
            + ", ".join(sorted({m[0].decode() for m in matches}))
        )

    raw_code, raw_name = matches[0]
    code = int(raw_code)
    # The name literal is only usually pooled right after the code; an
    # absent name means "unknown", not a defect - the producer sidecar
    # carries the authoritative value.
    name = raw_name.decode("ascii") or None

    info["version_code"] = code
    info["version_name"] = name
    info["polluted"] = is_polluted(name) if name else None
    parsed = (
        parse_describe(name)
        if name
        else {"tag": None, "distance": None, "commit": None}
    )
    info["tag"] = parsed["tag"]
    info["distance"] = parsed["distance"]
    info["commit"] = parsed["commit"]

    offset = VERSION_CODE_OFFSETS.get(variant or "", None)
    if offset is not None:
        count = code - offset
        info["commit_count"] = count
        if count <= 0:
            info["problems"].append(
                f"version_code {code} is not explainable by the {variant} offset "
                f"{offset} — wrong variant mapping?"
            )

    # A polluted version_name is not fatal here: the producer sidecar can supply
    # the real one. gen_payload_versions.py decides whether it is an error.
    return info


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("binary", type=Path)
    ap.add_argument(
        "--variant",
        choices=sorted(VERSION_CODE_OFFSETS),
        help="ksud variant, used to sanity-check the stamped version code",
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="exit 1 on any problem")
    args = ap.parse_args(argv)

    if not args.binary.is_file():
        print(f"error: {args.binary} not found", file=sys.stderr)
        return 2

    info = probe(args.binary, args.variant)
    if args.json:
        print(json.dumps(info, indent=2, sort_keys=True))
    else:
        for key in (
            "path",
            "size",
            "variant",
            "version_code",
            "version_name",
            "polluted",
            "tag",
            "distance",
            "commit",
            "commit_count",
        ):
            print(f"{key:13}: {info[key]}")
        for problem in info["problems"]:
            print(f"{'problem':13}: {problem}")

    if args.strict and (info["version_code"] is None or info["polluted"] is True):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
