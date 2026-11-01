#!/usr/bin/env python3
"""Turn payloads.lock.json into a build-log table, a markdown table for the
release notes, GitHub annotations, and the `stale` step output.

Kept separate from gen_payload_versions.py so the generator stays a pure
"describe the payloads" tool while the CI surfacing lives here.

Usage:
    python3 tools/report_payloads.py --lock <payloads.lock.json>
                                     [--table <out.md>]
                                     [--github-output <path>]
                                     [--strict]

Exit status is 1 when the lock reports problems and --strict is passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def behind_text(value: int | None) -> str:
    if value is None:
        return "unknown"
    return "up to date" if value == 0 else f"{value} behind"


def render_console(lock: dict) -> str:
    rows = [f"{'variant':8} {'code':>6}  {'sync':>12}  {'built_at':20}  label"]
    for name, e in lock["variants"].items():
        rows.append(
            f"{name:8} {e['version_code']!s:>6}  {behind_text(e['behind_upstream']):>12}  "
            f"{e['built_at'] or '-':20}  {e['label']}"
        )
    return "\n".join(rows)


def render_markdown(lock: dict) -> str:
    lines = [
        "| variant | version code | label | vs upstream CI | bundled payload sha256 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, e in lock["variants"].items():
        lines.append(
            f"| {name} | {e['version_code']} | {e['label']} | "
            f"{behind_text(e['behind_upstream'])} | `{(e['sha256'] or '')[:16]}…` |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lock", type=Path, required=True)
    ap.add_argument("--table", type=Path, help="write the markdown table here")
    ap.add_argument("--github-output", type=Path, help="append `stale=...` here")
    ap.add_argument("--strict", action="store_true", help="exit 1 when problems exist")
    args = ap.parse_args(argv)

    lock = json.loads(args.lock.read_text(encoding="utf-8"))

    print(render_console(lock))
    print()
    for problem in lock["problems"]:
        print(f"::error::{problem}")
    for warning in lock["warnings"]:
        print(f"::warning::{warning}")

    if args.table:
        args.table.write_text(render_markdown(lock), encoding="utf-8")

    stale = bool(lock["problems"])
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"stale={'true' if stale else 'false'}\n")

    if args.strict and stale:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
