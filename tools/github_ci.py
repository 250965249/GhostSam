#!/usr/bin/env python3
"""Resolve a manager artifact from one trusted GitHub Actions run.

"Latest" means the newest completed successful run on an explicitly selected
branch/workflow in the upstream repository, not a PR, nightly URL, or artifact
from a different run. Callers may instead require an exact source revision to
match a separately built component. These checks establish provenance, not
runtime compatibility.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import quote


class CiSourceError(RuntimeError):
    pass


def full_revision(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise CiSourceError("a full lowercase Git commit SHA is required")
    return value


def workflow_path(value: str) -> str:
    return value if value.startswith(".github/workflows/") else f".github/workflows/{value}"


def parse_time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise CiSourceError(f"invalid GitHub run timestamp: {value!r}") from exc
    if result.tzinfo is None:
        raise CiSourceError("GitHub run timestamp must include a timezone")
    return result


def eligible_run(run: dict, repository: str, branch: str, workflow: str) -> bool:
    return (
        run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("event") in ("push", "workflow_dispatch")
        and run.get("head_branch") == branch
        and (run.get("head_repository") or {}).get("full_name", "").lower() == repository.lower()
        and run.get("path", "").split("@")[0] == workflow_path(workflow)
        # GitHub also lists PRs in downstream forks that reference this main
        # branch. Event + head repository identify a PR build; this association
        # list alone does not, and must not reject a genuine upstream push.
        and isinstance(run.get("id"), int)
        and not isinstance(run.get("id"), bool)
        and run["id"] > 0
        and bool(re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", "")))
    )


def select_run(
    runs: list[dict], repository: str, branch: str, workflow: str,
    *, expected_revision: str | None = None,
) -> dict:
    if expected_revision is not None:
        full_revision(expected_revision)
    candidates = [
        run for run in runs
        if eligible_run(run, repository, branch, workflow)
        and (expected_revision is None or run["head_sha"] == expected_revision)
    ]
    if not candidates:
        mode = f"at {expected_revision}" if expected_revision else ""
        raise CiSourceError(f"no trusted successful {workflow} run for {repository}@{branch} {mode}".strip())
    return max(candidates, key=lambda run: (parse_time(run.get("created_at")), run.get("run_attempt", 1), run["id"]))


def select_artifact(artifacts: list[dict], name: str, run: dict) -> dict:
    candidates = [a for a in artifacts if a.get("name") == name]
    if len(candidates) != 1:
        raise CiSourceError(f"run {run['id']}: expected exactly one artifact named {name!r}")
    artifact = candidates[0]
    if artifact.get("expired") is not False:
        raise CiSourceError(f"run {run['id']}: {name} expired or expiry state unavailable")
    if not isinstance(artifact.get("id"), int) or artifact["id"] <= 0:
        raise CiSourceError(f"run {run['id']}: artifact id unavailable")
    if not isinstance(artifact.get("size_in_bytes"), int) or artifact["size_in_bytes"] <= 0:
        raise CiSourceError(f"run {run['id']}: artifact is empty")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", "")):
        raise CiSourceError(f"run {run['id']}: artifact archive checksum unavailable")
    identity = artifact.get("workflow_run") or {}
    if identity.get("id") != run["id"] or identity.get("head_sha") != run["head_sha"]:
        raise CiSourceError(f"run {run['id']}: artifact belongs to a different build revision")
    return artifact


def resolve_ci(
    api, repository: str, branch: str, workflow: str, artifact_name: str,
    *, expected_revision: str | None = None, max_pages: int = 5,
) -> tuple[dict, dict]:
    """Use a JSON API callable, stopping at the newest eligible build.

    Do not walk backwards when its artifact is missing/expired: publishing an
    older artifact as "latest" would hide a failed or incomplete release.
    """
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    runs: list[dict] = []
    encoded = quote(workflow.rsplit("/", 1)[-1], safe="")
    for page in range(1, max_pages + 1):
        data = api(
            f"repos/{repository}/actions/workflows/{encoded}/runs"
            # GitHub's workflow-runs endpoint has two quirks with a branch that
            # contains a slash (ghostsam/adapter): a %2F-encoded branch matches
            # nothing, and `branch=<slashed>&status=success` also returns zero
            # rows (the branch alone returns twelve). List without the status
            # filter - eligible_run() re-checks status/conclusion client-side -
            # and keep the branch slash literal, escaping everything else.
            f"?branch={quote(branch, safe='/')}&per_page=100&page={page}"
        )
        batch = data.get("workflow_runs") or []
        runs.extend(batch)
        try:
            selected = select_run(runs, repository, branch, workflow, expected_revision=expected_revision)
        except CiSourceError:
            if len(batch) < 100:
                raise
        else:
            break
    else:
        raise CiSourceError(f"no matching run within {max_pages} pages; older history was not inspected")

    artifacts: list[dict] = []
    page = 1
    while True:
        data = api(f"repos/{repository}/actions/runs/{selected['id']}/artifacts?per_page=100&page={page}")
        batch = data.get("artifacts") or []
        artifacts.extend(batch)
        if len(batch) < 100:
            break
        page += 1
        if page > max_pages:
            raise CiSourceError("artifact list was truncated; refusing an incomplete identity check")
    return selected, select_artifact(artifacts, artifact_name, selected)


def provenance(repository: str, workflow: str, run: dict, artifact: dict) -> dict:
    return {
        "repository": repository,
        "workflow": workflow_path(workflow),
        "branch": run["head_branch"],
        "commit": run["head_sha"],
        "run_id": run["id"],
        "run_attempt": run.get("run_attempt", 1),
        "run_url": run.get("html_url"),
        "built_at": run.get("updated_at"),
        "artifact_id": artifact["id"],
        "artifact_name": artifact["name"],
        "archive_digest": artifact.get("digest"),
        "runtime_compatibility": "not-tested",
    }
