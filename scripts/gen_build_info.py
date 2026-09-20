"""Bake the git history into the image at build time.

Method from Lateral Compass: the changelog lives in the REPO, not a database,
so it cannot drift from the code that shipped. LC keeps a hand-written
`docs/PRD.md`; here the per-commit log is generated from git itself, so
"every commit is logged" is structurally true rather than a discipline.

Run by the Dockerfile before the image is sealed. If git is unavailable the
file still gets written, with `available: false` — the admin page then says so
instead of rendering an empty history that looks like "no changes".

    python scripts/gen_build_info.py [out.json]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

OUT = sys.argv[1] if len(sys.argv) > 1 else "app/build_info.json"
SEP = "\x1f"


def git(*args: str) -> str | None:
    try:
        return subprocess.check_output(["git", *args], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    head = git("rev-parse", "HEAD")
    # The version + deploy stamp the footer renders. bump_version owns the
    # number; this file only carries it into the image.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from bump_version import deploy_stamp
        stamp = deploy_stamp()
    except Exception:  # noqa: BLE001
        stamp = {}
    info = {
        "available": bool(head),
        "commit": head or None,
        "short": (head or "")[:8] or None,
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "version": stamp.get("version"),
        "deployed_at": stamp.get("deployed_at"),
        "commits": [],
    }

    raw = git("log", "-n", "300", f"--pretty=format:%H{SEP}%h{SEP}%aI{SEP}%an{SEP}%s")
    if raw:
        for line in raw.split("\n"):
            parts = line.split(SEP)
            if len(parts) != 5:
                continue
            full, short, when, who, subject = parts
            stat = git("show", "--stat", "--format=", "--shortstat", full) or ""
            info["commits"].append({
                "sha": full, "short": short, "date": when,
                "author": who, "subject": subject,
                "stat": stat.strip().split("\n")[-1].strip() if stat.strip() else "",
            })

    with open(OUT, "w") as fh:
        json.dump(info, fh, indent=1)
    print(f"wrote {OUT}: {len(info['commits'])} commits, head {info['short']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
