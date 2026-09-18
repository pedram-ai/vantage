"""Bar ingestion from the Yahoo chart API (v1 source per handoff §3)."""

from __future__ import annotations

import time as _time
from datetime import datetime, timezone

from curl_cffi import requests as curl_requests

from .profile import Bar

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36")

_CACHE: dict[str, tuple[float, list[Bar], dict]] = {}
_CACHE_TTL = 60.0  # seconds


class BarsError(RuntimeError):
    pass


def fetch_bars(symbol: str, interval: str = "5m", range_: str = "5d",
               use_cache: bool = True) -> tuple[list[Bar], dict]:
    """Fetch bars for a symbol. Returns (bars, meta)."""
    key = f"{symbol}:{interval}:{range_}"
    now = _time.monotonic()
    if use_cache and key in _CACHE and now - _CACHE[key][0] < _CACHE_TTL:
        _, bars, meta = _CACHE[key]
        return bars, meta

    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?interval={interval}&range={range_}&includePrePost=true")
    # Yahoo TLS-fingerprints plain Python clients (429); impersonate Chrome.
    last_err: Exception | None = None
    for host in ("query1", "query2"):
        try:
            r = curl_requests.get(url.replace("query1", host),
                                  impersonate="chrome", timeout=20)
            r.raise_for_status()
            data = r.json()
            result = data["chart"]["result"][0]
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            result = None
    if result is None:
        raise BarsError(f"Yahoo chart fetch failed for {symbol}: {last_err}")

    meta = result.get("meta", {})
    ts = result.get("timestamp") or []
    q = result["indicators"]["quote"][0]
    bars: list[Bar] = []
    for i, t in enumerate(ts):
        o, h, lo, c, v = (q["open"][i], q["high"][i], q["low"][i],
                          q["close"][i], q["volume"][i])
        if o is None or h is None or lo is None or c is None:
            continue
        bars.append(Bar(
            ts=datetime.fromtimestamp(t, tz=timezone.utc),
            open=o, high=h, low=lo, close=c,
            volume=float(v or 0),
        ))
    if not bars:
        raise BarsError(f"Yahoo returned no bars for {symbol}")
    _CACHE[key] = (now, bars, meta)
    return bars, meta


def last_price(symbol: str) -> dict:
    """Live-ish quote from chart meta (regularMarketPrice + time)."""
    _, meta = fetch_bars(symbol)
    return {
        "price": meta.get("regularMarketPrice"),
        "time": meta.get("regularMarketTime"),
        "previous_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
        "symbol": meta.get("symbol", symbol),
    }
