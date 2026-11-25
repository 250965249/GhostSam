#!/usr/bin/env python3
"""Assert that the maplesu variant is wired end to end in the GhostSam app.

Adding a value to `enum class KsudVariant` makes every exhaustive `when (variant)` a
compile error until it is handled, and the settings dropdown / asset path / manager pin
live in five different files. This tool reads those files and fails if any half of the
wiring is missing, so "the variant is selectable and routes to the right payload" is a
checkable claim instead of a code review impression.

Static checks (always):
  1. KsudVariant: MapleSU entry exists and its label branch returns "MapleSU"
  2. KsudAssets: MapleSU -> "payloads/ksud/ksud-maplesu"
  3. PayloadVersions: a MapleSU fallback entry exists
  4. KernelSuProbe: package dev.maplesu.manager, cert pin 9c8e10bd..., DER 846,
     and a MapleSU branch in each of the five variant switches
  5. ExploitViewModel: MapleSU -> R.string.settings_ksud_variant_maplesu
  6. SettingsScreen: the dropdown lists MapleSU and its order matches the enum
  7. strings (en + zh-rCN): settings_ksud_variant_maplesu is defined
  8. AndroidManifest: <queries> can see dev.maplesu.manager

APK checks (--apk PATH):
  9. assets/payloads/ksud/ksud-maplesu exists, and its size/sha256 match --payload
 10. the packaged versions.properties names the maplesu slot

Usage:
  python3 tools/verify_variant_routing.py [--root .] [--apk app/app/build/outputs/apk/debug/app-debug.apk]
                                          [--payload app/app/src/main/assets/payloads/ksud/ksud-maplesu]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path

CERT = "9c8e10bd53a284c8186c827ed02651d1507819345b3948334795121f432e0d7d"
PACKAGE = "dev.maplesu.manager"
PAYLOAD_ASSET = "payloads/ksud/ksud-maplesu"

F = {
    "enum": "app/app/src/main/java/com/snothin/ghostsam/data/prefs/KsudVariant.kt",
    "assets": "app/app/src/main/java/com/snothin/ghostsam/data/exploit/KsudAssets.kt",
    "versions": "app/app/src/main/java/com/snothin/ghostsam/data/prefs/PayloadVersions.kt",
    "probe": "app/app/src/main/java/com/snothin/ghostsam/data/device/KernelSuProbe.kt",
    "viewmodel": "app/app/src/main/java/com/snothin/ghostsam/data/exploit/ExploitViewModel.kt",
    "settings": "app/app/src/main/java/com/snothin/ghostsam/ui/screen/SettingsScreen.kt",
    "strings_en": "app/app/src/main/res/values/strings.xml",
    "strings_zh": "app/app/src/main/res/values-zh-rCN/strings.xml",
    "manifest": "app/app/src/main/AndroidManifest.xml",
}


class Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def add(self, label: str, ok: bool, detail: str = "") -> None:
        self.rows.append((label, ok, detail))

    def report(self) -> int:
        width = max(len(label) for label, _, _ in self.rows)
        for label, ok, detail in self.rows:
            print(f"[{'PASS' if ok else 'FAIL'}] {label:{width}}  {detail}")
        bad = [r for r in self.rows if not r[1]]
        print(f"\n{len(self.rows) - len(bad)}/{len(self.rows)} checks passed")
        return 1 if bad else 0


def enum_entries(text: str) -> list[str]:
    body = re.search(r"enum class KsudVariant \{(.*?)\n\}", text, re.S)
    if not body:
        return []
    head = body.group(1).split(";")[0]
    return [name.strip() for name in re.findall(r"^\s{4}([A-Za-z]+),?$", head, re.M)]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--apk", type=Path, default=None)
    ap.add_argument("--payload", type=Path, default=None)
    args = ap.parse_args(argv)

    texts: dict[str, str] = {}
    missing = []
    for key, rel in F.items():
        path = args.root / rel
        if not path.is_file():
            missing.append(rel)
            texts[key] = ""
        else:
            texts[key] = path.read_text(encoding="utf-8", errors="replace")
    if missing:
        print("missing source files:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 2

    checks = Checks()

    entries = enum_entries(texts["enum"])
    checks.add("enum has MapleSU", "MapleSU" in entries, f"entries={entries}")
    checks.add(
        "enum label returns MapleSU",
        bool(re.search(r'MapleSU\s*->\s*"MapleSU"', texts["enum"])),
        "KsudVariant.MapleSU -> \"MapleSU\"",
    )
    checks.add(
        "asset path routes to ksud-maplesu",
        'KsudVariant.MapleSU -> "payloads/ksud/ksud-maplesu"' in texts["assets"],
        f'expected {PAYLOAD_ASSET}',
    )
    checks.add(
        "payload version fallback present",
        "KsudVariant.MapleSU to" in texts["versions"],
        "PayloadVersions.fallback[MapleSU]",
    )

    probe = texts["probe"]
    checks.add("probe knows the manager package", f'PACKAGE_MAPLESU = "{PACKAGE}"' in probe, PACKAGE)
    checks.add("probe pins the identity cert", CERT in probe, CERT[:16] + "...")
    checks.add("probe records DER size 846", re.search(r"MAPLESU_MANAGER_CERT_DER_BYTES\s*=\s*846", probe) is not None, "846")
    switch_sites = len(re.findall(r"KsudVariant\.MapleSU\s*->", probe))
    checks.add("probe has a branch in every variant switch", switch_sites >= 5, f"{switch_sites} branches (need >=5)")

    checks.add(
        "log line resource wired",
        "KsudVariant.MapleSU -> R.string.settings_ksud_variant_maplesu" in texts["viewmodel"],
        "ExploitViewModel.ksudVariantLabel",
    )
    checks.add(
        "settings dropdown lists the payload",
        "PayloadVersions.label(context, KsudVariant.MapleSU)" in texts["settings"],
        "SettingsScreen items",
    )
    dropdown = re.findall(r"PayloadVersions\.label\(context, KsudVariant\.(\w+)\)", texts["settings"])
    checks.add(
        "dropdown order matches enum order",
        dropdown == entries,
        f"dropdown={dropdown} enum={entries}",
    )
    for locale in ("strings_en", "strings_zh"):
        checks.add(
            f"{locale} defines settings_ksud_variant_maplesu",
            'name="settings_ksud_variant_maplesu"' in texts[locale],
            "resource present",
        )
    checks.add(
        "manifest can see the manager package",
        f'<package android:name="{PACKAGE}" />' in texts["manifest"],
        "AndroidManifest <queries>",
    )

    if args.apk:
        apk = args.apk
        payload = args.payload or (args.root / "app/app/src/main/assets/payloads/ksud/ksud-maplesu")
        try:
            with zipfile.ZipFile(apk) as zf:
                names = zf.namelist()
                entry = f"assets/{PAYLOAD_ASSET}"
                checks.add("APK bundles ksud-maplesu", entry in names, entry)
                if entry in names:
                    data = zf.read(entry)
                    want = payload.read_bytes() if payload.is_file() else b""
                    checks.add(
                        "APK payload bytes equal the fed payload",
                        bool(want) and data == want,
                        f"{len(data)} bytes sha256={hashlib.sha256(data).hexdigest()[:16]}",
                    )
                versions = "assets/payloads/ksud/versions.properties"
                props = zf.read(versions).decode("utf-8", "replace") if versions in names else ""
                checks.add("packaged manifest names the maplesu slot", "maplesu=" in props,
                           next((l for l in props.splitlines() if l.startswith("maplesu=")), ""))
        except (OSError, zipfile.BadZipFile) as exc:
            checks.add("APK readable", False, str(exc))

    return checks.report()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
