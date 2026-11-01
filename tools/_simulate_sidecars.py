"""Build a simulated payload tree with producer sidecars, to exercise the
label formatter without waiting for a real producer run.

Usage: python3 tools/_simulate_sidecars.py <payload-dir> <out-dir>
"""

import datetime
import hashlib
import shutil
import sys
from pathlib import Path

SIDECARS = {
    "sukisu": {
        "version_code": "41106",
        "version_name": "4.3.0-44-g4f74a450",
        "behind_upstream": "0",
        "upstream_commit": "0fe8d052",
        "upstream_version_name": "4.3.0-20-g0fe8d052",
    },
    "bakasu": {
        "version_code": "35260",
        "version_name": "4.2.0-4-gb8f9d24f",
        "behind_upstream": "0",
        "upstream_commit": "deadbeef",
        "upstream_version_name": "4.2.0",
    },
}


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    src = Path(argv[0])
    out = Path(argv[1])
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for name in ("ksud", "ksud-next", "ksud-sukisu", "ksud-bakasu"):
        shutil.copy(src / name, out / name)

    for variant, kv in SIDECARS.items():
        target = out / f"ksud-{variant}"
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        body = [f"variant={variant}"]
        body += [f"{k}={v}" for k, v in kv.items()]
        body += ["commit=0123456789abcdef", f"built_at={now}", f"sha256={digest}"]
        (out / f"ksud-{variant}.version").write_text("\n".join(body) + "\n", encoding="utf-8")

    print(f"simulated sidecars in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
