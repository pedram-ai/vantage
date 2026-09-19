"""One place that answers "what is ES trading at, and how much should I
trust that number?"

Schwab first (real-time, brokerage entitlement), Yahoo as the fallback
(10-minute delayed per Yahoo's own exchange table). The answer ALWAYS
carries its source and its age, because the two are not interchangeable:
a delayed print can be several points off, which is enough to flip a
verdict near a band edge.

⛔ Never return a bare price from here. Callers render `source_label` and
`age_text` next to it. A number whose provenance is invisible is the exact
failure this module exists to prevent.
"""

from __future__ import annotations

import time
from datetime import datetime, time as dtime

from .profile import ET, PT

YAHOO_DELAY_MIN = 10  # Yahoo lists CME (.CME) as 10-min delayed, via ICE Data Services


def es_market_open(now_et: datetime | None = None) -> bool:
    """ES/Globex: Sun 18:00 ET -> Fri 17:00 ET, with a daily 17:00-18:00 halt."""
    n = now_et or datetime.now(ET)
    wd, t = n.weekday(), n.time()  # Mon=0 .. Sun=6
    if wd == 5:                                   # Saturday
        return False
    if wd == 6:                                   # Sunday: opens 18:00
        return t >= dtime(18, 0)
    if wd == 4 and t >= dtime(17, 0):             # Friday: closed after 17:00
        return False
    return not (dtime(17, 0) <= t < dtime(18, 0))  # daily maintenance halt


def _age_text(as_of: float | None, market_open: bool) -> str:
    if not as_of:
        return "time unknown"
    secs = max(time.time() - as_of, 0)
    stamp = datetime.fromtimestamp(as_of, PT).strftime("%-I:%M:%S %p PT")
    if not market_open:
        return f"{stamp} · last print before the close"
    if secs < 90:
        return f"{stamp} · {int(secs)}s ago"
    mins = secs / 60
    if mins < 90:
        return f"{stamp} · {mins:.0f} min ago"
    return f"{stamp} · {mins/60:.1f} h ago"


def es_quote(contract: str, yahoo_meta: dict | None = None,
             yahoo_fallback_price: float | None = None) -> dict:
    """Best available ES quote with full provenance.

    yahoo_meta: the `meta` dict already fetched for the bars, so the common
    path costs no extra HTTP call.
    """
    open_now = es_market_open()

    # 1. Schwab — real-time, if the user has linked it.
    try:
        from . import schwab
        q = schwab.quote()
    except Exception:  # noqa: BLE001 - never let this path break the map
        q = None
    if q and q.get("price"):
        return {
            "price": float(q["price"]),
            "source": "schwab",
            "source_label": "Schwab · real-time",
            "delayed_min": 0,
            "as_of": q.get("as_of"),
            "age_text": _age_text(q.get("as_of"), open_now),
            "market_open": open_now,
            "trustworthy": True,
            "symbol": q.get("symbol") or contract,
        }

    # 2. Yahoo — delayed, but always there.
    price = None
    as_of = None
    if yahoo_meta:
        price = yahoo_meta.get("regularMarketPrice")
        as_of = yahoo_meta.get("regularMarketTime")
    if price is None:
        price = yahoo_fallback_price
    return {
        "price": float(price) if price is not None else None,
        "source": "yahoo",
        "source_label": f"Yahoo · {YAHOO_DELAY_MIN}-min delayed",
        "delayed_min": YAHOO_DELAY_MIN,
        "as_of": as_of,
        "age_text": _age_text(as_of, open_now),
        "market_open": open_now,
        # A delayed price during live trading is the case to warn about.
        "trustworthy": not open_now,
        "symbol": contract,
    }
