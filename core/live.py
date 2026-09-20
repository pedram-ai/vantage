"""The price snapshot — every quote on every page, from memory.

⛔⛔ NO PAGE MAY BLOCK ON A QUOTE. The header price strip rendered on EVERY
template and called Yahoo synchronously; Markets and Today did it again for
each watchlist symbol. That is one to twelve HTTP round trips standing between
a click and a paint, on pages that are otherwise pure memory.

The snapshot is refreshed by a background thread and read instantly. A read
NEVER touches the network — not on a miss, not on a stale entry, not ever.

⭐ AND THERE IS ALWAYS AN ANSWER, because the bar archive is a price source.
A symbol with no live quote yet falls back to the last archived bar, labelled
as such. The alternative — a blank where a price should be — reads as broken.

⚠ Freshness is REPORTED, never implied. Every entry carries where it came from
and how old it is, so a five-minute-old close is visibly a five-minute-old
close rather than passing for a live tick.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

_LOCK = threading.Lock()
_SNAP: dict[str, dict] = {}
_LAST_REFRESH: float | None = None


def _from_archive(symbol: str) -> dict | None:
    """Last archived bar as a price. Always available once seeded."""
    from . import barstore
    for iv in ("1m", "5m", "15m", "1h", "1d"):
        bars = barstore.read_store(symbol, iv)
        if bars:
            b = bars[-1]
            prev = bars[-2].close if len(bars) > 1 else None
            return {
                "symbol": symbol, "price": round(b.close, 2),
                "change_pct": (round((b.close - prev) / prev * 100, 2)
                               if prev else None),
                "at": b.ts.isoformat(), "source": f"archive {iv}",
                "live": False,
            }
    return None


def get(symbol: str) -> dict | None:
    """One quote, from memory. Never raises, never fetches."""
    with _LOCK:
        hit = _SNAP.get(symbol)
    if hit:
        return dict(hit)
    a = _from_archive(symbol)
    if a:
        with _LOCK:
            _SNAP[symbol] = a
    return a


def many(symbols: list[str]) -> dict[str, dict]:
    return {s: q for s in symbols if (q := get(s)) is not None}


def age_seconds() -> float | None:
    return None if _LAST_REFRESH is None else round(time.monotonic() - _LAST_REFRESH, 1)


def refresh(symbols: list[str]) -> dict:
    """Pull live quotes into the snapshot. ⚠ BACKGROUND ONLY.

    Called by the sync loop, never by a request handler. If it fails the
    snapshot simply keeps its previous values, which is why every page still
    renders during a Yahoo outage.
    """
    global _LAST_REFRESH
    got, failed = 0, 0
    try:
        from .quotes_batch import quotes_for
        batch = quotes_for(symbols, fresh=True)
        quotes = batch.get("quotes") or {}
    except Exception:  # noqa: BLE001
        quotes = {}

    now = datetime.now(timezone.utc).isoformat()
    for s in symbols:
        q = quotes.get(s) or {}
        price = q.get("price")
        if price is None:
            failed += 1
            # keep whatever we had; fall back to the archive if we had nothing
            with _LOCK:
                if s not in _SNAP:
                    a = _from_archive(s)
                    if a:
                        _SNAP[s] = a
            continue
        with _LOCK:
            _SNAP[s] = {
                "symbol": s, "price": round(float(price), 2),
                "change_pct": q.get("change_pct"),
                "at": now, "source": q.get("source") or "yahoo", "live": True,
            }
        got += 1
    _LAST_REFRESH = time.monotonic()
    return {"got": got, "failed": failed, "symbols": len(symbols)}


def state() -> dict:
    """For System Health. Never raises."""
    with _LOCK:
        n = len(_SNAP)
        live = sum(1 for v in _SNAP.values() if v.get("live"))
    return {"symbols": n, "live": live, "from_archive": n - live,
            "age_seconds": age_seconds()}
