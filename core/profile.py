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
    histogram: dict[float, float] = field(default_factory=dict)
    hvns: list[float] = field(default_factory=list)
    n_bars: int = 0

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "session_date": self.session_date.isoformat(),
            "kind": self.kind,
            "open": round(self.open, 2), "high": round(self.high, 2),
            "low": round(self.low, 2),
            "close": round(self.close, 2), "vwap": round(self.vwap, 2),
            "poc": round(self.poc, 2), "vah": round(self.vah, 2),
            "val": round(self.val, 2),
            "total_volume": self.total_volume,
            "hvns": self.hvns,
            "n_bars": self.n_bars,
        }


def _histogram(bars: list[Bar], bw: float = 1.0) -> dict[float, float]:
    """Volume histogram in `bw`-wide price bins.

    `bw` is per-instrument. A fixed 1-point bin is right for ES at 7,700 and
    useless on a $40 stock, where it collapses the whole profile into a few
    buckets. Keys are bin LOWER EDGES, as floats.
    """
    hist: dict[float, float] = {}
    if bw <= 0:
        bw = 1.0
    for b in bars:
        if not b.volume or b.volume <= 0:
            continue
        lo_i = int(math.floor(b.low / bw))
        hi_i = int(math.floor(b.high / bw))
        n = hi_i - lo_i + 1
        share = b.volume / n
        for i in range(lo_i, hi_i + 1):
            key = round(i * bw, 6)
            hist[key] = hist.get(key, 0.0) + share
    return hist


def _value_area(hist: dict[float, float], pct: float = 0.70) -> tuple[float, float, float]:
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


def high_volume_nodes(hist: dict[float, float], ratio: float = 1.5,
                      bw: float = 1.0) -> list[float]:
    """Local maxima >= ratio x both neighbouring bins."""
    out = []
    for p, v in hist.items():
        left = hist.get(round(p - bw, 6), 0.0)
        right = hist.get(round(p + bw, 6), 0.0)
        if v > 0 and v >= ratio * max(left, 1e-9) and v >= ratio * max(right, 1e-9):
            out.append(p)
    return sorted(out)


def compute_stats(bars: list[Bar], label: str, session_date: date, kind: str,
                  bw: float = 1.0) -> SessionStats | None:
    bars = [b for b in bars if b.volume is not None]
    if not bars:
        return None
    bars = sorted(bars, key=lambda b: b.ts)
    hist = _histogram(bars, bw)
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
        hvns=high_volume_nodes(hist, bw=bw),
        n_bars=len(bars),
    )


def rth_window(d: date, spec=None) -> tuple[datetime, datetime]:
    """RTH window for a date, per the instrument's session spec."""
    from .instruments import CME_FUTURES
    spec = spec or CME_FUTURES
    return (datetime.combine(d, spec.rth_start, ET),
            datetime.combine(d, spec.rth_end, ET))


def overnight_window(rth_date: date, spec=None) -> tuple[datetime, datetime]:
    """The extended session leading into an RTH open.

    Futures: 18:00 the previous day -> 09:30. Equities have no overnight, so
    this is the 04:00 pre-market of the same day. The distinction matters —
    applying the futures window to SPY would sweep in the prior afternoon's
    post-market and call it 'overnight'.
    """
    from .instruments import CME_FUTURES
    spec = spec or CME_FUTURES
    if spec.pre_starts_prev_day:
        start = datetime.combine(rth_date - timedelta(days=1), spec.pre_start, ET)
    else:
        start = datetime.combine(rth_date, spec.pre_start, ET)
    return (start, datetime.combine(rth_date, spec.rth_start, ET))


def slice_bars(bars: list[Bar], start: datetime, end: datetime) -> list[Bar]:
    return [b for b in bars if start <= b.ts < end]


def trading_days_desc(bars: list[Bar], spec=None) -> list[date]:
    """Distinct dates (ET) that have RTH bars, newest first."""
    from .instruments import CME_FUTURES
    spec = spec or CME_FUTURES
    days = set()
    for b in bars:
        t = b.ts.astimezone(ET)
        if spec.rth_start <= t.time() < spec.rth_end and t.weekday() < 5:
            days.add(t.date())
    return sorted(days, reverse=True)


def build_sessions(bars: list[Bar], now: datetime | None = None, spec=None,
                   bw: float = 1.0) -> dict:
    """Compute the session set the map needs.

    Returns dict with keys: rth (last 3 RTH SessionStats, newest first),
    overnight (most recent extended session), combined (3-RTH profile).
    """
    from .instruments import CME_FUTURES
    spec = spec or CME_FUTURES
    now = now or datetime.now(ET)
    days = trading_days_desc(bars, spec)
    rth_stats: list[SessionStats] = []
    for d in days[:4]:
        s, e = rth_window(d, spec)
        chunk = slice_bars(bars, s, e)
        st = compute_stats(chunk, f"RTH {d.isoformat()}", d, "rth", bw)
        if st:
            rth_stats.append(st)
        if len(rth_stats) == 3:
            break

    on_stats = None
    if days:
        candidates = [days[0] + timedelta(days=1), days[0]]
        for d in candidates:
            s, e = overnight_window(d, spec)
            chunk = slice_bars(bars, s, e)
            if len(chunk) >= 6:
                st = compute_stats(chunk, f"{spec.label_overnight} into {d.isoformat()}",
                                   d, "overnight", bw)
                if st:
                    on_stats = st
                    break

    combined = None
    if rth_stats:
        all_bars: list[Bar] = []
        for st in rth_stats:
            s, e = rth_window(st.session_date, spec)
            all_bars.extend(slice_bars(bars, s, e))
        combined = compute_stats(all_bars, "Last 3 RTH combined",
                                 rth_stats[0].session_date, "combined", bw)

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
