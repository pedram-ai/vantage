"""Builds a full Vantage run: ES + SPY session profiles, ES action map."""

from __future__ import annotations

from datetime import datetime, date

from .bars import fetch_bars, BarsError
from .calendar_rules import builtin_events
from .charts import chart_for
from .contracts import active_es_contract, yahoo_symbol
from .profile import ET, PT, build_sessions
from .quote import es_quote, es_market_open
from .zones import build_action_map
from . import store


def _sessions_payload(sess: dict) -> dict:
    return {
        "rth": [s.to_dict() for s in sess["rth"]],
        "overnight": sess["overnight"].to_dict() if sess["overnight"] else None,
        "combined": sess["combined"].to_dict() if sess["combined"] else None,
    }


def _ladder(sess: dict, price: float, bin_pts: int) -> list[dict]:
    """5-point (ES) / 1-point (SPY) bins of the combined histogram, plus
    level labels per bin — powers the Level Map."""
    combined = sess["combined"]
    if not combined:
        return []
    hist: dict[int, float] = {}
    for p, v in combined.histogram.items():
        b = (p // bin_pts) * bin_pts
        hist[b] = hist.get(b, 0.0) + v
    if not hist:
        return []
    max_v = max(hist.values())

    labels: dict[int, list[str]] = {}

    def put(px: float | None, text: str):
        if px is None:
            return
        b = (int(px) // bin_pts) * bin_pts
        labels.setdefault(b, []).append(text)

    rth = sess["rth"]
    if rth:
        s1 = rth[0]
        put(s1.vah, f"prior VAH {s1.vah:g}")
        put(s1.poc, f"prior POC {s1.poc:g}")
        put(s1.val, f"prior VAL {s1.val:g}")
        put(s1.vwap, f"prior VWAP {s1.vwap:.2f}")
        put(s1.open, f"prior open {s1.open:g}")
    if len(rth) > 1:
        put(rth[1].poc, f"prior-prior POC {rth[1].poc:g}")
    on = sess["overnight"]
    if on:
        put(on.high, f"o/n high {on.high:g}")
        put(on.low, f"o/n low {on.low:g}")

    lo_b = min(hist)
    hi_b = max(hist)
    # cap rows: widen bin if > 40 rows
    rows = []
    price_bin = (int(price) // bin_pts) * bin_pts
    for b in range(hi_b, lo_b - bin_pts, -bin_pts):
        v = hist.get(b, 0.0)
        row = {
            "price": b,
            "pct": round(100 * v / max_v) if max_v else 0,
            "labels": labels.get(b, []),
            "is_poc": (int(combined.poc) // bin_pts) * bin_pts == b,
            "in_value": combined.val <= b <= combined.vah,
            "is_price": b == price_bin,
            "is_vah": (int(combined.vah) // bin_pts) * bin_pts == b,
            "is_val": (int(combined.val) // bin_pts) * bin_pts == b,
        }
        rows.append(row)
    if len(rows) > 44:
        step = 2
        rows = [r for i, r in enumerate(rows) if i % step == 0 or r["labels"] or r["is_price"]]
    return rows


def build_run(persist: bool = False, tf: str = "15m", with_charts: bool = False,
              fresh: bool = False) -> dict:
    now_et = datetime.now(ET)
    today: date = now_et.date()
    contract = store.get_settings().get("contract_override") or active_es_contract(today)

    errors: list[str] = []

    # --- ES ---
    es = None
    try:
        # HISTORY comes from Yahoo (free, and a 10-min delay is meaningless for
        # a completed session). The LIVE price prefers Schwab — see core/quote.py.
        es_bars, es_meta = fetch_bars(yahoo_symbol(contract), use_cache=not fresh)
        es_sess = build_sessions(es_bars)
        es_q = es_quote(contract, es_meta,
                        yahoo_fallback_price=es_sess["rth"][0].close if es_sess["rth"] else None)
        es_price = float(es_q["price"])
        rth = es_sess["rth"]
        s1 = rth[0] if rth else None
        s2 = rth[1] if len(rth) > 1 else None
        author_levels = store.levels_for_session(today.isoformat())
        events = builtin_events(today) + store.events_for(today.isoformat())
        is_qopex = any(e["kind"] == "quarterly_opex" for e in events)
        action_map = build_action_map(
            es_price, s1, s2, es_sess["overnight"], author_levels, is_qopex,
        ) if s1 else None
        es_chart = None
        if with_charts:
            lv = [(s1.poc, f"pPOC {s1.poc:g}"), (s1.vah, f"pVAH {s1.vah:g}"),
                  (s1.val, f"pVAL {s1.val:g}")] if s1 else []
            es_chart = chart_for(yahoo_symbol(contract), tf, es_bars, lv)
        es = {
            "contract": contract,
            "price": round(es_price, 2),
            "quote": es_q,
            "chart_svg": es_chart,
            "sessions": _sessions_payload(es_sess),
            "ladder": _ladder(es_sess, es_price, 5),
            "action_map": action_map,
            "author_levels": author_levels,
            "events": events,
        }
    except (BarsError, Exception) as e:  # noqa: BLE001
        errors.append(f"ES: {e}")

    # --- SPY ---
    spy = None
    try:
        spy_bars, spy_meta = fetch_bars("SPY", use_cache=not fresh)
        spy_sess = build_sessions(spy_bars)
        spy_price = float(spy_meta.get("regularMarketPrice") or spy_sess["rth"][0].close)
        spy_chart = None
        if with_charts:
            srth = spy_sess["rth"]
            lv = [(srth[0].poc, f"pPOC {srth[0].poc:g}"), (srth[0].vah, f"pVAH {srth[0].vah:g}"),
                  (srth[0].val, f"pVAL {srth[0].val:g}")] if srth else []
            spy_chart = chart_for("SPY", tf, spy_bars, lv)
        # SPY is a different entitlement from CME futures. Yahoo's table lists
        # Nasdaq real-time but does not break out NYSE Arca, so the delay here
        # is UNVERIFIED — say so rather than imply it is live.
        from .quote import _age_text
        spy = {
            "price": round(spy_price, 2),
            "quote": {
                "source": "yahoo",
                "source_label": "Yahoo · delay unverified",
                "as_of": spy_meta.get("regularMarketTime"),
                "age_text": _age_text(spy_meta.get("regularMarketTime"), es_market_open()),
            },
            "chart_svg": spy_chart,
            "sessions": _sessions_payload(spy_sess),
            "ladder": _ladder(spy_sess, spy_price, 1),
        }
    except (BarsError, Exception) as e:  # noqa: BLE001
        errors.append(f"SPY: {e}")

    run = {
        "date": today.isoformat(),
        "generated_at_pt": datetime.now(PT).strftime("%Y-%m-%d %I:%M %p PT"),
        "es": es,
        "spy": spy,
        "errors": errors,
        "verdict": (es or {}).get("action_map", {}).get("verdict") if es and es.get("action_map") else None,
    }
    if persist:
        # ladders and histograms are recomputable; keep archived runs lean
        slim = dict(run)
        if es:
            slim_es = dict(es)
            slim_es.pop("ladder", None)
            slim["es"] = slim_es
        if spy:
            slim_spy = dict(spy)
            slim_spy.pop("ladder", None)
            slim["spy"] = slim_spy
        run["run_id"] = store.save_run(slim)
    return run
