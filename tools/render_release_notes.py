#!/usr/bin/env python3
"""Render the release notes for one GhostSam auto build.

The notes are the "本次版本更新描述" attached to every release: which ksud
payload each variant ships, how each one compares to its upstream CI build, what
changed since the previous auto release, and which root managers are bundled.

Usage:
    python3 tools/render_release_notes.py \
        --lock out/payloads.lock.json \
        [--previous prev/payloads.lock.json] \
        --apk out/GhostSam_auto_YYYYMMDD.apk \
        --managers managers \
        --tag auto-YYYYMMDD-HHMM \
        --out /tmp/release-notes.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ORDER = ["sukisu", "bakasu", "next", "default"]
LABEL = {
    "sukisu": "SukiSU-Ultra",
    "bakasu": "BakaSU",
    "next": "KernelSU-Next",
    "default": "KernelSU (official)",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sync_cell(entry: dict) -> str:
    behind = entry.get("behind_upstream")
    if behind is None:
        return "n/a"
    if behind == 0:
        return "已同步"
    return f"落后 {behind} 个提交"


def variant_rows(lock: dict) -> list[str]:
    rows = ["| 变体 | 版本 | 载荷版本号 | 相对上游 CI |", "| --- | --- | --- | --- |"]
    variants = lock.get("variants", {})
    for name in ORDER + [n for n in variants if n not in ORDER]:
        entry = variants.get(name)
        if not entry:
            continue
        label = LABEL.get(name, name)
        version = entry.get("version_name") or "unknown"
        pinned = " (版本钉死)" if entry.get("version_pinned") else ""
        rows.append(
            f"| {label} | `{version}`{pinned} | {entry.get('version_code')} "
            f"| {sync_cell(entry)} |"
        )
    return rows


def changes_section(lock: dict, previous: dict | None) -> list[str]:
    if not previous:
        return ["首次记录版本状态，暂无历史对比。"]
    cur = lock.get("variants", {})
    old = previous.get("variants", {})
    lines = ["| 变体 | 变化 |", "| --- | --- |"]
    changed = 0
    for name in ORDER + [n for n in cur if n not in ORDER]:
        if name not in cur:
            continue
        c = cur[name]
        o = old.get(name, {})
        c_code, o_code = c.get("version_code"), o.get("version_code")
        c_sha, o_sha = (c.get("sha256") or "")[:12], (o.get("sha256") or "")[:12]
        if o_code is None:
            lines.append(f"| {LABEL.get(name, name)} | 新增 |")
            changed += 1
            continue
        if c_sha == o_sha:
            continue
        delta = ""
        if isinstance(c_code, int) and isinstance(o_code, int) and c_code != o_code:
            delta = f" {c_code - o_code:+d}"
        lines.append(
            f"| {LABEL.get(name, name)} | `{o.get('version_name')}` ({o_code}) → "
            f"`{c.get('version_name')}` ({c_code}) {delta} |"
        )
        changed += 1
    if changed == 0:
        return ["本次没有载荷变化（上游未推送新提交）。"]
    return lines


def managers_section(managers_dir: Path) -> list[str]:
    if not managers_dir.is_dir():
        return ["本次没有附上 root 管理器（下载失败，见运行日志）。"]

    info: list[dict] = []
    report = managers_dir / "managers.json"
    if report.is_file():
        try:
            info = json.loads(report.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            info = []

    if info:
        lines = [
            "| 管理器 | 来源 | 版本 | 文件 | 大小 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for entry in info:
            size_mb = entry.get("size", 0) / (1024 * 1024)
            lines.append(
                f"| {entry.get('display')} | `{entry.get('repo')}` "
                f"| `{entry.get('tag')}` | `{entry.get('file')}` | {size_mb:.1f} MB |"
            )
        lines.append("")
        lines.append(
            "管理器直接取自上游 release，与上面的 ksud 版本一一对应，签名用的是各项目自己的证书。"
        )
        fallbacks = [e for e in info if e.get("match") not in (None, "exact")]
        if fallbacks:
            lines.append("")
            for entry in fallbacks:
                lines.append(
                    f"- ⚠️ {entry.get('display')}：没找到与 ksud "
                    f"`{entry.get('ksud_version')}` 完全对应的 release，退回最新的 "
                    f"`{entry.get('tag')}`"
                )
        return lines

    apks = sorted(p for p in managers_dir.rglob("*.apk"))
    if not apks:
        return ["本次没有附上 root 管理器（下载失败，见运行日志）。"]
    lines = ["| 管理器 | 文件 | sha256 |", "| --- | --- | --- |"]
    for apk in apks:
        lines.append(f"| {apk.stem} | `{apk.name}` | `{sha256(apk)[:32]}…` |")
    return lines


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lock", type=Path, required=True)
    ap.add_argument("--previous", type=Path, default=None)
    ap.add_argument("--apk", type=Path, default=None)
    ap.add_argument("--managers", type=Path, default=None)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    previous = None
    if args.previous and args.previous.is_file():
        try:
            previous = json.loads(args.previous.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = None

    generated = lock.get("generated_at", "unknown")
    out: list[str] = []
    out.append(f"GhostSam 自动构建 `{args.tag}`，载荷清单生成于 {generated}。")
    out.append("")
    out.append("## 本次载荷")
    out.append("")
    out.extend(variant_rows(lock))
    out.append("")
    out.append("## 相比上一次发布")
    out.append("")
    out.extend(changes_section(lock, previous))
    out.append("")
    out.append("## Root 管理器")
    out.append("")
    out.extend(managers_section(args.managers) if args.managers else ["未附带。"])
    out.append("")
    if args.apk and args.apk.is_file():
        out.append(f"GhostSam APK：`{args.apk.name}` ({args.apk.stat().st_size} 字节)")
        out.append(f"sha256 `{sha256(args.apk)}`")
        out.append("")
    problems = lock.get("problems") or []
    warnings = lock.get("warnings") or []
    if problems or warnings:
        out.append("## 注意")
        out.append("")
        for p in problems:
            out.append(f"- ⚠️ {p}")
        for w in warnings:
            out.append(f"- {w}")
        out.append("")

    args.out.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
