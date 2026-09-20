"""Advance VERSION by 0.001 and stamp when it was deployed.

⛔ THE VERSION FILE IS COMMITTED, ON PURPOSE. It is the one piece of build
state that must survive the image: `app/build_info.json` is gitignored, so if
the number lived only there it would reset to 0.001 on any clean checkout and
two different deploys would claim the same version. A version that repeats is
worse than no version — it makes "is that fix live?" unanswerable.

⚠ FIXED-POINT, NOT FLOAT. 0.001 steps accumulate binary error (0.1+0.2 is the
famous one); at v0.029 a float would start printing 0.029000000000000005. The
number is stored and incremented as an INTEGER of thousandths and only
formatted for display.

    python scripts/bump_version.py           # bump, print the new version
    python scripts/bump_version.py --read    # print current, change nothing
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(ROOT, "VERSION")
STEP = 1  # thousandths


def read_thousandths() -> int:
    try:
        with open(VERSION_FILE) as f:
            return round(float(f.read().strip()) * 1000)
    except Exception:  # noqa: BLE001
        return 0


def fmt(thousandths: int) -> str:
    return f"{thousandths // 1000}.{thousandths % 1000:03d}"


def main() -> int:
    cur = read_thousandths()
    if "--read" in sys.argv:
        print(fmt(cur))
        return 0
    nxt = cur + STEP
    with open(VERSION_FILE, "w") as f:
        f.write(fmt(nxt) + "\n")
    print(f"V{fmt(cur)} -> V{fmt(nxt)}")
    return 0


def deploy_stamp() -> dict:
    """What the footer shows. Called by gen_build_info.py."""
    def git(*a):
        try:
            return subprocess.check_output(["git", *a], text=True,
                                           stderr=subprocess.DEVNULL).strip()
        except Exception:  # noqa: BLE001
            return None
    return {
        "version": fmt(read_thousandths()),
        # ⛔ STORED IN UTC, RENDERED IN PACIFIC. The container clock is UTC and
        # Pedram is in Los Angeles; a bare timestamp he has to subtract 7 hours
        # from is not a timestamp. core/version.py does the conversion.
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "commit": (git("rev-parse", "HEAD") or "")[:8] or None,
    }


if __name__ == "__main__":
    sys.exit(main())
