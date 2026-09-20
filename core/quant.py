"""The quant layer — levels computed from volume, not from opinion.

This is the third input alongside news and X, and it is the only one that
cannot be wrong about what happened: it is arithmetic over traded volume from
the local archive.

For each session it produces the auction's own description of itself —
**POC** (the price that traded most), the **value area** (the 70% of volume
around it), **VWAP**, and the **high- and low-volume nodes** that act as shelves
and gaps. Then it asks one question price can actually answer:

    where is price now, relative to the value the market built yesterday?

⛔ THE VERDICT IS A LOOKUP, NOT A MODEL. Above value / inside value / below
value is a comparison of two numbers. Nothing here is fitted, trained, or
tuned, so it cannot drift, and every figure on screen traces to bars you hold.

⚠ Built ON THE ARCHIVE, never on a live fetch. Sessions that have closed never
change, so recomputing them from a network call would be both slower and less
reproducible than reading the bars we stored.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .instruments import derive_bin, resolve
from .profile import (ET, SessionStats, _histogram, _value_area,
                      compute_stats, high_volume_nodes, rth_window,
                      slice_bars, trading_days_desc)

# How far price must sit beyond the value area before "above"/"below" means
# anything. Expressed as a fraction of the value area's own width, so it
# scales with the instrument and with how wide the session was — a fixed
# point count would be noise on a quiet day and nothing on a volatile one.
EDGE = 0.08


def sessions_for(symbol: str, days: int = 10, interval: str = "5m") -> list[SessionStats]:
    """Per-session volume profiles, newest first, from the archive.

    ⚠ 5-minute bars, not 1-minute: the profile is a distribution, and at a
    sensible bin width the two are indistinguishable while 1m is 5x the rows.
    """
    from . import barstore

    inst = resolve("SPY" if symbol.upper() in ("SPY",) else "ES")
    spec = inst.session
    bars = barstore.read_store(symbol, interval)
    if not bars:
        return []

    out: list[SessionStats] = []
    for d in trading_days_desc(bars, spec)[:days]:
        s, e = rth_window(d, spec)
        chunk = slice_bars(bars, s, e)
        if len(chunk) < 5:
            continue
        try:
            bw = session_bin(chunk, inst)
            out.append(compute_stats(chunk, "RTH", d, inst.kind, bw=bw)
                       if _takes_bw(compute_stats) else
                       compute_stats(chunk, "RTH", d, inst.kind))
        except Exception:  # noqa: BLE001
            continue
    return out


TARGET_BINS = 40


def session_bin(chunk: list, inst) -> float:
    """Bin width for ONE session, from that session's own range.

    ⛔ NOT `derive_bin(price)`. Sizing from the price LEVEL gave SPY a 0.50
    bin, and a quiet 4-point session then has eight bins in it — a value area
    computed on four buckets is blocky enough that the POC jumps half a point
    at a time and the 70% edge lands wherever a bucket boundary happens to be.
    A profile is a DISTRIBUTION; it needs resolution across the range that was
    actually traded, not across the price it was trading at.

    ⚠ Snapped to the instrument's real tick so the levels are prices you could
    put an order at, never 761.8375.
    """
    hi = max(b.high for b in chunk)
    lo = min(b.low for b in chunk)
    rng = max(hi - lo, 1e-9)
    raw = rng / TARGET_BINS
    tick = inst.bin_pts or (0.25 if inst.kind == "future" else 0.01)
    steps = max(1, round(raw / tick))
    return steps * tick


def _takes_bw(fn) -> bool:
    import inspect
    try:
        return "bw" in inspect.signature(fn).parameters
    except Exception:  # noqa: BLE001
        return False


def verdict(price: float | None, prior: SessionStats | None) -> dict:
    """Where price stands against the prior session's value. Never raises.

    ⛔ Returns `unknown` rather than guessing when either input is missing.
    "We could not compute this" and "price is inside value" are opposite
    statements and must not share a rendering.
    """
    if price is None or prior is None:
        return {"state": "unknown", "label": "no session to compare against",
                "bias": None}
    vah, val, poc = prior.vah, prior.val, prior.poc
    if vah is None or val is None:
        return {"state": "unknown", "label": "prior session has no value area",
                "bias": None}
    width = max(vah - val, 1e-9)
    edge = width * EDGE
    if price > vah + edge:
        return {"state": "above", "bias": "bullish",
                "label": "accepted above yesterday's value",
                "distance": round(price - vah, 2), "ref": round(vah, 2)}
    if price < val - edge:
        return {"state": "below", "bias": "bearish",
                "label": "accepted below yesterday's value",
                "distance": round(val - price, 2), "ref": round(val, 2)}
    if price > poc:
        return {"state": "inside_upper", "bias": "neutral",
                "label": "inside value, above the POC",
                "distance": round(price - poc, 2), "ref": round(poc, 2)}
    return {"state": "inside_lower", "bias": "neutral",
            "label": "inside value, below the POC",
            "distance": round(poc - price, 2), "ref": round(poc, 2)}


def migration(sessions: list[SessionStats], n: int = 3) -> dict:
    """Is the POC walking up or down? The auction's own trend.

    ⚠ Needs at least three sessions to say anything — two points is a line
    through any two numbers, not a direction.
    """
    pocs = [s.poc for s in sessions[:n] if s.poc is not None]
    if len(pocs) < 3:
        return {"trend": "unknown", "label": "not enough sessions", "pocs": pocs}
    newest, oldest = pocs[0], pocs[-1]
    step = (newest - oldest) / max(len(pocs) - 1, 1)
    # ⚠ Compared against the value-area width, not an absolute number, so the
    # threshold means the same thing on ES at 7,700 and on a $40 stock.
    ref = next((s.vah - s.val for s in sessions[:n]
                if s.vah is not None and s.val is not None), None)
    thresh = (ref or abs(newest) * 0.002) * 0.25
    if step > thresh:
        return {"trend": "up", "label": "value migrating higher",
                "pocs": pocs, "step": round(step, 2)}
    if step < -thresh:
        return {"trend": "down", "label": "value migrating lower",
                "pocs": pocs, "step": round(step, 2)}
    return {"trend": "flat", "label": "value holding", "pocs": pocs,
            "step": round(step, 2)}


def levels(symbol: str, days: int = 10) -> dict:
    """Everything the quant tab renders. Never raises."""
    from . import barstore

    try:
        sess = sessions_for(symbol, days)
    except Exception:  # noqa: BLE001
        sess = []
    bars = barstore.read_store(symbol, "5m")
    price = bars[-1].close if bars else None
    prior = sess[1] if len(sess) > 1 else (sess[0] if sess else None)

    rows = []
    for s in sess:
        rows.append({
            "date": s.session_date.isoformat() if s.session_date else "—",
            "poc": s.poc, "vah": s.vah, "val": s.val, "vwap": s.vwap,
            "high": s.high, "low": s.low,
            "width": (round(s.vah - s.val, 2)
                      if (s.vah is not None and s.val is not None) else None),
        })

    return {
        "symbol": symbol,
        "price": price,
        "asof": bars[-1].ts.isoformat() if bars else None,
        "sessions": rows,
        "prior": rows[1] if len(rows) > 1 else (rows[0] if rows else None),
        "verdict": verdict(price, prior),
        "migration": migration(sess),
        "n": len(rows),
    }


def quant_bias(symbol: str) -> dict:
    """One line for the combined read: what the volume says, and why.

    ⛔ Two INDEPENDENT signals, reported separately and never blended into a
    score. Acceptance answers "where is price now"; migration answers "which
    way has value been moving". They can disagree, and when they do that is
    information, not an error to average away.
    """
    lv = levels(symbol, 5)
    v, m = lv["verdict"], lv["migration"]
    parts = [p for p in (v.get("bias"), {"up": "bullish", "down": "bearish"}.get(m["trend"]))
             if p]
    if not parts:
        bias = None
    elif len(set(parts)) == 1:
        bias = parts[0]
    else:
        bias = "mixed"
    return {
        "bias": bias,
        "acceptance": v, "migration": m,
        "price": lv["price"], "sessions": lv["n"],
        "why": " · ".join(x for x in (v.get("label"), m.get("label")) if x),
    }
