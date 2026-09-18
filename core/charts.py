"""Server-rendered SVG candlestick charts for the web UI (SVG is allowed
in the UI, never in email)."""

from __future__ import annotations

from datetime import timedelta

from .profile import ET, Bar

GREEN = "#2e7d32"
RED = "#c62828"


def resample(bars: list[Bar], minutes: int) -> list[Bar]:
    """Aggregate 5m bars into N-minute candles (ET bucket alignment)."""
    out: list[Bar] = []
    cur_key = None
    for b in sorted(bars, key=lambda x: x.ts):
        t = b.ts.astimezone(ET)
        key = t.replace(minute=(t.minute // minutes) * minutes if minutes < 60 else 0,
                        second=0, microsecond=0)
        if minutes >= 60:
            key = key.replace(hour=(t.hour // (minutes // 60)) * (minutes // 60))
        if key != cur_key:
            out.append(Bar(ts=key, open=b.open, high=b.high, low=b.low,
                           close=b.close, volume=b.volume))
            cur_key = key
        else:
            o = out[-1]
            o.high = max(o.high, b.high)
            o.low = min(o.low, b.low)
            o.close = b.close
            o.volume += b.volume
    return out


def svg_candles(candles: list[Bar], levels: list[tuple[float, str]] | None = None,
                width: int = 560, height: int = 300, tf_label: str = "") -> str:
    if not candles:
        return "<p class='grey'>no chart data</p>"
    levels = levels or []
    pad_l, pad_r, pad_t, pad_b = 8, 62, 8, 22
    w = width - pad_l - pad_r
    h = height - pad_t - pad_b
    lo = min(c.low for c in candles)
    hi = max(c.high for c in candles)
    for p, _ in levels:
        if p and lo * 0.97 < p < hi * 1.03:
            lo, hi = min(lo, p), max(hi, p)
    rng = max(hi - lo, 1e-9)
    lo -= rng * 0.03
    hi += rng * 0.03
    rng = hi - lo

    def y(p: float) -> float:
        return pad_t + h * (1 - (p - lo) / rng)

    n = len(candles)
    step = w / n
    bw = max(min(step * 0.65, 11.0), 1.5)

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'style="max-width:{width}px;font-family:inherit">']
    # y grid: 5 lines
    for i in range(5):
        p = lo + rng * i / 4
        yy = y(p)
        parts.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width-pad_r}" y2="{yy:.1f}" '
                     f'stroke="#eee" stroke-width="1"/>')
        parts.append(f'<text x="{width-pad_r+4}" y="{yy+3.5:.1f}" font-size="10" '
                     f'fill="#888">{p:,.2f}</text>')
    # x labels: ~5
    lab_every = max(n // 5, 1)
    for i, c in enumerate(candles):
        if i % lab_every == 0:
            t = c.ts.astimezone(ET)
            lab = t.strftime("%m/%d") if step * n > 6.5 * 60 or tf_label in ("D", "W") else t.strftime("%H:%M")
            parts.append(f'<text x="{pad_l + i*step:.1f}" y="{height-6}" font-size="10" '
                         f'fill="#888">{lab}</text>')
    # candles
    for i, c in enumerate(candles):
        x = pad_l + i * step + step / 2
        col = GREEN if c.close >= c.open else RED
        parts.append(f'<line x1="{x:.1f}" y1="{y(c.high):.1f}" x2="{x:.1f}" '
                     f'y2="{y(c.low):.1f}" stroke="{col}" stroke-width="1"/>')
        top = y(max(c.open, c.close))
        bot = y(min(c.open, c.close))
        parts.append(f'<rect x="{x-bw/2:.1f}" y="{top:.1f}" width="{bw:.1f}" '
                     f'height="{max(bot-top,1):.1f}" fill="{col}"/>')
    # level lines
    for p, label in levels:
        if not p or not (lo <= p <= hi):
            continue
        yy = y(p)
        parts.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width-pad_r}" y2="{yy:.1f}" '
                     f'stroke="#0b7285" stroke-width="1" stroke-dasharray="5,4"/>')
        parts.append(f'<text x="{pad_l+2}" y="{yy-3:.1f}" font-size="10" '
                     f'fill="#0b7285">{label}</text>')
    parts.append("</svg>")
    return "".join(parts)


TIMEFRAMES = {
    "15m": {"label": "15 min", "minutes": 15, "keep": 110},
    "1h": {"label": "1 hour", "minutes": 60, "keep": 110},
    "1d": {"label": "Daily", "interval": "1d", "range": "6mo", "keep": 90},
    "1wk": {"label": "Weekly", "interval": "1wk", "range": "2y", "keep": 80},
}


def chart_for(symbol: str, tf: str, bars_5m: list[Bar],
              levels: list[tuple[float, str]] | None = None) -> str:
    from .bars import fetch_bars
    cfg = TIMEFRAMES.get(tf, TIMEFRAMES["15m"])
    if "minutes" in cfg:
        candles = resample(bars_5m, cfg["minutes"])
    else:
        candles, _ = fetch_bars(symbol, interval=cfg["interval"], range_=cfg["range"])
        levels = None  # session levels only make sense intraday
    candles = candles[-cfg["keep"]:]
    return svg_candles(candles, levels, tf_label=cfg["label"][0].upper())
