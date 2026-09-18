"""Session profile math for Vantage.

Ports §4.1 of VANTAGE_HANDOFF.md. All session boundaries are ET
(America/New_York):

  RTH:        09:30 - 16:00
  Overnight:  18:00 previous day - 09:30
  Full:       18:00 previous day - 16:00

For each session we compute open/high/low/close, VWAP (typical price x
volume / volume), a 1-point volume histogram (each bar's volume spread
evenly over the integer prices it spans), POC, and the 70% value area
(rank bins by volume, add until >= 70% of total; VAL = lowest chosen bin,
VAH = highest chosen bin).

Regression (ESZ26, from the handoff §11 — reproduce from the same bars):
  Wed 9/16 RTH:  O 7676.5  H 7699    L 7575    C 7622.25  POC 7679  VAH 7692  VAL 7591  VWAP ~7653
  Thu 9/17 RTH:  O 7713.5  H 7716.25 L 7680.5  C 7707.25  POC 7705  VAH 7711  VAL 7691  VWAP ~7700.7
  Fri overnight: O 7703.5  H 7739.25 L 7692.25 C 7709.75  POC 7701  VAH 7735  VAL 7697  VWAP ~7713.4
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")


@dataclass
class Bar:
    ts: datetime  # tz-aware
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class SessionStats:
    label: str
    session_date: date
    kind: str  # "rth" | "overnight" | "combined"
    open: float
    high: float
    low: float
    close: float
    vwap: float
    poc: float
    vah: float
    val: float
    total_volume: float
    histogram: dict[int, float] = field(default_factory=dict)
    hvns: list[int] = field(default_factory=list)
    n_bars: int = 0

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "session_date": self.session_date.isoformat(),
            "kind": self.kind,
            "open": self.open, "high": self.high, "low": self.low,
            "close": self.close, "vwap": round(self.vwap, 2),
            "poc": self.poc, "vah": self.vah, "val": self.val,
            "total_volume": self.total_volume,
            "hvns": self.hvns,
            "n_bars": self.n_bars,
        }


def _histogram(bars: list[Bar]) -> dict[int, float]:
    hist: dict[int, float] = {}
    for b in bars:
        if not b.volume or b.volume <= 0:
            continue
        lo = int(math.floor(b.low))
        hi = int(math.floor(b.high))
        prices = range(lo, hi + 1)
        share = b.volume / len(prices)
        for p in prices:
            hist[p] = hist.get(p, 0.0) + share
    return hist


def _value_area(hist: dict[int, float], pct: float = 0.70) -> tuple[int, int, int]:
    """Return (poc, vah, val). Rank bins by volume, accumulate to >= pct."""
    total = sum(hist.values())
    ranked = sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))
    chosen: list[int] = []
    acc = 0.0
    for price, vol in ranked:
        chosen.append(price)
        acc += vol
        if acc >= pct * total:
            break
    poc = ranked[0][0]
    return poc, max(chosen), min(chosen)


def high_volume_nodes(hist: dict[int, float], ratio: float = 1.5) -> list[int]:
    """Local maxima >= ratio x both neighbours."""
    out = []
    for p, v in hist.items():
        left = hist.get(p - 1, 0.0)
        right = hist.get(p + 1, 0.0)
        if v > 0 and v >= ratio * max(left, 1e-9) and v >= ratio * max(right, 1e-9):
            out.append(p)
    return sorted(out)


def compute_stats(bars: list[Bar], label: str, session_date: date, kind: str) -> SessionStats | None:
    bars = [b for b in bars if b.volume is not None]
    if not bars:
        return None
    bars = sorted(bars, key=lambda b: b.ts)
    hist = _histogram(bars)
    if not hist:
        return None
    poc, vah, val = _value_area(hist)
    tot_v = sum(b.volume for b in bars)
    vwap = (
        sum(((b.high + b.low + b.close) / 3) * b.volume for b in bars) / tot_v
        if tot_v > 0 else bars[-1].close
    )
    return SessionStats(
        label=label, session_date=session_date, kind=kind,
        open=bars[0].open,
        high=max(b.high for b in bars),
        low=min(b.low for b in bars),
        close=bars[-1].close,
        vwap=vwap, poc=poc, vah=vah, val=val,
        total_volume=tot_v,
        histogram=hist,
        hvns=high_volume_nodes(hist),
        n_bars=len(bars),
    )


def rth_window(d: date) -> tuple[datetime, datetime]:
    return (datetime.combine(d, time(9, 30), ET), datetime.combine(d, time(16, 0), ET))


def overnight_window(rth_date: date) -> tuple[datetime, datetime]:
    prev = rth_date - timedelta(days=1)
    return (datetime.combine(prev, time(18, 0), ET), datetime.combine(rth_date, time(9, 30), ET))


def slice_bars(bars: list[Bar], start: datetime, end: datetime) -> list[Bar]:
    return [b for b in bars if start <= b.ts < end]


def trading_days_desc(bars: list[Bar]) -> list[date]:
    """Distinct dates (ET) that have RTH bars, newest first."""
    days = set()
    for b in bars:
        t = b.ts.astimezone(ET)
        if time(9, 30) <= t.time() < time(16, 0) and t.weekday() < 5:
            days.add(t.date())
    return sorted(days, reverse=True)


def build_sessions(bars: list[Bar], now: datetime | None = None) -> dict:
    """Compute the session set the map needs.

    Returns dict with keys: rth (list of last 3 completed-or-current RTH
    SessionStats, newest first), overnight (current/most recent overnight),
    combined (last-3-RTH combined profile).
    """
    now = now or datetime.now(ET)
    days = trading_days_desc(bars)
    rth_stats: list[SessionStats] = []
    for d in days[:4]:
        s, e = rth_window(d)
        chunk = slice_bars(bars, s, e)
        st = compute_stats(chunk, f"RTH {d.isoformat()}", d, "rth")
        if st:
            rth_stats.append(st)
        if len(rth_stats) == 3:
            break

    # Overnight for the next session after the newest RTH day (or today's).
    on_stats = None
    if days:
        # find the overnight that ends at the next RTH open after the last close
        candidates = [days[0] + timedelta(days=1), days[0]]
        for d in candidates:
            s, e = overnight_window(d)
            chunk = slice_bars(bars, s, e)
            if len(chunk) >= 6:
                st = compute_stats(chunk, f"Overnight into {d.isoformat()}", d, "overnight")
                if st:
                    on_stats = st
                    break

    combined = None
    if rth_stats:
        all_bars: list[Bar] = []
        for st in rth_stats:
            s, e = rth_window(st.session_date)
            all_bars.extend(slice_bars(bars, s, e))
        combined = compute_stats(all_bars, "Last 3 RTH combined", rth_stats[0].session_date, "combined")

    return {"rth": rth_stats, "overnight": on_stats, "combined": combined}


def classify_open(price: float, s1: SessionStats) -> dict:
    """Tic Toc's open taxonomy (§2.1). s1 = prior RTH session."""
    va_width = max(s1.vah - s1.val, 1.0)
    near = abs(price - s1.poc) <= max(5.0, 0.2 * va_width)
    inside = s1.val <= price <= s1.vah
    if inside and near:
        code, text = "A", (
            "Open inside prior value and near prior POC - lean on prior "
            "VAH/VAL for reactions; breakouts fizzle and can be faded; trend "
            "day unlikely unless prior high/low goes in the first hour."
        )
    elif inside and price < s1.poc:
        code, text = "B-below", (
            "Inside prior value but below prior POC - slight bearish lean; "
            "expect a retrace to prior VAH to find sellers, or a fast trend "
            "down without testing VAH."
        )
    elif inside:
        code, text = "B-above", (
            "Inside prior value but above prior POC - slight bullish lean; "
            "expect a retrace to prior VAL to find buyers, or a fast trend up."
        )
    elif price > s1.vah:
        code, text = "outside-up", (
            "Open above prior value (wild card) - default expectation is a "
            "gap fill / test of prior VAH unless the first hour makes higher "
            "highs and higher lows."
        )
    else:
        code, text = "outside-down", (
            "Open below prior value (wild card) - bears get the benefit of "
            "the doubt; if a new POC forms below the open, short retraces to "
            "prior VAL/VAH/open; otherwise default is a gap fill."
        )
    return {"code": code, "text": text, "inside_value": inside, "near_poc": near}
