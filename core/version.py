"""What the footer shows: which build is live, and when it went live.

Read ONCE at import from `app/build_info.json` — the file is baked into the
image, so it cannot change while the process runs and re-reading it per request
would be pure I/O.

⚠ FAIL-SOFT, AND SAYS SO. If the build info is missing (someone ran uvicorn
straight from a checkout without `scripts/deploy.sh`), the footer reads
"dev build" rather than inventing a version. A wrong version number is worse
than an absent one: it is the thing you check to answer "did my fix ship?".
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from .profile import PT

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "app", "build_info.json")


def _load() -> dict:
    try:
        with open(_PATH) as f:
            return json.load(f) or {}
    except Exception:  # noqa: BLE001
        return {}


_INFO = _load()


def _pacific(iso: str | None) -> str | None:
    """UTC ISO string → 'Sep 20, 2026 · 3:14 PM PT'."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(PT).strftime("%b %-d, %Y · %-I:%M %p PT")
    except Exception:  # noqa: BLE001
        return None


def footer() -> dict:
    """Everything the footer needs. Never raises."""
    v = _INFO.get("version")
    when = _pacific(_INFO.get("deployed_at") or _INFO.get("built_at"))
    return {
        "version": f"V{v}" if v else None,
        "deployed": when,
        "commit": (_INFO.get("short") or _INFO.get("commit") or "")[:8] or None,
        "dev": not v,
    }
