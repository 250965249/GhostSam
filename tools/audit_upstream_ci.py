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
    "maplesu": {
        "repo": "FengQiuFQ/MapleSU", "branch": "main",
        "anchor": "ff3aa44850ea22717fa811a17831155a85f43445",
        "official": "FengQiuFQ/MapleSU",
        "produced_build": "latest-ksud; MapleSU standard variant (source-declared pin placeholder)",
    },
    "default": {
        "repo": "250965249/KernelSU", "branch": "samsung",
        "anchor": "2a60b2893b8d0d885d1ceff2287d8ad854f0e0f4",
        "official": "tiann/KernelSU",
        "produced_build": "latest-ksud; official main + Samsung adaptation producer",
    },
    "next": {
        "repo": "250965249/KernelSU-Next", "branch": "samsung",
        "anchor": "32e25d58fc1a187e1abe7278904c31571235fc83",
        "official": "KernelSU-Next/KernelSU-Next",
        "produced_build": "latest-ksud; official dev + Samsung adaptation producer",
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


def audit(records: list[dict], api=api_json, uapi_reader=source_uapi) -> dict:
    entries = {}
    retrieval_errors = []
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
        reasons = ["producer_receipt_not_verified", "runtime_not_tested"]
        if not result["source_caught_up"]:
            reasons.append("official_ci_commits_missing")
        if not result["signature_verified"]:
            reasons.append("manager_signature_not_verified")
        try:
            kernel_uapi = uapi_reader(spec["repo"], producer_commit)
            manager_uapi = record.get("uapi_version")
            if type(kernel_uapi) is not int or kernel_uapi <= 0 or type(manager_uapi) is not int or manager_uapi <= 0:
                raise ValueError("UAPI values must be verified positive integers")
        except (RuntimeError, ValueError, KeyError) as exc:
            result["kernel_uapi"] = None
            result["uapi_error"] = str(exc)
            result["uapi_compatible"] = None
            result["uapi_status"] = "unavailable"
            retrieval_errors.append(f"{variant}: {exc}")
            reasons.append("uapi_verification_unavailable")
        else:
            result["kernel_uapi"] = kernel_uapi
            # Equality is the current release contract. A lower manager UAPI
            # must not be called a reviewed pair merely because it is numeric.
            result["uapi_compatible"] = manager_uapi == kernel_uapi
            result["uapi_status"] = "equal_contract" if manager_uapi == kernel_uapi else "different_contract"
            if manager_uapi != kernel_uapi:
                reasons.append("uapi_contract_mismatch")
        result["not_ready_reasons"] = reasons
        entries[variant] = result
    return {
        "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "read-only-source-and-uapi-audit",
        "audit_complete": not retrieval_errors,
        "retrieval_errors": retrieval_errors,
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
                  f"manager UAPI={result['manager_uapi']}, baseline UAPI={result['kernel_uapi']}; "
                  f"UAPI status={result['uapi_status']}; not release-ready")
        if not report["audit_complete"]:
            for error in report["retrieval_errors"]:
                print(f"ERROR incomplete source audit: {error}", file=sys.stderr)
            return 1
    except (CiSourceError, RuntimeError, OSError, ValueError, KeyError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
