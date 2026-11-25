#!/usr/bin/env python3
"""Emit a Samsung producer `.version` sidecar for a payload that was built *here*.

The Samsung producers in 250965249/{KernelSU,KernelSU-Next,SukiSU-Ultra} emit this
file inside their own CI. MapleSU's payload for GhostSam is taken from a MapleSU
fork run artifact instead (no `latest-ksud` release yet), so the receipt has to be
written on the consumer side - but it must still describe *measured* facts only:

  * `sha256` and `producer_artifact_digest` come from the bytes and from the
    Actions artifact metadata supplied on the command line;
  * `version_code` / `version_name` come from the `KSU_NO_NEW_PRIVS<code><name>`
    stamp that `probe_payload_version.probe()` reads out of the binary itself;
  * everything this script cannot verify (`official_ci_commit`,
    `kernel_version_code`, `upstream_commit`, ...) is left EMPTY rather than
    guessed, and `--require` lists the fields that must be non-empty for the
    caller's contract level.

Field names and order follow a real producer sidecar
(`ksud-default-aarch64.version` from 250965249/KernelSU@latest-ksud), so
`verify_samsung_payloads.py` and `gen_payload_versions.py` parse it unchanged.

Usage:
  python3 tools/emit_samsung_sidecar.py \\
      --payload ksud-maplesu --variant maplesu \\
      --commit b3e5057c99b3fba5901cbed6a4140a7d59462d6f \\
      --run-id 45523572248 --artifact-id 14980758843 \\
      --artifact-digest sha256:83a4e976... \\
      --built-at 2026-11-25T01:21:02Z --kernel-uapi 6 \\
      --out tools/sidecars/ksud-maplesu.version \\
      --require variant,commit,sha256,version_code,version_name,producer_run_id,producer_artifact_id,producer_artifact_digest,build_kind,kernel_uapi_version

  # re-verify an existing sidecar against a payload (CI gate):
  python3 tools/emit_samsung_sidecar.py --check \\
      --payload ksud-maplesu --variant maplesu --out tools/sidecars/ksud-maplesu.version
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

from probe_payload_version import probe

#: Field order copied from a live producer sidecar (see module docstring).
ORDER = [
    "variant", "version_code", "version_name", "commit", "commit_short", "built_at",
    "version_pinned", "merge_auto_resolved", "git_version_name", "behind_upstream",
    "upstream_commit", "upstream_version_name", "sha256", "build_kind",
    "kernel_version_code", "kernel_uapi_version", "official_ci_commit",
    "producer_run_id", "producer_artifact_id", "producer_artifact_digest",
]
REQUIRED_FOR_VERIFY = ("variant", "commit", "sha256", "version_code", "version_name")


def read_sidecar(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def fail(message: str) -> int:
    print(f"ERROR {message}", file=sys.stderr)
    return 1


def build(args) -> dict[str, str]:
    payload = Path(args.payload)
    data = payload.read_bytes()
    info = probe(payload, args.variant)
    fields: dict[str, str] = {"variant": args.variant}

    fields["version_code"] = "" if info["version_code"] is None else str(info["version_code"])
    name = (info["version_name"] or "").strip()
    fields["version_name"] = name
    fields["commit"] = args.commit or ""
    fields["commit_short"] = (args.commit or "")[:8]
    fields["built_at"] = args.built_at or ""
    fields["version_pinned"] = "false"
    fields["merge_auto_resolved"] = ""
    fields["git_version_name"] = name
    fields["behind_upstream"] = "" if args.behind_upstream is None else str(args.behind_upstream)
    fields["upstream_commit"] = args.upstream_commit or ""
    fields["upstream_version_name"] = args.upstream_version_name or ""
    fields["sha256"] = hashlib.sha256(data).hexdigest()
    fields["build_kind"] = args.build_kind
    fields["kernel_version_code"] = "" if args.kernel_version_code is None else str(args.kernel_version_code)
    fields["kernel_uapi_version"] = "" if args.kernel_uapi is None else str(args.kernel_uapi)
    fields["official_ci_commit"] = args.official_ci_commit or ""
    fields["producer_run_id"] = "" if args.run_id is None else str(args.run_id)
    fields["producer_artifact_id"] = "" if args.artifact_id is None else str(args.artifact_id)
    fields["producer_artifact_digest"] = args.artifact_digest or ""
    return fields


def validate(args, fields: dict[str, str]) -> list[str]:
    problems: list[str] = []
    if not fields["sha256"]:
        problems.append("payload has no digest")
    if fields["commit"] and not re.fullmatch(r"[0-9a-f]{40}", fields["commit"]):
        problems.append(f"commit {fields['commit']!r} is not a 40-hex revision")
    if fields["producer_artifact_digest"] and not re.fullmatch(r"sha256:[0-9a-f]{64}", fields["producer_artifact_digest"]):
        problems.append(f"artifact digest {fields['producer_artifact_digest']!r} is not sha256:<64 hex>")
    if fields["kernel_uapi_version"] and not fields["kernel_uapi_version"].isdigit():
        problems.append("kernel_uapi_version must be an integer")
    info = probe(Path(args.payload), args.variant)
    problems.extend(f"payload: {p}" for p in info["problems"])
    if info["version_code"] is None:
        problems.append("payload carries no KSU_NO_NEW_PRIVS<code><name> stamp, so version_code cannot be measured")
    for name in (args.require or "").split(","):
        name = name.strip()
        if name and not fields.get(name):
            problems.append(f"required field {name!r} is empty (this script never invents a value)")
    return problems


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--payload", required=True, type=Path)
    ap.add_argument("--variant", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--commit", default="")
    ap.add_argument("--built-at", default="")
    ap.add_argument("--build-kind", default="samsung-ci")
    ap.add_argument("--kernel-version-code", type=int, default=None)
    ap.add_argument("--kernel-uapi", type=int, default=None)
    ap.add_argument("--official-ci-commit", default="")
    ap.add_argument("--run-id", type=int, default=None)
    ap.add_argument("--artifact-id", type=int, default=None)
    ap.add_argument("--artifact-digest", default="")
    ap.add_argument("--behind-upstream", type=int, default=None)
    ap.add_argument("--upstream-commit", default="")
    ap.add_argument("--upstream-version-name", default="")
    ap.add_argument("--require", default=",".join(REQUIRED_FOR_VERIFY))
    ap.add_argument("--check", action="store_true", help="verify an existing --out against --payload instead of writing")
    args = ap.parse_args(argv)

    if not args.payload.is_file():
        return fail(f"payload not found: {args.payload}")

    if args.check:
        fields = read_sidecar(args.out)
        if not fields:
            return fail(f"sidecar not found or empty: {args.out}")
        info = probe(args.payload, args.variant)
        data = args.payload.read_bytes()
        problems = []
        if fields.get("variant") != args.variant:
            problems.append(f"variant mismatch: {fields.get('variant')!r}")
        if fields.get("sha256") != hashlib.sha256(data).hexdigest():
            problems.append("sha256 does not match the payload bytes")
        if str(info["version_code"]) != fields.get("version_code"):
            problems.append(f"version_code {fields.get('version_code')!r} != stamp {info['version_code']}")
        if info["version_name"] and fields.get("version_name") != info["version_name"]:
            problems.append(f"version_name {fields.get('version_name')!r} != stamp {info['version_name']!r}")
        for name in (args.require or "").split(","):
            name = name.strip()
            if name and not fields.get(name):
                problems.append(f"required field {name!r} is empty")
        for problem in problems:
            print(f"ERROR {problem}", file=sys.stderr)
        if problems:
            return 1
        print(f"{args.out}: sidecar matches {args.payload}")
        return 0

    fields = build(args)
    problems = validate(args, fields)
    for problem in problems:
        print(f"ERROR {problem}", file=sys.stderr)
    if problems:
        return 1

    lines = [f"{key}={fields[key]}" for key in ORDER]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.out} ({len(lines)} fields)")
    for key in ORDER:
        if fields[key]:
            print(f"  {key}={fields[key]}")
        else:
            print(f"  {key}=<empty: not verifiable here>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
