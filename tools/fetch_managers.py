#!/usr/bin/env python3
"""Download the root manager APK for each bundled ksud variant.

Sources differ per project:

  default  Newest successful tiann/KernelSU main CI, final `manager` artifact.
  next     Newest successful KernelSU-Next/KernelSU-Next dev CI, non-spoofed
           `manager` artifact. These are candidates, not automatic assertions
           of compatibility with the existing Samsung kernel.
  sukisu   SukiSU-Nova v3.0.0 stays pinned; its manager shares the existing
           producer's UAPI. Never silently advance it to a future UAPI.
  maplesu  Provisional slot for 250965249/MapleSU (Samsung-side fork of
           FengQiuFQ/MapleSU). Its release channel does not exist yet, so this
           entry fails closed and, being a release-domain source, is allowed to
           carry the previous GhostSam release's manager forward.

Pinned and CI sources fail closed. Missing/expired artifacts are not replaced
with an arbitrary older APK. Signature and pairing verification are separate
steps; downloading an APK alone does not make it release-ready.

Requires GH_TOKEN in the environment (the workflow's GITHUB_TOKEN is enough).

Usage:
    python3 tools/fetch_managers.py --sidecars payloads-sidecars --out managers
"""

from __future__ import annotations

import argparse
import base64
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

from github_ci import full_revision, provenance, resolve_ci
#: Owner of VARIANTS/PROVISIONAL: the "registered but producer offline" set has
#: exactly one definition there, and this module reads it instead of keeping a
#: private copy that could drift.
from gen_payload_versions import MANAGER_SOURCE_MISSING, PROVISIONAL

SPEC: dict[str, dict] = {
    "default": {
        "kind": "ci",
        "repo": "tiann/KernelSU",
        "branch": "main",
        "workflow": "build-manager.yml",
        "artifact": "manager",
        "out": "GhostSam-manager-KernelSU.apk",
        "display": "KernelSU",
        "prefer": [
            re.compile(r"-arm64-v8a-release\.apk$"),
            re.compile(r"-universal-release\.apk$"),
            re.compile(r"(?<!spoofed)-release\.apk$"),
        ],
    },
    "next": {
        "kind": "ci",
        "repo": "KernelSU-Next/KernelSU-Next",
        "branch": "dev",
        "workflow": "build-manager-ci.yml",
        "artifact": "manager",
        "out": "GhostSam-manager-KernelSU-Next.apk",
        "display": "KernelSU-Next",
        "prefer": [
            re.compile(r"-arm64-v8a-release\.apk$"),
            re.compile(r"-universal-release\.apk$"),
            re.compile(r"(?<!spoofed)-release\.apk$"),
        ],
    },
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
    "maplesu": {
        # Provisional fourth slot, registered before its producer exists.
        # 250965249/MapleSU (Samsung-side fork of FengQiuFQ/MapleSU) has no
        # release, no nightly.link-indexed artifact and no
        # `GhostSam-manager-MapleSU.apk` anywhere yet, so today every branch of
        # this spec ends in `carry_over()` and then in "no previous GhostSam
        # release carried it".
        #
        # `kind: release` is the closest existing mechanism: MapleSU ships its
        # manager as an APK with the project's own identity (package
        # `dev.maplesu.manager`, signing cert DER 772B, certificate SHA-256
        # prefix 30bafb57…; the full 64-hex pin still has to be added to
        # verify_manager_apks.MANAGERS once the APK is in hand). Unlike `ci` and
        # `pinned_tag` sources, the release domain permits the documented
        # carry-over path.
        #
        # While the producer is offline this entry is skipped outright by
        # `main()`, which reads the single-source PROVISIONAL set from
        # gen_payload_versions; delete the key from that set (not from here) once
        # the release channel exists.
        "kind": "release",
        "repo": "250965249/MapleSU",
        "out": "GhostSam-manager-MapleSU.apk",
        "display": "MapleSU",
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
    # Explicit file-backed stdio also works in confined Windows environments,
    # where a grandchild using named pipes can fail before gh starts.
    with tempfile.TemporaryFile(dir=Path.cwd()) as stdout, tempfile.TemporaryFile(dir=Path.cwd()) as stderr:
        try:
            result = subprocess.run(["gh", *args], stdout=stdout, stderr=stderr, env=os.environ, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"gh {' '.join(args)} failed: {exc}") from exc
        stdout.seek(0)
        stderr.seek(0)
        output = stdout.read().decode("utf-8", errors="replace")
        error = stderr.read().decode("utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {error.strip()}")
    return output


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
    revision = release_revision(spec["repo"], tag)
    uapi = source_uapi(spec["repo"], revision)

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
        "source_commit": revision,
        "uapi_version": uapi,
        "pairing": "not-verified",
    }


def api_json(path: str) -> dict:
    return json.loads(gh("api", path))


def release_revision(repository: str, tag: str, api=None) -> str:
    api = api or api_json
    data = api(f"repos/{repository}/git/ref/tags/{tag}")
    obj = data.get("object") or {}
    for _ in range(8):
        sha = full_revision(obj.get("sha"))
        if obj.get("type") == "commit":
            return sha
        if obj.get("type") != "tag":
            raise RuntimeError("release tag does not identify a Git commit")
        obj = (api(f"repos/{repository}/git/tags/{sha}").get("object") or {})
    raise RuntimeError("release tag chain exceeds the verification limit")


def parse_uapi(text: str) -> int:
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.DOTALL)
    literal = r"(0[xX][0-9a-fA-F]+|\d+)[uU]?"
    values = re.findall(r"^\s*#define\s+KERNEL_SU_UAPI_VERSION\s+" + literal + r"\s*$", text, re.MULTILINE)
    values += re.findall(r"\b(?:static\s+)?const\s+\w+\s+KERNEL_SU_UAPI_VERSION\s*=\s*" + literal + r"\s*;", text)
    if len(values) != 1:
        raise RuntimeError("UAPI version is missing or ambiguous")
    value = int(values[0], 16 if values[0].lower().startswith("0x") else 10)
    if value <= 0:
        raise RuntimeError("UAPI version must be positive")
    return value


