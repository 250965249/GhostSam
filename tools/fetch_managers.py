#!/usr/bin/env python3
"""Download the root manager APK that matches each bundled ksud version.

The managers are built and signed by the upstream projects, so GhostSam does not
rebuild them — it just pulls the release that matches the ksud it is shipping:

    ksud 4.3.0-r2-17-g1c0913bd  ->  SukiSU-Ultra  release v4.3.0-r2
    ksud 4.2.0-rc3-99-g1504e901 ->  Baka-SU/BakaSU release v4.2.0-rc3

Tag selection: take the version name, strip the `-<count>-g<sha>` describe
suffix, and look for `v<base>` (then `<base>`). If that release has no APK, fall
back to the newest non-draft release that does, and say so in the report.

Requires GH_TOKEN in the environment (the workflow's GITHUB_TOKEN is enough for
public repositories).

Usage:
    python3 tools/fetch_managers.py --sidecars payloads-sidecars --out managers
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

#: variant -> upstream repository, output file name, display name
SPEC = {
    "sukisu": {
        "repo": "SukiSU-Ultra/SukiSU-Ultra",
        "out": "GhostSam-manager-SukiSU.apk",
        "display": "SukiSU-Ultra",
    },
    "bakasu": {
        "repo": "Baka-SU/BakaSU",
        "out": "GhostSam-manager-BakaSU.apk",
        "display": "BakaSU",
    },
}

DESCRIBE_TAIL = re.compile(r"-\d+-g[0-9a-f]{7,40}$")

#: asset preference, best first
ASSET_ORDER = (
    re.compile(r"universal-release\.apk$"),
    re.compile(r"(?<!spoofed)-release\.apk$"),
    re.compile(r"arm64-v8a.*\.apk$"),
    re.compile(r"\.apk$"),
)


def gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=os.environ,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def base_version(version_name: str | None) -> str | None:
    if not version_name:
        return None
    return DESCRIBE_TAIL.sub("", version_name.strip()) or None


def pick_asset(assets: list[dict]) -> dict | None:
    apks = [a for a in assets if a["name"].lower().endswith(".apk")]
    apks = [a for a in apks if "spoof" not in a["name"].lower()]
    if not apks:
        return None
    for pattern in ASSET_ORDER:
        for asset in apks:
            if pattern.search(asset["name"]):
                return asset
    return apks[0]


def pick_release(releases: list[dict], base: str | None) -> tuple[dict | None, str]:
    usable = [r for r in releases if not r.get("draft")]
    with_apk = [r for r in usable if pick_asset(r.get("assets") or [])]

    if base:
        for candidate in (f"v{base}", base):
            for release in with_apk:
                if release["tag_name"] == candidate:
                    return release, "exact"

    stable = [r for r in with_apk if not r.get("prerelease")]
    if stable:
        return stable[0], "newest-stable"
    if with_apk:
        return with_apk[0], "newest"
    return None, "none"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_variant(variant: str, spec: dict, sidecars: Path, out_dir: Path) -> dict:
    sidecar = sidecars / f"ksud-{variant}.version"
    version_name = None
    if sidecar.is_file():
        for line in sidecar.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("version_name="):
                version_name = line.split("=", 1)[1].strip()
    base = base_version(version_name)

    raw = gh("api", f"repos/{spec['repo']}/releases?per_page=30")
    releases = json.loads(raw)
    release, how = pick_release(releases, base)
    if not release:
        raise RuntimeError(f"{spec['repo']}: no release carries an APK")

    asset = pick_asset(release.get("assets") or [])
    if not asset:
        raise RuntimeError(f"{spec['repo']} {release['tag_name']}: no APK asset")

    with tempfile.TemporaryDirectory() as tmp:
        gh(
            "release", "download", release["tag_name"],
            "-R", spec["repo"],
            "--pattern", asset["name"],
            "--dir", tmp,
            "--clobber",
        )
        downloaded = Path(tmp) / asset["name"]
        if not downloaded.is_file():
            raise RuntimeError(f"{spec['repo']}: {asset['name']} not downloaded")
        target = out_dir / spec["out"]
        shutil.move(str(downloaded), target)

    digest = sha256(target)
    print(
        f"{variant}: {spec['repo']} {release['tag_name']} -> {asset['name']} "
        f"({target.stat().st_size} bytes, match={how})"
    )
    return {
        "variant": variant,
        "display": spec["display"],
        "repo": spec["repo"],
        "tag": release["tag_name"],
        "asset": asset["name"],
        "file": spec["out"],
        "size": target.stat().st_size,
        "sha256": digest,
        "ksud_version": version_name,
        "match": how,
        "url": asset.get("browser_download_url"),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sidecars", type=Path, default=Path("payloads-sidecars"))
    ap.add_argument("--out", type=Path, default=Path("managers"))
    ap.add_argument("--only", choices=sorted(SPEC), action="append")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    report: list[dict] = []
    failures: list[str] = []

    for variant, spec in SPEC.items():
        if args.only and variant not in args.only:
            continue
        try:
            report.append(fetch_variant(variant, spec, args.sidecars, args.out))
        except (RuntimeError, json.JSONDecodeError) as exc:
            print(f"::error::{variant}: {exc}", file=sys.stderr)
            failures.append(variant)

    (args.out / "managers.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.out / 'managers.json'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
