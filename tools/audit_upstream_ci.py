#!/usr/bin/env python3
"""Compare existing Samsung source baselines against exact official CI commits.

A fork's "behind_upstream=0" may only refer to another fork, not the official
project. This read-only audit compares both histories from a verified common
ancestor. It neither merges patches nor builds/loads a kernel. Ancestry and
matching UAPI are necessary checks, not proof of runtime compatibility.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fetch_managers import api_json, source_uapi
from github_ci import CiSourceError, full_revision

BASELINES = {
    "default": {
        "repo": "snothin/KernelSU", "branch": "samsung",
        "anchor": "0c0fdb960178fa12d80d6cbcfdcb6ad8be6a865d",
        "official": "tiann/KernelSU",
        "produced_build": "no published CI artifact or sidecar verified",
    },
    "next": {
        "repo": "250965249/KernelSU-Next-Samsung", "branch": "samsung",
        "anchor": "4c706b5938665866ebe16944a481a7b91e3396af",
        "official": "KernelSU-Next/KernelSU-Next",
        "produced_build": "latest-ksud; its intermediate upstream is not the official dev branch",
    },
}


def collect_delta(api, repository: str, anchor: str, revision: str, *, max_pages: int = 20) -> set[str]:
    full_revision(anchor)
    full_revision(revision)
    commits: set[str] = set()
    expected = None
    for page in range(1, max_pages + 1):
        data = api(f"repos/{repository}/compare/{anchor}...{revision}?per_page=100&page={page}")
        if (data.get("merge_base_commit") or {}).get("sha") != anchor or data.get("behind_by") != 0:
            raise CiSourceError(f"{repository}: configured common ancestor is not an ancestor of {revision}")
        if not isinstance(data.get("ahead_by"), int) or data["ahead_by"] < 0:
            raise CiSourceError("compare response lacks a reliable commit count")
        if expected is None:
            expected = data["ahead_by"]
        elif expected != data["ahead_by"]:
            raise CiSourceError("compare result changed while paging")
        batch = data.get("commits") or []
        for item in batch:
            commits.add(full_revision(item.get("sha")))
        if len(commits) == expected:
            return commits
        if len(commits) > expected or len(batch) < 100:
            raise CiSourceError("compare history is incomplete; refusing to report zero missing commits")
    raise CiSourceError("compare pagination limit reached; source synchronization remains unverified")


def compare_histories(api, official: str, producer: str, anchor: str, official_revision: str, producer_revision: str) -> dict:
    upstream = collect_delta(api, official, anchor, official_revision)
    adapted = collect_delta(api, producer, anchor, producer_revision)
    missing = sorted(upstream - adapted)
    extra = sorted(adapted - upstream)
    return {
        "common_ancestor": anchor, "official_ci_commit": official_revision,
        "producer_source_commit": producer_revision,
        "official_commits_missing": len(missing), "producer_only_commits": len(extra),
        "missing_commits": missing,
        "source_caught_up": not missing,
        "patch_semantics_reviewed": False,
    }


def audit(records: list[dict], api=api_json) -> dict:
    entries = {}
    for variant, spec in BASELINES.items():
        candidates = [r for r in records if r.get("variant") == variant]
        if len(candidates) != 1:
            raise CiSourceError(f"{variant}: manager provenance record missing or duplicated")
        record = candidates[0]
        ci = record.get("ci") or {}
        if ci.get("repository") != spec["official"] or record.get("pairing") != "not-verified":
            raise CiSourceError(f"{variant}: expected an unpaired official CI candidate")
        official_commit = full_revision(ci.get("commit"))
        ref = api(f"repos/{spec['repo']}/git/ref/heads/{quote(spec['branch'], safe='')}")
        producer_commit = full_revision((ref.get("object") or {}).get("sha"))
        result = compare_histories(api, spec["official"], spec["repo"], spec["anchor"], official_commit, producer_commit)
        result.update({
            "official_repository": spec["official"], "producer_repository": spec["repo"],
            "producer_branch": spec["branch"], "manager_ci": ci,
            "manager_uapi": record.get("uapi_version"),
            "signature_verified": record.get("signature_verified") is True,
            "producer_build_status": spec["produced_build"],
            "runtime_compatibility": "not-tested", "release_ready": False,
        })
        try:
            kernel_uapi = source_uapi(spec["repo"], producer_commit)
        except (RuntimeError, ValueError, KeyError) as exc:
            result["kernel_uapi"] = None
            result["uapi_error"] = str(exc)
            result["uapi_compatible"] = None
        else:
            result["kernel_uapi"] = kernel_uapi
            manager_uapi = record.get("uapi_version")
            result["uapi_compatible"] = isinstance(manager_uapi, int) and manager_uapi <= kernel_uapi
        entries[variant] = result
    return {
        "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "read-only-source-and-uapi-audit",
        "variants": entries,
        "release_boundary": "CI candidates are not automatically Samsung-compatible payloads or releases",
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--managers", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = audit(json.loads(args.managers.read_text(encoding="utf-8")))
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for variant, result in report["variants"].items():
            print(f"{variant}: missing official CI commits={result['official_commits_missing']}; "
                  f"manager UAPI={result['manager_uapi']}, baseline UAPI={result['kernel_uapi']}; not release-ready")
    except (CiSourceError, RuntimeError, OSError, ValueError, KeyError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