def source_uapi(repository: str, revision: str) -> int:
    data = api_json(f"repos/{repository}/contents/uapi/supercall.h?ref={revision}")
    if data.get("encoding") != "base64":
        raise RuntimeError(f"{repository}@{revision}: UAPI header is unavailable")
    return parse_uapi(base64.b64decode(data["content"]).decode("utf-8"))


def fetch_ci(variant: str, spec: dict, version_name: str | None, out_dir: Path, *, expected_revision: str | None = None) -> dict:
    run, artifact = resolve_ci(
        api_json, spec["repo"], spec["branch"], spec["workflow"], spec["artifact"],
        expected_revision=expected_revision,
    )
    target = out_dir / spec["out"]
    # Download exactly the audited artifact ID, not a mutable run/name URL.
    # Read only the selected APK entry; never extract arbitrary archive paths.
    with tempfile.TemporaryFile(dir=out_dir) as archive_file, tempfile.TemporaryFile(dir=out_dir) as stderr:
        result = subprocess.run(
            ["gh", "api", f"repos/{spec['repo']}/actions/artifacts/{artifact['id']}/zip"],
            stdout=archive_file, stderr=stderr, timeout=300,
        )
        if result.returncode:
            stderr.seek(0)
            raise RuntimeError(f"CI archive download failed: {stderr.read().decode('utf-8', errors='replace')}")
        archive_file.seek(0)
        archive_digest = hashlib.file_digest(archive_file, "sha256").hexdigest()
        if artifact["digest"] != f"sha256:{archive_digest}":
            raise RuntimeError("CI archive differs from its audited GitHub digest")
        archive_file.seek(0)
        with zipfile.ZipFile(archive_file) as archive:
            by_name = [{"name": name} for name in archive.namelist()]
            chosen = pick_asset(by_name, tuple(spec["prefer"]))
            if chosen is None:
                raise RuntimeError(f"{spec['repo']}: manager artifact contains no non-spoofed APK")
            infos = [info for info in archive.infolist() if info.filename == chosen["name"]]
            if len(infos) != 1:
                raise RuntimeError("manager archive has ambiguous APK names")
            info = infos[0]
            if info.file_size <= 0 or info.file_size > 512 * 1024 * 1024 or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError("invalid APK archive entry")
            with archive.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    return {
        "variant": variant, "display": spec["display"], "repo": spec["repo"],
        "tag": f"{spec['branch']}@{run['head_sha'][:12]}", "asset": chosen["name"],
        "file": spec["out"], "size": target.stat().st_size, "sha256": sha256(target),
        "ksud_version": version_name, "match": "trusted-ci", "kind": "ci",
        "url": run.get("html_url"), "apk_version": apk_label(chosen["name"]),
        "uapi_version": source_uapi(spec["repo"], run["head_sha"]),
        "ci": provenance(spec["repo"], spec["workflow"], run, artifact),
        "pairing": "not-verified",
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
    ap.add_argument("--payload-lock", type=Path, help="use the exact APK build's shared contract, not a fresh sidecar download")
    ap.add_argument("--out", type=Path, default=Path("managers"))
    ap.add_argument("--only", choices=sorted(SPEC), action="append")
    args = ap.parse_args(argv)

    contract = None
    if args.payload_lock is not None:
        try:
            contract = json.loads(args.payload_lock.read_text(encoding="utf-8"))
            if not isinstance(contract, dict) or not isinstance(contract.get("variants"), dict):
                raise ValueError("payload lock must be an object containing a variants object")
        except (OSError, ValueError) as exc:
            print(f"::error::invalid shared payload contract: {exc}", file=sys.stderr)
            return 1
    args.out.mkdir(parents=True, exist_ok=True)
    report: list[dict] = []
    failures: list[str] = []

    for variant, spec in SPEC.items():
        if args.only and variant not in args.only:
            continue
        if variant in PROVISIONAL:
            # The producer has no release channel at all yet, so there is nothing
            # to download and nothing to carry over. Skip loudly and keep the
            # other managers coming; the release gate still refuses to publish a
            # build whose manager set is incomplete.
            print(
                f"::warning::{variant}: provisional variant - {spec['repo']} has no "
                "release channel yet; skipped (drop it from "
                "gen_payload_versions.PROVISIONAL once the producer publishes)"
            )
            continue
        if variant in MANAGER_SOURCE_MISSING:
            # The payload is real and bundled, but the manager is a download we have
            # no source for yet: no release, and no earlier GhostSam release carries
            # GhostSam-manager-<variant>.apk, so carry_over() cannot help either.
            # Warn with the exact gap and keep the other managers coming; the release
            # gate still requires the APK once the variant joins the publication set.
            print(
                f"::warning::{variant}: manager source missing - {spec['repo']} has no "
                "downloadable manager APK and no earlier GhostSam release carries "
                f"{spec['out']}; the payload is bundled but the manager slot is empty "
                "(drop it from gen_payload_versions.MANAGER_SOURCE_MISSING once the "
                "official APK is available)"
            )
            continue
        try:
            if contract is not None:
                entry = contract.get("variants", {}).get(variant)
                if not isinstance(entry, dict):
                    raise RuntimeError(f"{variant}: shared APK build contract is missing")
                version_name = entry.get("version_name")
                expected_revision = entry.get("official_ci_commit")
                if spec["kind"] == "ci" and expected_revision is None:
                    raise RuntimeError(f"{variant}: shared contract has no official CI snapshot; refusing an unrelated latest manager")
            else:
                version_name = read_version_name(args.sidecars, variant)
                expected_revision = None
            if spec["kind"] == "ci":
                report.append(fetch_ci(variant, spec, version_name, args.out, expected_revision=expected_revision))
            elif spec["kind"] == "nightly":
                report.append(fetch_nightly(variant, spec, version_name, args.out))
            else:
                report.append(fetch_release(variant, spec, version_name, args.out))
        except (RuntimeError, json.JSONDecodeError) as exc:
            # A pinned manager is part of the compatibility contract. Carrying
            # over an arbitrary older APK would make the ksud/manager pair
            # unknowable, so fail the managers job and publish nothing for it.
            if spec.get("pinned_tag") or spec["kind"] == "ci":
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
