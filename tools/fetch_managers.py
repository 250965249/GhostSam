#!/usr/bin/env python3
"""Download the root manager APK for each bundled ksud variant.

Sources differ per project:

  sukisu  SukiSU-Nova v3.0.0 is pinned because the Samsung ksud producer and
          this manager share the tested UAPI. Never silently advance to a future
          release whose UAPI may require a newer kernel.

Anything that still cannot be resolved carries the manager from the previous
GhostSam release forward, so a release never ships without one.

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

SPEC: dict[str, dict] = {
    "sukisu": {
        # This manager shares its UAPI with the Samsung producer. The producer's
        # git describe still uses an Ultra tag, so do not infer a Nova release
        # from that tag or silently advance the manager to a future UAPI.
        "pinned_tag": "v3.0.0",
        "kind": "release",
        "repo": "SukiSU-Nova/SukiSU-Nova",
        "out": "GhostSam-manager-SukiSU.apk",
        "display": "SukiSU-Nova",
        "prefer": [
            re.compile(r"-universal-release\.apk$"),
            re.compile(r"(?<!spoofed)-release\.apk$"),
            re.compile(r"\.apk$"),
        ],
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


def pick_asset(assets: list[dict], prefer: tuple = ASSET_ORDER) -> dict | None:
    apks = [a for a in assets if a["name"].lower().endswith(".apk")]
    apks = [a for a in apks if "spoof" not in a["name"].lower()]
    if not apks:
        return None
    for pattern in prefer:
        for asset in apks:
            if pattern.search(asset["name"]):
                return asset
    return apks[0]


def list_releases(repo: str) -> list[dict]:
    """Newest-first release metadata.

    `gh api .../releases` comes back empty for SukiSU-Nova (the repository was
    renamed; the collection endpoint resolves to an empty page for the old id)
    while `gh release list` and `releases/tags/<tag>` answer normally, so go
    through the CLI and fetch assets per tag.
    """
    out = gh(
        "release", "list", "-R", repo, "--limit", "30",
        "--json", "tagName,isPrerelease,isLatest,publishedAt",
    )
    return [r for r in json.loads(out) if not r.get("isDraft")]


def release_assets(repo: str, tag: str) -> list[dict]:
    try:
        data = json.loads(gh("api", f"repos/{repo}/releases/tags/{tag}"))
    except (RuntimeError, json.JSONDecodeError):
        return []
    return data.get("assets") or []


def pick_release(
    repo: str, releases: list[dict], base: str | None, prefer: tuple = ASSET_ORDER,
    *, pinned_tag: str | None = None,
) -> tuple[dict | None, str, dict | None]:
    """Select a compatible APK; an explicit pin must never fall back to another tag."""
    if pinned_tag:
        asset = pick_asset(release_assets(repo, pinned_tag), prefer)
        if asset:
            return {"tagName": pinned_tag}, "exact", asset
        return None, "none", None
    candidates: list[tuple[dict, str]] = []

    if base:
        for tag in (f"v{base}", base):
            for release in releases:
                if release["tagName"] == tag:
                    candidates.append((release, "exact"))

    for release in releases:
        if not release.get("isPrerelease"):
            candidates.append((release, "newest-stable"))
    for release in releases:
        candidates.append((release, "newest"))

    seen: set[str] = set()
    for release, how in candidates:
        tag = release["tagName"]
        if tag in seen:
            continue
        seen.add(tag)
        asset = pick_asset(release_assets(repo, tag), prefer)
        if asset:
            return release, how, asset
    return None, "none", None


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
    releases = [] if spec.get("pinned_tag") else list_releases(spec["repo"])
    prefer = tuple(spec.get("prefer") or ASSET_ORDER)
    release, how, asset = pick_release(
        spec["repo"], releases, base, prefer, pinned_tag=spec.get("pinned_tag"),
    )
    if not release or not asset:
        raise RuntimeError(f"{spec['repo']}: no release carries an APK")
    tag = release["tagName"]

    with tempfile.TemporaryDirectory() as tmp:
        gh(
            "release", "download", tag,
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

    print(f"{variant}: {spec['repo']} {tag} -> {asset['name']} ({how})")
    return {
        "variant": variant,
        "display": spec["display"],
        "repo": spec["repo"],
        "tag": tag,
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
            rel, how2, picked = pick_release(
                spec["release_repo"],
                list_releases(spec["release_repo"]),
                base_version(version_name),
                tuple(spec.get("prefer") or ASSET_ORDER),
            )
            if not rel or not picked:
                raise RuntimeError(f"{variant}: neither nightly.link nor a release had an APK")
            with tempfile.TemporaryDirectory() as tmp2:
                gh(
                    "release", "download", rel["tagName"],
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
            # A pinned manager is part of the compatibility contract. Carrying
            # over an arbitrary older APK would make the ksud/manager pair
            # unknowable, so fail the managers job and publish nothing for it.
            if spec.get("pinned_tag"):
                print(f"::error::{variant}: {exc}", file=sys.stderr)
                failures.append(variant)
                continue
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
