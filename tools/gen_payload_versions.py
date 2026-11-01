#!/usr/bin/env python3
"""Generate `assets/payloads/ksud/versions.properties` from the payloads themselves.

The settings screen shows this file verbatim ("KernelSU 版本 → 当前 Payload：…"),
so it must never be hand-maintained: the previous workflow replaced the four
`ksud*` binaries on every run but left this manifest untouched, which is why the
UI kept reporting the version of whichever build happened to be current when
somebody last edited it by hand.

This script is the single source of truth. For every payload it:

  1. reads the version stamped into the ELF by `userspace/ksud/build.rs`
     (see tools/probe_payload_version.py);
  2. cross-checks that stamp against the optional `*.version` sidecar that the
     ksud producer repository publishes next to the binary;
  3. fails loudly when the two disagree, when a fork release tag polluted
     `git describe`, or when the payload is older than `--max-age-hours`.

Usage:
    python3 tools/gen_payload_versions.py --payloads <dir> [--sidecars <dir>]
                                          [--write] [--check]

    --write   rewrite versions.properties + payloads.lock.json
    --check   compare the on-disk versions.properties with what would be
              generated; exit 1 on drift (this is what CI runs before building)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_payload_version import (  # noqa: E402
    VERSION_CODE_OFFSETS,
    is_polluted,
    parse_describe,
    probe,
)

#: canonical order and the names shown in the settings dropdown.
VARIANTS: dict[str, dict[str, str]] = {
    "default": {
        "file": "ksud",
        "display": "KernelSU",
        "source": "tiann/KernelSU",
        "release_tag": None,
    },
    "next": {
        "file": "ksud-next",
        "display": "KernelSU-Next",
        "source": "250965249/KernelSU-Next-Samsung",
        "release_tag": "latest-ksud",
    },
    "sukisu": {
        "file": "ksud-sukisu",
        "display": "SukiSU-Ultra",
        "source": "250965249/SukiSU-Ultra",
        "release_tag": "latest-ksud",
    },
    "bakasu": {
        "file": "ksud-bakasu",
        "display": "BakaSU",
        "source": "250965249/BakaSU",
        "release_tag": "latest-ksud",
    },
}

MANIFEST_NAME = "versions.properties"
LOCK_NAME = "payloads.lock.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_sidecar(path: Path) -> dict[str, str]:
    """Parse a `key=value` producer sidecar; missing file is not an error."""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def format_label(display: str, name: str | None, code: int | None, sidecar: dict) -> str:
    """`SukiSU-Ultra v4.3.0 (41128)` / `SukiSU-Ultra v4.3.0+44 (41128)`.

    A variant whose `userspace/ksud/build.rs` pins the stamp (KernelSU-Next)
    gets a `pinned` marker, because otherwise the number would silently look
    like a real, current version number forever.
    """
    if code is None:
        return f"{display} unknown"

    pinned = sidecar.get("version_pinned", "").lower() == "true"
    marker = ", pinned" if pinned else ""

    # The sidecar is authoritative for the name when the ELF stamp is polluted
    # by a fork release tag (`latest-ksud`), because the producer deleted those
    # tags before running `git describe`.
    candidate = (sidecar.get("version_name") or "").strip() or name or ""
    if not candidate or is_polluted(candidate):
        return f"{display} ({code}{marker})"

    parsed = parse_describe(candidate)
    tag = parsed["tag"]
    distance = parsed["distance"] or 0
    suffix = f"+{distance}" if distance else ""
    return f"{display} v{tag}{suffix} ({code}{marker})"


def build_report(payloads: Path, sidecars: Path, max_age_hours: float) -> dict:
    now = datetime.now(timezone.utc)
    entries: dict[str, dict] = {}
    problems: list[str] = []
    warnings: list[str] = []

    for variant, spec in VARIANTS.items():
        path = payloads / spec["file"]
        if not path.is_file():
            problems.append(f"{variant}: payload {spec['file']} is missing")
            continue

        info = probe(path, variant)
        digest = sha256(path)
        info["sha256"] = digest

        sidecar_path = sidecars / f"{spec['file']}.version"
        sidecar = read_sidecar(sidecar_path)
        sidecar_name = (sidecar.get("version_name") or "").strip()
        sidecar_names_the_build = bool(sidecar_name) and not is_polluted(sidecar_name)

        # A `latest-ksud` release tag leaking into `git describe` is a defect of
        # the producing build, but it stays harmless as long as nothing else can
        # name the payload either - so only report it when it actually hides the
        # version from the UI.
        for problem in info["problems"]:
            if info["polluted"] and problem.startswith("version_name"):
                continue
            problems.append(f"{variant}: {problem}")
        if info["polluted"] and not sidecar_names_the_build:
            problems.append(
                f"{variant}: version_name {info['version_name']!r} came from a fork "
                "release tag and no producer sidecar supplies the real one - the "
                "producer's ksud build ran `git describe` with `latest-ksud` present"
            )
        if info["version_code"] is None:
            problems.append(f"{variant}: no version code stamped in the payload")

        if spec["release_tag"] and not sidecar:
            problems.append(
                f"{variant}: no {sidecar_path.name} published by {spec['source']} - "
                "the producer's auto-sync-build workflow has not run since the "
                "sidecar was introduced (check that Actions is enabled on that repo), "
                "so neither the version name nor the upstream distance can be verified"
            )
        if sidecar:
            if sidecar.get("version_code") and info["version_code"] is not None:
                if int(sidecar["version_code"]) != info["version_code"]:
                    problems.append(
                        f"{variant}: sidecar version_code {sidecar['version_code']} "
                        f"!= ELF stamp {info['version_code']}"
                    )
            if sidecar.get("sha256") and sidecar["sha256"] != digest:
                problems.append(
                    f"{variant}: sidecar sha256 does not match the downloaded binary"
                )
            behind = sidecar.get("behind_upstream")
            if behind is not None and behind.isdigit() and int(behind) > 0:
                problems.append(
                    f"{variant}: producer is {behind} upstream commit(s) behind "
                    f"{spec['source']} - the scheduled sync has not caught up"
                )
            built_at = sidecar.get("built_at")
            if built_at:
                try:
                    ts = datetime.strptime(built_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=timezone.utc
                    )
                    age = now - ts
                    if age > timedelta(hours=max_age_hours):
                        warnings.append(
                            f"{variant}: payload built {age_total(age)} ago "
                            f"(> {max_age_hours:g}h), upstream producer looks stalled"
                        )
                except ValueError:
                    warnings.append(f"{variant}: unparsable built_at {built_at!r}")

        entries[variant] = {
            "variant": variant,
            "display": spec["display"],
            "source": spec["source"],
            "file": spec["file"],
            "version_code": info["version_code"],
            "version_name": sidecar.get("version_name") or info["version_name"],
            "elf_version_name": info["version_name"],
            "tag": info["tag"],
            "distance": info["distance"],
            "commit": sidecar.get("commit") or info["commit"],
            "commit_count": info["commit_count"],
            "built_at": sidecar.get("built_at"),
            "version_pinned": sidecar.get("version_pinned", "").lower() == "true",
            "git_version_name": sidecar.get("git_version_name"),
            "behind_upstream": int(sidecar["behind_upstream"])
            if sidecar.get("behind_upstream", "").isdigit()
            else None,
            "upstream_commit": sidecar.get("upstream_commit"),
            "upstream_version_name": sidecar.get("upstream_version_name"),
            "size": info["size"],
            "sha256": digest,
            "label": format_label(
                spec["display"], info["version_name"], info["version_code"], sidecar
            ),
        }

    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "payloads_dir": str(payloads),
        "variants": entries,
        "problems": problems,
        "warnings": warnings,
    }


def age_total(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def render_manifest(report: dict) -> str:
    # Keep this file byte-stable for unchanged payloads so `--check` can be a
    # plain diff; the timestamp lives in payloads.lock.json instead.
    lines = [
        "# Generated by tools/gen_payload_versions.py - do not edit by hand.",
        "# Regenerated by .github/workflows/auto-build.yml from the bundled payloads.",
    ]
    for variant in VARIANTS:
        entry = report["variants"].get(variant)
        if not entry:
            continue
        lines.append(f"{variant}={entry['label']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--payloads",
        type=Path,
        default=Path("app/app/src/main/assets/payloads/ksud"),
        help="directory holding the ksud payload binaries",
    )
    ap.add_argument(
        "--sidecars",
        type=Path,
        default=None,
        help="directory holding the producer *.version sidecars (default: --payloads)",
    )
    ap.add_argument("--max-age-hours", type=float, default=48.0)
    ap.add_argument(
        "--lock-out",
        type=Path,
        default=None,
        help="where to write payloads.lock.json (default: inside --payloads). "
        "Pass a path outside the assets tree: app/build.gradle.kts verifyPayloads "
        "rejects any file in assets/payloads/ksud that is not a known role.",
    )
    ap.add_argument("--json", action="store_true", help="print the full report")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument(
        "--allow-problems",
        action="store_true",
        help="report problems but exit 0 (publish-then-fail pipelines)",
    )
    args = ap.parse_args(argv)

    payloads = args.payloads
    sidecars = args.sidecars or args.payloads
    if not payloads.is_dir():
        print(f"error: {payloads} is not a directory", file=sys.stderr)
        return 2

    report = build_report(payloads, sidecars, args.max_age_hours)

    for warning in report["warnings"]:
        print(f"WARNING {warning}")
    for problem in report["problems"]:
        print(f"ERROR   {problem}")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))

    manifest = render_manifest(report)
    lock = json.dumps(report, indent=2, sort_keys=True) + "\n"

    # Force LF so the committed file is byte-identical whether it was generated
    # on the Linux runner or on a Windows dev box (the repo sets `* -text`, so
    # the bytes land in git unchanged and --check compares them literally).
    lock_out = args.lock_out or (payloads / LOCK_NAME)
    if args.write:
        (payloads / MANIFEST_NAME).write_text(manifest, encoding="utf-8", newline="\n")
        print(f"wrote {payloads / MANIFEST_NAME}")
        lock_out.parent.mkdir(parents=True, exist_ok=True)
        lock_out.write_text(lock, encoding="utf-8", newline="\n")
        print(f"wrote {lock_out}")

    if args.check:
        current = (payloads / MANIFEST_NAME).read_text(encoding="utf-8").replace("\r\n", "\n")
        if current != manifest:
            print(
                f"ERROR   {payloads / MANIFEST_NAME} is stale - the shipped label "
                "does not match the bundled payloads",
                file=sys.stderr,
            )
            print(f"--- on disk ---\n{current}", file=sys.stderr)
            print(f"--- expected ---\n{manifest}", file=sys.stderr)
            return 1

    print()
    print(f"{'variant':8} {'code':>6}  label")
    for variant, entry in report["variants"].items():
        print(f"{variant:8} {entry['version_code']!s:>6}  {entry['label']}")

    if report["problems"] and not args.allow_problems:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

