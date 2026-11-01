#!/usr/bin/env python3
"""Verify the two shell pipelines the producer release job relies on.

The release job rejects a build whose binary disagrees with HEAD:

    embedded_code=$(grep -aoE 'KSU_NO_NEW_PRIVS[0-9]{5}' "$bin" | head -n1 \\
                    | sed 's/KSU_NO_NEW_PRIVS//')
    embedded_name=$(grep -aoE 'KSU_NO_NEW_PRIVS[0-9]{5}[0-9A-Za-z._:+~-]+' "$bin" \\
                    | head -n1 | sed -E 's/^KSU_NO_NEW_PRIVS[0-9]{5}//')

This script reproduces them with the same patterns and checks the output against
the values tools/probe_payload_version.py recovers, so a typo in the workflow
cannot silently disable the guard.

Usage: python3 tools/verify_embedded_version.py <payload-dir>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_payload_version import probe  # noqa: E402

#: exactly what `grep -aoE` sees, in order.
CODE_GREP = re.compile(rb"KSU_NO_NEW_PRIVS[0-9]{5}")
NAME_GREP = re.compile(rb"KSU_NO_NEW_PRIVS[0-9]{5}[0-9A-Za-z._:+~-]+")

VARIANTS = {
    "ksud": "default",
    "ksud-next": "next",
    "ksud-sukisu": "sukisu",
    "ksud-bakasu": "bakasu",
}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    payloads = Path(argv[0])

    failed = 0
    for filename, variant in VARIANTS.items():
        path = payloads / filename
        if not path.is_file():
            print(f"SKIP {filename}: not found")
            continue
        blob = path.read_bytes()

        codes = CODE_GREP.findall(blob)
        names = NAME_GREP.findall(blob)
        shell_code = codes[0].replace(b"KSU_NO_NEW_PRIVS", b"").decode() if codes else ""
        shell_name = ""
        if names:
            shell_name = re.sub(rb"^KSU_NO_NEW_PRIVS[0-9]{5}", b"", names[0]).decode()

        info = probe(path, variant)
        ok = (
            shell_code == str(info["version_code"])
            and shell_name == info["version_name"]
        )
        print(
            f"{'OK  ' if ok else 'FAIL'} {filename:12} "
            f"code={shell_code!r:10} name={shell_name!r:34} "
            f"probe=({info['version_code']!r}, {info['version_name']!r})"
        )
        if not ok:
            failed += 1

    print()
    print("shell pipelines agree with the probe" if not failed else f"{failed} mismatch(es)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
