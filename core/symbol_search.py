"""Symbol autocomplete.

Two sources, merged, best-first:

  1. **What you already track** — every symbol on any watchlist, plus every
     symbol you have ever traded. These rank first because the common case is
     re-adding something you already follow.
  2. **Yahoo's search endpoint** — the whole universe, so a symbol you have
     never touched is still one keystroke away.

⛔ A TYPED SYMBOL LIST WOULD GO STALE SILENTLY. Tickers change, companies get
acquired, ETFs close. A fixed list offers names that no longer resolve and
omits everything listed since it was written — and nothing goes red either way.
The local half is derived from your own data; the remote half is the exchange's
own index.

⚠ Yahoo TLS-fingerprints plain Python clients (see core/bars.py), so this uses
`curl_cffi` with `impersonate="chrome"` like every other Yahoo call here.

Fail-soft: if the remote lookup fails, the local matches are still returned and
the field still works. A dead autocomplete must never block typing a symbol.
"""

from __future__ import annotations

import threading
import time

SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
TIMEOUT = 4.0

# Yahoo classifies results; these are the ones worth offering.
KEEP_TYPES = {"EQUITY", "ETF", "INDEX", "FUTURE", "MUTUALFUND", "CURRENCY",
              "CRYPTOCURRENCY"}

_CACHE: dict[str, tuple[float, list]] = {}
_TTL = 900.0
_LOCK = threading.Lock()


def _local(q: str) -> list[dict]:
    """Symbols already in your own data. Never raises."""
    out: dict[str, dict] = {}
    try:
        from . import watchlists
        for lst in watchlists.all_lists():
            for s in lst.get("symbols", []):
                out.setdefault(s.upper(), {"symbol": s.upper(), "name": lst.get("name", ""),
                                           "kind": "on a watchlist", "local": True})
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import portfolio
        for t in portfolio._all("trades"):
            s = (t.get("symbol") or "").upper()
            if s:
                out.setdefault(s, {"symbol": s, "name": "", "kind": "you have traded",
                                   "local": True})
    except Exception:  # noqa: BLE001
        pass
    ql = q.upper()
    return [v for k, v in sorted(out.items()) if k.startswith(ql)]


def _remote(q: str) -> list[dict]:
    """Yahoo's own symbol index. Never raises."""
    try:
        from curl_cffi import requests as cr
        r = cr.get(SEARCH_URL, params={"q": q, "quotesCount": 12, "newsCount": 0},
                   impersonate="chrome", timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        rows = []
        for it in (r.json().get("quotes") or []):
            sym = (it.get("symbol") or "").upper()
            typ = (it.get("quoteType") or "").upper()
            if not sym or typ not in KEEP_TYPES:
                continue
            rows.append({
                "symbol": sym,
                "name": it.get("shortname") or it.get("longname") or "",
                "kind": (it.get("typeDisp") or typ.title()),
                "exchange": it.get("exchDisp") or "",
                "local": False,
            })
        return rows
    except Exception:  # noqa: BLE001
        return []


def search(q: str, limit: int = 10) -> list[dict]:
    """Merged, de-duplicated, yours first. Never raises."""
    q = (q or "").strip()
    if len(q) < 1:
        return []
    key = q.upper()
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _TTL:
            return hit[1][:limit]

    rows, seen = [], set()
    for r in _local(q) + _remote(q):
        if r["symbol"] in seen:
            continue
        seen.add(r["symbol"])
        rows.append(r)

    with _LOCK:
        _CACHE[key] = (now, rows)
    return rows[:limit]
