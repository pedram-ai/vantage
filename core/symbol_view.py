"""Everything one instrument's page needs — for ES, SPY, QQQ or any ticker.

This replaces the ES-shaped `run_builder`: the profile engine, zone algorithm
and ladder are instrument-agnostic, they just need the right session spec and
bin width. What stays ES-only is author levels, because only ES has published
ones — and that is stated on screen rather than implied by an empty section.
"""

from __future__ import annotations

from datetime import date, datetime

from .bars import fetch_bars, BarsError
from .calendar_rules import builtin_events
from .charts import chart_for
from .instruments import Instrument, derive_bin, derive_ladder_bin, resolve
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


def _ladder(sess: dict, price: float, bin_w: float) -> list[dict]:
    combined = sess["combined"]
    if not combined or bin_w <= 0:
        return []
    hist: dict[float, float] = {}
    for p, v in combined.histogram.items():
        b = round((p // bin_w) * bin_w, 4)
        hist[b] = hist.get(b, 0.0) + v
    if not hist:
        return []
    max_v = max(hist.values())
    labels: dict[float, list[str]] = {}

    def put(px, text):
        if px is None:
            return
        labels.setdefault(round((px // bin_w) * bin_w, 4), []).append(text)

    rth = sess["rth"]
    if rth:
        s1 = rth[0]
        put(s1.vah, f"prior VAH {s1.vah:g}")
        put(s1.poc, f"prior POC {s1.poc:g}")
        put(s1.val, f"prior VAL {s1.val:g}")
        put(s1.vwap, f"prior VWAP {s1.vwap:.2f}")
    if len(rth) > 1:
        put(rth[1].poc, f"prior-prior POC {rth[1].poc:g}")
    on = sess["overnight"]
    if on:
        put(on.high, f"{on.kind} high {on.high:g}")
        put(on.low, f"{on.kind} low {on.low:g}")

    lo_b, hi_b = min(hist), max(hist)
    price_bin = round((price // bin_w) * bin_w, 4)
    rows = []
    b = hi_b
    guard = 0
    while b >= lo_b - bin_w / 2 and guard < 400:
        guard += 1
        v = hist.get(round(b, 4), 0.0)
        rows.append({
            "price": round(b, 2),
            "pct": round(100 * v / max_v) if max_v else 0,
            "labels": labels.get(round(b, 4), []),
            "is_poc": round((combined.poc // bin_w) * bin_w, 4) == round(b, 4),
            "in_value": combined.val <= b <= combined.vah,
            "is_price": round(b, 4) == price_bin,
            "is_vah": round((combined.vah // bin_w) * bin_w, 4) == round(b, 4),
            "is_val": round((combined.val // bin_w) * bin_w, 4) == round(b, 4),
        })
        b = round(b - bin_w, 4)
    if len(rows) > 44:
        rows = [r for i, r in enumerate(rows)
                if i % 2 == 0 or r["labels"] or r["is_price"]]
    return rows


def build_symbol(symbol: str, tf: str = "1d", with_chart: bool = True,
                 fresh: bool = False, with_zones: bool = True) -> dict:
    """Full view for one instrument. Never raises — errors come back as data."""
    inst: Instrument = resolve(symbol)
    today = datetime.now(ET).date()
    out: dict = {
        "symbol": inst.symbol,
        "instrument": inst.to_dict(today),
        "error": None,
    }
    try:
        y = inst.yahoo_symbol(today)
        bars, meta = fetch_bars(y, use_cache=not fresh)
        last_close = None
        price = meta.get("regularMarketPrice")

        bw = inst.bin_pts or derive_bin(float(price or 1), inst.kind)
        sess = build_sessions(bars, spec=inst.session, bw=bw)
        rth = sess["rth"]
        if not rth:
            out["error"] = "not enough session data"
            return out
        last_close = rth[0].close
        if price is None:
            price = last_close

        # ES gets the Schwab/Yahoo provenance path; other symbols are Yahoo
        # today and say so rather than implying a live feed.
        if inst.symbol == "ES":
            q = es_quote(inst.display_contract(today), meta, last_close)
        else:
            from .quote import _age_text
            q = {
                "price": float(price), "source": "yahoo",
                "source_label": "Yahoo · delay unverified",
                "delayed_min": None, "as_of": meta.get("regularMarketTime"),
                "age_text": _age_text(meta.get("regularMarketTime"), es_market_open()),
                "market_open": es_market_open(), "symbol": inst.symbol,
            }
        price = float(q["price"])

        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        out["change_pct"] = ((price - prev) / prev * 100) if prev else None
        out["price"] = round(price, 2)
        out["quote"] = q
        out["sessions"] = _sessions_payload(sess)
        out["bin"] = bw

        lb = inst.ladder_bin or derive_ladder_bin(price, inst.kind)
        out["ladder"] = _ladder(sess, price, lb)
        out["ladder_bin"] = lb

        if with_zones:
            s1 = rth[0]
            s2 = rth[1] if len(rth) > 1 else None
            levels = (store.levels_for_session(today.isoformat())
                      if inst.has_author_levels else [])
            events = builtin_events(today) + store.events_for(today.isoformat())
            is_qopex = any(e["kind"] == "quarterly_opex" for e in events)
            out["events"] = events
            out["author_levels"] = levels
            out["action_map"] = build_action_map(
                price, s1, s2, sess["overnight"], levels, is_qopex)

        if with_chart:
            s1 = rth[0]
            lv = [(s1.poc, f"POC {s1.poc:g}"), (s1.vah, f"VAH {s1.vah:g}"),
                  (s1.val, f"VAL {s1.val:g}")]
            out["chart_svg"] = chart_for(y, tf, bars, lv)
    except BarsError as e:
        out["error"] = f"price data unavailable: {e}"
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:200]
    return out


def build_today(symbols: list[str], fresh: bool = False) -> dict:
    """The Today briefing: a verdict row per symbol."""
    rows = []
    for sym in symbols:
        v = build_symbol(sym, with_chart=False, fresh=fresh)
        am = v.get("action_map") or {}
        dist = am.get("distances", {})
        near = None
        if am.get("zone_state") in ("dip", "fade"):
            near = "in the band"
        elif dist.get("put_zone_above") and dist.get("call_zone_below"):
            pu = dist["put_zone_above"]["pts"]
            cd = dist["call_zone_below"]["pts"]
            near = (f"put zone {pu:.2f} above" if pu <= cd
                    else f"call zone {cd:.2f} below")
        rows.append({
            "symbol": v["symbol"],
            "instrument": v.get("instrument", {}),
            "price": v.get("price"),
            "change_pct": v.get("change_pct"),
            "verdict": am.get("verdict"),
            "zone_state": am.get("zone_state"),
            "near": near,
            "quote": v.get("quote"),
            "error": v.get("error"),
            "events": v.get("events", []),
        })
    events = builtin_events(datetime.now(ET).date())
    return {
        "rows": rows,
        "events": events,
        "generated_at_pt": datetime.now(PT).strftime("%a %b %-d · %-I:%M %p PT"),
        "date": datetime.now(ET).date().isoformat(),
    }
