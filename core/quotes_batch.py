"""Quotes for a whole watchlist.

The scaling fact that drives this module: Yahoo's chart endpoint returns ONE
symbol per request, so a 20-symbol list is 20 HTTP calls — slow, and Yahoo
already 429s this app. Schwab quotes the entire list in a single call, in
real time.

So: one Schwab call when linked; otherwise Yahoo, capped and cached, with the
list plainly labelled delayed. The cap is surfaced, never silent — a symbol we
skipped renders as "not fetched", never as a blank that reads like no data.
"""

from __future__ import annotations

import time as _time

from .instruments import resolve
from .quote import YAHOO_DELAY_MIN, es_market_open

YAHOO_MAX_PER_REFRESH = 12   # beyond this, Yahoo rate-limits and the page crawls
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 20.0


def _from_schwab(symbols: list[str]) -> dict[str, dict] | None:
    """One request for every symbol. None if Schwab isn't usable."""
    try:
        from . import schwab
        if not schwab.access_token():
            return None
        mapping = {s: resolve(s).schwab_symbol() for s in symbols}
        payload = schwab.quotes(list(mapping.values()))
        if not payload:
            return None
    except Exception:  # noqa: BLE001
        return None

    out: dict[str, dict] = {}
    for sym, schwab_sym in mapping.items():
        q = schwab.parse_quote(payload, schwab_sym)
        if q and q.get("price"):
            out[sym] = {
                "symbol": sym, "price": float(q["price"]),
                "change_pct": q.get("change_pct"), "delayed_min": 0,
                "source": "schwab", "source_label": "Schwab · real-time",
                "as_of": q.get("as_of"), "ok": True,
            }
    return out or None


def _from_yahoo(symbols: list[str]) -> dict[str, dict]:
    from .bars import fetch_bars
    out: dict[str, dict] = {}
    for i, sym in enumerate(symbols):
        if i >= YAHOO_MAX_PER_REFRESH:
            out[sym] = {"symbol": sym, "price": None, "change_pct": None,
                        "as_of": None, "delayed_min": None, "ok": False,
                        "source": "yahoo", "source_label": "not fetched",
                        "skipped": True}
            continue
        try:
            inst = resolve(sym)
            _, meta = fetch_bars(inst.yahoo_symbol(), interval="5m", range_="1d")
            price = meta.get("regularMarketPrice")
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            chg = ((price - prev) / prev * 100) if price and prev else None
            out[sym] = {
                "symbol": sym, "price": price, "change_pct": chg,
                "source": "yahoo",
                "source_label": f"Yahoo · {YAHOO_DELAY_MIN}-min delayed",
                "as_of": meta.get("regularMarketTime"),
                "delayed_min": YAHOO_DELAY_MIN, "ok": price is not None,
            }
        except Exception as e:  # noqa: BLE001
            out[sym] = {"symbol": sym, "price": None, "change_pct": None,
                        "as_of": None, "delayed_min": None, "ok": False,
                        "source": "yahoo", "source_label": "unavailable",
                        "error": str(e)[:120]}
    return out


def quotes_for_snapshot(symbols: list[str]) -> dict:
    """The snapshot shape, from memory. ⛔ Never fetches.

    Same contract as `quotes_for` so the templates do not change, but every
    value comes from core.live — which the background loop keeps current.
    """
    from . import live
    snap = live.many(symbols)
    return {
        "quotes": {s: {"price": q.get("price"),
                       "change_pct": q.get("change_pct"),
                       "source": q.get("source"),
                       "source_label": ("Schwab · real time" if q.get("source") == "schwab"
                                        else "Yahoo · 10-min delayed" if q.get("live")
                                        else "last archived bar"),
                       "live": q.get("live")}
                   for s, q in snap.items()},
        "degraded": [s for s in symbols if s not in snap],
        "cap": None,
        "from_snapshot": True,
        "age_seconds": live.age_seconds(),
    }


def quotes_for(symbols: list[str], fresh: bool = False) -> dict:
    """{'quotes': {SYM: {...}}, 'source': 'schwab'|'yahoo', 'degraded': bool}"""
    symbols = [s.strip().upper() for s in symbols if s and s.strip()]
    if not symbols:
        return {"quotes": {}, "source": None, "degraded": False,
                "market_open": es_market_open(), "skipped": 0}

    key = ",".join(symbols)
    now = _time.monotonic()
    if not fresh and key in _CACHE and now - _CACHE[key][0] < _TTL:
        return _CACHE[key][1]

    got = _from_schwab(symbols)
    source = "schwab"
    if got is None:
        got = _from_yahoo(symbols)
        source = "yahoo"

    skipped = sum(1 for q in got.values() if q.get("skipped"))
    result = {
        "quotes": got,
        "source": source,
        "degraded": source != "schwab",
        "skipped": skipped,
        "cap": YAHOO_MAX_PER_REFRESH if source == "yahoo" else None,
        "market_open": es_market_open(),
        "fetched_at": _time.time(),
    }
    _CACHE[key] = (now, result)
    return result
