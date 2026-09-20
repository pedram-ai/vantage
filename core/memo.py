"""A tiny TTL memo for DERIVED results.

⚠ This is not another data cache. The bars and articles are already in memory;
what costs time now is the arithmetic over them — a session profile per day, a
horizon tally across 237 articles. Those inputs change on a schedule we
control, so the OUTPUT can be memoised and refreshed off the request path.

⛔ EVERY ENTRY HAS A TTL AND AN EXPLICIT BUSTER. A derived figure that outlives
its input is the wrong-number-on-a-screen failure this codebase keeps guarding
against, so writers call `bust()` and the TTL is only a backstop.
"""

from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_C: dict[str, tuple[float, object]] = {}


def get(key: str, ttl: float, build):
    """Memoised `build()`. Returns a shared object — treat it as read-only."""
    now = time.monotonic()
    with _LOCK:
        hit = _C.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = build()
    with _LOCK:
        _C[key] = (now, value)
    return value


def bust(*prefixes: str) -> None:
    with _LOCK:
        if not prefixes:
            _C.clear()
            return
        for k in [k for k in _C if k.startswith(prefixes)]:
            _C.pop(k, None)


def state() -> dict:
    now = time.monotonic()
    with _LOCK:
        return {k: round(now - v[0], 1) for k, v in _C.items()}
