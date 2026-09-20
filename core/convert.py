"""ES / SPX / SPY level conversion.

⛔⛔ THE MODEL NEVER DOES THIS ARITHMETIC. It reports levels exactly as the
article wrote them, plus which instrument they are in. The conversion happens
here, in code, against a **measured** ratio — so a wrong number is a bug with a
stack trace rather than a hallucination that reads like analysis.

⛔ AND THE RATIO IS NOT 10. Measured live 2026-09-20: ES 7,712.50 against SPY
761.69 is **10.1255**. Dividing by 10 puts ES 7697 at SPY 769.70 when it is
really **760.16** — **9.54 SPY points out, 1.25%**. On a plan built from
levels that is the difference between a level sitting above price and below it,
which inverts the whole read.

⛔⛔ AND IT MUST BE THE RATIO **ON THE DAY THE ARTICLE WAS WRITTEN**, not
today's. The futures basis decays toward expiry and jumps at every quarterly
roll; SPY drifts against the index as dividends accrue. Measured across 126
trading days to 2026-09-18:

    min 10.0172 · max 10.1680 · spread 1.51%

On an ES 7697 level that is **11.40 SPY points** between the extremes — and
applying today's ratio to the oldest article in the feed misplaces it by 3.17.
A level that lands on the wrong side of price inverts the read that rests on
it, and nothing on screen would look wrong.

⭐ So `ratio_on(date)` reads that date's ES and SPY closes, and the ratio it
used is STORED on the article with the date it came from. A converted level is
then reproducible: you can check the arithmetic later without guessing which
number was in force.
"""

from __future__ import annotations

import threading
import time
from datetime import date

# SPX is the cash index; ES tracks it with a basis. SPY is ~1/10 of SPX, minus
# accumulated dividends. Both are measured, never assumed.
_CACHE: tuple[float, dict] | None = None
_TTL = 300.0
_LOCK = threading.Lock()

FALLBACK_ES_SPY = 10.12   # only if the live read fails; always labelled as such


def ratios(fresh: bool = False) -> dict:
    """Live ES→SPY and SPX→SPY ratios. Never raises.

    Returns {'es_spy', 'spx_spy', 'es', 'spy', 'spx', 'measured', 'at'}.
    `measured` is False when a fallback was used — the caller must say so.
    """
    global _CACHE
    now = time.monotonic()
    with _LOCK:
        if not fresh and _CACHE and now - _CACHE[0] < _TTL:
            return dict(_CACHE[1])

    out = {"es_spy": FALLBACK_ES_SPY, "spx_spy": FALLBACK_ES_SPY,
           "es": None, "spy": None, "spx": None, "measured": False,
           "at": None}
    try:
        from .bars import fetch_bars
        from .instruments import resolve
        _, m_spy = fetch_bars("SPY")
        spy = m_spy.get("regularMarketPrice")
        _, m_es = fetch_bars(resolve("ES").yahoo_symbol(date.today()))
        es = m_es.get("regularMarketPrice")
        if spy and es:
            out.update({"es": float(es), "spy": float(spy),
                        "es_spy": float(es) / float(spy), "measured": True,
                        "at": m_spy.get("regularMarketTime")})
        try:
            _, m_spx = fetch_bars("^GSPC")
            spx = m_spx.get("regularMarketPrice")
            if spx and spy:
                out["spx"] = float(spx)
                out["spx_spy"] = float(spx) / float(spy)
        except Exception:  # noqa: BLE001
            # SPX is optional — ES is what these articles quote.
            if out["measured"]:
                out["spx_spy"] = out["es_spy"]
    except Exception:  # noqa: BLE001
        pass

    with _LOCK:
        _CACHE = (now, out)
    return dict(out)


_BY_DATE: dict[str, dict] = {}
_DATE_LOCK = threading.Lock()


