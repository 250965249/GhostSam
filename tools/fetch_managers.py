#!/usr/bin/env python3
"""Download the root manager APK for each bundled ksud variant.

Sources differ per project:

  sukisu  the official release that matches the ksud version. Take the version
          name from the sidecar (`4.3.0-r2-17-g...`), strip the `-<n>-g<sha>`
          describe suffix, and look for tag `v4.3.0-r2`.
  bakasu  BakaSU no longer publishes a usable manager in its releases; the
          manager is built by its `build-manager` workflow on `main` and exposed
          by nightly.link as a zip:
          https://nightly.link/Baka-SU/BakaSU/workflows/build-manager/main/Manager-release.zip
          Take the arm64-v8a APK out of that zip (universal as fallback), and
          fall back to the release APK if nightly.link is unavailable.

Requires GH_TOKEN in the environment (the workflow's GITHUB_TOKEN is enough).

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
import urllib.request
import zipfile
from pathlib import Path

NIGHTLY_BAKASU = (
    "https://nightly.link/Baka-SU/BakaSU/workflows/build-manager/main/"
    "Manager-release.zip"
)

SPEC: dict[str, dict] = {
    "sukisu": {
        "kind": "release",
        "repo": "SukiSU-Ultra/SukiSU-Ultra",
        "out": "GhostSam-manager-SukiSU.apk",
        "display": "SukiSU-Ultra",
    },
    "bakasu": {
        "kind": "nightly",
        "repo": "Baka-SU/BakaSU",
        "url": NIGHTLY_BAKASU,
        "out": "GhostSam-manager-BakaSU.apk",
        "display": "BakaSU",
        "prefer": [
            re.compile(r"-arm64-v8a-release\.apk$"),
            re.compile(r"-universal-release\.apk$"),
            re.compile(r"(?<!spoofed)-release\.apk$"),
        ],
        "release_repo": "Baka-SU/BakaSU",
    },
}

DESCRIBE_TAIL = re.compile(r"-\d+-g[0-9a-f]{7,40}$")
ASSET_ORDER = (
    re.compile(r"universal-release\.apk$"),
    re.compile(r"(?<!spoofed)-release\.apk$"),
    re.compile(r"arm64-v8a.*\.apk$"),
    re.compile(r"\.apk$"),
)
APK_VERSION = re.compile(r"_v?(?P<tag>[0-9][0-9A-Za-z.\-]*?)_(?P<code>\d+)-")


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


def read_version_name(sidecars: Path, variant: str) -> str | None:
    sidecar = sidecars / f"ksud-{variant}.version"
    if not sidecar.is_file():
        return None
    for line in sidecar.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("version_name="):
            return line.split("=", 1)[1].strip() or None
    return None


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


def apk_label(name: str) -> str:
    m = APK_VERSION.search(name)
    if not m:
        return ""
    return f"{m.group('tag')} ({m.group('code')})"



def carry_over(variant: str, spec: dict, out_dir: Path, reason: str) -> dict:
    """Reuse the manager from the previous GhostSam release.

    Upstream reorganises its repositories every so often: SukiSU-Ultra deleted
    every release and now only exposes manager artifacts through Actions, which
    nightly.link does not index for that repository. Rather than ship a release
    without a manager, carry the last one we published forward and say so.
    """
    print(f'::warning::{variant}: {reason} - carrying the manager over from the previous release')
    tags = json.loads(
        gh('release', 'list', '-R', '250965249/GhostSam', '--limit', '30',
           '--json', 'tagName,publishedAt')
    )
    for entry in tags:
        tag = entry['tagName']
        if not tag.startswith('auto-'):
            continue
        with tempfile.TemporaryDirectory() as tmp:
            try:
                gh('release', 'download', tag, '-R', '250965249/GhostSam',
                   '--pattern', spec['out'], '--dir', tmp, '--clobber')
            except RuntimeError:
                continue
            src = Path(tmp) / spec['out']
            if not src.is_file():
                continue
            target = out_dir / spec['out']
            shutil.move(str(src), target)
            return {
                'variant': variant,
                'display': spec['display'],
                'repo': '250965249/GhostSam',
                'tag': tag,
                'asset': spec['out'],
                'file': spec['out'],
                'size': target.stat().st_size,
                'sha256': sha256(target),
                'ksud_version': None,
                'match': 'carried-over',
                'url': None,
                'kind': 'carry-over',
                'apk_version': '',
            }
    raise RuntimeError(f"{variant}: no previous GhostSam release carried {spec['out']}")


def fetch_release(variant: str, spec: dict, version_name: str | None, out_dir: Path) -> dict:
    base = base_version(version_name)
    releases = json.loads(gh("api", f"repos/{spec['repo']}/releases?per_page=30"))
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

    print(f"{variant}: {spec['repo']} {release['tag_name']} -> {asset['name']} ({how})")
    return {
        "variant": variant,
        "display": spec["display"],
        "repo": spec["repo"],
        "tag": release["tag_name"],
        "asset": asset["name"],
        "file": spec["out"],
        "size": target.stat().st_size,
        "sha256": sha256(target),
        "ksud_version": version_name,
        "match": how,
        "url": asset.get("browser_download_url"),
        "kind": "release",
        "apk_version": apk_label(asset["name"]),
    }


def http_get(url: str, dest: Path, attempts: int = 4) -> None:
    last: Exception | None = None
    for _ in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "GhostSam-auto-build"})
            with urllib.request.urlopen(req, timeout=300) as resp, dest.open("wb") as fh:
                shutil.copyfileobj(resp, fh, 1 << 20)
            if dest.stat().st_size > 0:
                return
        except Exception as exc:  # noqa: BLE001 - surfaced after the last attempt
            last = exc
    raise RuntimeError(f"download failed: {url}: {last}")


def fetch_nightly(variant: str, spec: dict, version_name: str | None, out_dir: Path) -> dict:
    target = out_dir / spec["out"]
    how = "nightly.link"
    asset = ""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        archive = tmpdir / "Manager-release.zip"
        try:
            http_get(spec["url"], archive)
            with zipfile.ZipFile(archive) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith(".apk")]
                chosen = ""
                for pattern in spec["prefer"]:
                    for name in names:
                        if pattern.search(name):
                            chosen = name
                            break
                    if chosen:
                        break
                if not chosen:
                    raise RuntimeError(f"{spec['repo']}: no APK inside the artifact zip")
                with zf.open(chosen) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
            asset = chosen
        except RuntimeError as exc:
            print(f"::warning::{variant}: {exc} - falling back to the release APK")
            releases = json.loads(
                gh("api", f"repos/{spec['release_repo']}/releases?per_page=30")
            )
            rel, how2 = pick_release(releases, base_version(version_name))
            if not rel:
                raise RuntimeError(f"{variant}: neither nightly.link nor a release had an APK")
            picked = pick_asset(rel.get("assets") or [])
            with tempfile.TemporaryDirectory() as tmp2:
                gh(
                    "release", "download", rel["tag_name"],
                    "-R", spec["release_repo"],
                    "--pattern", picked["name"],
                    "--dir", tmp2,
                    "--clobber",
                )
                shutil.move(str(Path(tmp2) / picked["name"]), target)
            asset = picked["name"]
            how = f"release-fallback({how2})"

    print(f"{variant}: {spec['repo']} {how} -> {asset}")
    return {
        "variant": variant,
        "display": spec["display"],
        "repo": spec["repo"],
        "tag": "build-manager@main" if how == "nightly.link" else asset,
        "asset": asset,
        "file": spec["out"],
        "size": target.stat().st_size,
        "sha256": sha256(target),
        "ksud_version": version_name,
        "match": how,
        "url": spec.get("url"),
        "kind": "nightly",
        "apk_version": apk_label(asset),
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
        version_name = read_version_name(args.sidecars, variant)
        try:
            if spec["kind"] == "nightly":
                report.append(fetch_nightly(variant, spec, version_name, args.out))
            else:
                report.append(fetch_release(variant, spec, version_name, args.out))
        except (RuntimeError, json.JSONDecodeError) as exc:
            try:
                report.append(carry_over(variant, spec, args.out, str(exc)))
            except RuntimeError as exc2:
                print(f"::error::{variant}: {exc2}", file=sys.stderr)
                failures.append(variant)

    (args.out / "managers.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.out / 'managers.json'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
