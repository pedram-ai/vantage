"""The combined read — news, X, and the volume profile, side by side.

⛔⛔ THE THREE INPUTS ARE NEVER BLENDED INTO ONE SCORE. They measure different
things and they are wrong in different ways:

  * **news** is what commentators SAY, weighted by nothing but how many said
    it. It is opinion, it lags, and a loud week is not a right week.
  * **X** is the same substance at higher frequency and lower quality.
  * **quant** is the only one that cannot be wrong about the past: it is
    arithmetic over traded volume. It says nothing about the future.

A weighted average of those would produce a single confident number whose
inputs nobody could inspect — and when it was wrong, nothing would say which
part was wrong. So each is reported with its own evidence count, and the
headline is simply whether they AGREE.

⭐ DISAGREEMENT IS THE INTERESTING CASE and is surfaced as such rather than
smoothed away. "Commentary is bullish while price sits below value" is a
sentence worth reading; an average of the two is not.
"""

from __future__ import annotations

LEAN = ("bullish", "bearish", "neutral")


def _news(horizon: str = "24h", days: int = 14) -> dict:
    """What the read articles say at one horizon. Counts, never an average."""
    from . import research
    tally = {k: 0 for k in LEAN}
    voices: dict[str, str] = {}
    n_read = 0
    for a in research._within(research._list_all(), days):
        rd = a.get("read")
        if not isinstance(rd, dict) or "calls" not in rd:
            continue
        n_read += 1
        for c in (rd.get("calls") or []):
            if c.get("horizon") != horizon:
                continue
            b = (c.get("bias") or "neutral").lower()
            if b in tally:
                tally[b] += 1
                who = research.person_name(a.get("source_label") or "")
                # one vote per PERSON per horizon: a commentator covered by
                # five outlets in one week is still one opinion
                voices[who] = b
    pv = {k: sum(1 for v in voices.values() if v == k) for k in LEAN}
    total = sum(pv.values())
    return {
        "component": "news",
        "bias": (max(pv, key=pv.get) if total else None),
        "tally": pv, "voices": total, "articles_read": n_read,
        "detail": (", ".join(f"{v} {k}" for k, v in pv.items() if v)
                   or "nobody addresses this horizon yet"),
    }


def _x() -> dict:
    """X. ⛔ NOT CONNECTED, and it says so rather than returning neutral.

    Every free route is closed: nitter is dead, syndication.twitter.com
    rate-limits to 429 or returns nothing, x.com serves a JavaScript shell
    with no tweet text in the HTML, and the browser extension is blocked from
    the domain at policy level. The X API v2 needs a paid tier.

    Returning "neutral" here would let an absent input vote.
    """
    return {
        "component": "x", "bias": None, "voices": 0,
        "unavailable": True,
        "detail": "not connected — X API v2 requires a paid tier",
    }


def _quant(symbol: str) -> dict:
    from . import quant
    q = quant.quant_bias(symbol)
    return {
        "component": "quant", "bias": q.get("bias"),
        "voices": q.get("sessions", 0),
        "price": q.get("price"),
        "acceptance": q.get("acceptance"), "migration": q.get("migration"),
        "detail": q.get("why") or "not enough sessions in the archive",
    }


def read(symbol: str = "SPY", horizon: str = "24h", days: int = 14) -> dict:
    """The three components and whether they agree. Never raises."""
    try:
        news = _news(horizon, days)
    except Exception as e:  # noqa: BLE001
        news = {"component": "news", "bias": None, "voices": 0,
                "detail": f"unavailable: {str(e)[:60]}"}
    try:
        qt = _quant(symbol)
    except Exception as e:  # noqa: BLE001
        qt = {"component": "quant", "bias": None, "voices": 0,
              "detail": f"unavailable: {str(e)[:60]}"}
    xx = _x()

    parts = [news, xx, qt]
    stated = [p for p in parts if p.get("bias") and p["bias"] != "mixed"]
    biases = {p["bias"] for p in stated}

    if not stated:
        agreement, headline = "none", "Nothing has a view yet."
    elif len(biases) == 1 and len(stated) > 1:
        agreement = "agree"
        headline = f"All {len(stated)} inputs read {stated[0]['bias']}."
    elif len(biases) == 1:
        agreement = "single"
        headline = (f"Only {stated[0]['component']} has a view — "
                    f"{stated[0]['bias']}.")
    else:
        agreement = "split"
        headline = " vs ".join(
            f"{p['component']} {p['bias']}" for p in stated)

    return {
        "symbol": symbol, "horizon": horizon, "days": days,
        "components": parts, "agreement": agreement, "headline": headline,
        # ⛔ Deliberately NO single score. See the module docstring.
        "score": None,
    }