def ratio_on(day: str | date | None) -> dict:
    """The ES→SPY ratio as of `day` (YYYY-MM-DD or a date).

    Falls back to the nearest PRIOR trading day — an article published on a
    Sunday is priced off Friday's close, which is the last thing its author
    could have seen. Never raises; `measured` is False when it could not be
    established and the caller must say so.
    """
    if not day:
        return ratios()
    key = day if isinstance(day, str) else day.isoformat()
    key = key[:10]
    with _DATE_LOCK:
        if key in _BY_DATE:
            return dict(_BY_DATE[key])

    out = {"es_spy": FALLBACK_ES_SPY, "spx_spy": FALLBACK_ES_SPY,
           "es": None, "spy": None, "spx": None, "measured": False,
           "at": None, "asof": None, "requested": key}
    try:
        # ⭐ READ THE ARCHIVE FIRST. This used to fetch two years of daily bars
        # from Yahoo on every cold call and cost 3.0 s on an article page.
        # Historical closes never change, so they belong in the store.
        from . import barstore
        spy_bars = barstore.read_store("SPY", "1d")
        es_bars = barstore.read_store("ES=F", "1d")
        if not spy_bars or not es_bars:
            from .bars import fetch_bars
            if not spy_bars:
                spy_bars, _ = fetch_bars("SPY", interval="1d", range_="10y")
            if not es_bars:
                es_bars, _ = fetch_bars("ES=F", interval="1d", range_="10y")
        spy = {b.ts.date().isoformat(): b.close for b in spy_bars}
        es = {b.ts.date().isoformat(): b.close for b in es_bars}
        both = sorted(set(spy) & set(es))
        # nearest prior session
        prior = [d for d in both if d <= key]
        use = prior[-1] if prior else (both[0] if both else None)
        if use and spy[use]:
            out.update({"es": es[use], "spy": spy[use],
                        "es_spy": es[use] / spy[use], "spx_spy": es[use] / spy[use],
                        "measured": True, "asof": use})
    except Exception:  # noqa: BLE001
        pass

    # ⚠ Cache only a MEASURED result. Caching a fallback would pin an article
    # to a guessed ratio for the life of the process, and it would never
    # re-try once the data source recovered.
    if out["measured"]:
        with _DATE_LOCK:
            _BY_DATE[key] = out
    return dict(out)


def to_spy(price: float | None, unit: str, r: dict | None = None) -> float | None:
    """One level, in the article's own unit, as SPY. None stays None.

    ⚠ `unit` comes from the model and is untrusted — an unrecognised value
    returns None rather than guessing a divisor. A level converted by the wrong
    divisor is off by a factor of ten and still looks like a price.
    """
    if price is None:
        return None
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    r = r or ratios()
    u = (unit or "").strip().upper()
    if u == "SPY":
        return round(p, 2)
    if u in ("ES", "MES", "/ES"):
        return round(p / r["es_spy"], 2)
    if u in ("SPX", "^GSPC", "SPXW"):
        return round(p / r["spx_spy"], 2)
    return None


def spy_to(price: float | None, unit: str, r: dict | None = None) -> float | None:
    """The inverse — a SPY level expressed in the article's unit."""
    if price is None:
        return None
    r = r or ratios()
    u = (unit or "").strip().upper()
    if u == "SPY":
        return round(float(price), 2)
    if u in ("ES", "MES", "/ES"):
        return round(float(price) * r["es_spy"], 2)
    if u in ("SPX", "^GSPC", "SPXW"):
        return round(float(price) * r["spx_spy"], 2)
    return None


def plausible_spy(p: float | None) -> bool:
    """A sanity band on a converted SPY level.

    ⛔ THIS IS THE GUARD THAT CATCHES A WRONG UNIT. An ES level that escaped
    conversion arrives as ~7700 and would render as a SPY price, which is
    absurd but perfectly renderable. SPY has never traded outside this band and
    will not this week.
    """
    if p is None:
        return False
    return 50.0 <= float(p) <= 3000.0
