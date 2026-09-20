"""Performance charts — server-rendered SVG, no JS dependency.

Colour: profit/loss is a DIVERGING job (polarity), so two hues and a neutral
midpoint — never a categorical ramp.

⚠ The obvious green/red pair FAILS colour-vision separation: #35875c vs
#c05b4d measures ΔE 5.9 under protanopia, where the floor is 8. Validated with
the dataviz palette checker rather than guessed. The shipped pair is
**#2f9e8f / #c0563f** — a teal-leaning green against an orange-leaning red —
which passes all six checks in BOTH light and dark (worst CVD ΔE 10.6 deutan,
normal-vision 23.9, contrast ≥ 3:1).

Position is a second, redundant encoding on every chart here: bars sit above or
below a zero baseline and values carry an explicit sign, so the reading never
depends on hue alone.
"""

from __future__ import annotations

import html
from collections import defaultdict
from datetime import date, datetime

UP = "#2f9e8f"      # profit
DOWN = "#c0563f"    # loss
INK = "#6e7c8a"
GRID = "#d8dee6"
ACCENT = "#3e7c8f"


def _money(v: float) -> str:
    a = abs(v)
    s = f"{a/1000:.0f}k" if a >= 1000 else f"{a:.0f}"
    return ("-$" if v < 0 else "$") + s


def _esc(s) -> str:
    return html.escape(str(s))


def _empty(msg: str) -> str:
    return (f'<p style="color:{INK};font-size:13px;padding:18px 0;margin:0">{_esc(msg)}</p>')


# --- 1. equity curve ---------------------------------------------------------

def equity_curve(trades: list[dict], w: int = 820, h: int = 230) -> str:
    """Cumulative realized P&L. One series — no legend needed, the title names it."""
    rows = sorted((t for t in trades if t.get("closed_on")),
                  key=lambda t: t["closed_on"])
    if len(rows) < 2:
        return _empty("Not enough closed trades to plot a curve.")

    cum, pts = 0.0, []
    for t in rows:
        cum += float(t.get("pnl", 0) or 0)
        pts.append((t["closed_on"], cum))

    lo = min(min(p[1] for p in pts), 0.0)
    hi = max(max(p[1] for p in pts), 0.0)
    span = (hi - lo) or 1.0
    pl, pr, pt_, pb = 8, 62, 14, 26
    iw, ih = w - pl - pr, h - pt_ - pb

    def X(i): return pl + iw * (i / max(len(pts) - 1, 1))
    def Y(v): return pt_ + ih * (1 - (v - lo) / span)

    zero_y = Y(0.0)
    path = " ".join(f"{'M' if i == 0 else 'L'} {X(i):.1f} {Y(v):.1f}"
                    for i, (_, v) in enumerate(pts))
    end_v = pts[-1][1]
    col = UP if end_v >= 0 else DOWN
    area = (f"{path} L {X(len(pts)-1):.1f} {zero_y:.1f} L {X(0):.1f} {zero_y:.1f} Z")
    # ⚠ Tinting the whole fill by the ENDING value made a profitable stretch
    # read as a loss. The gradient switches hue exactly at the zero line, so
    # area above zero is always profit-coloured and below is always loss.
    zoff = max(0.0, min(1.0, (zero_y - pt_) / ih))

    ticks = []
    for f in (0.0, 0.5, 1.0):
        v = lo + span * f
        ticks.append(f'<line x1="{pl}" y1="{Y(v):.1f}" x2="{pl+iw}" y2="{Y(v):.1f}" '
                     f'stroke="{GRID}" stroke-width="1" opacity=".5"/>'
                     f'<text x="{pl+iw+6}" y="{Y(v)+4:.1f}" font-size="10.5" fill="{INK}">'
                     f'{_money(v)}</text>')

    lab = []
    for i in (0, len(pts) - 1):
        d = pts[i][0]
        anchor = "start" if i == 0 else "end"
        lab.append(f'<text x="{X(i):.1f}" y="{h-8}" font-size="10.5" fill="{INK}" '
                   f'text-anchor="{anchor}">{_esc(d)}</text>')

    return f'''<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px" role="img"
 aria-label="Cumulative realized profit and loss, ending {_money(end_v)}">
  <defs><linearGradient id="eqf" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="{UP}" stop-opacity=".30"/>
    <stop offset="{zoff*100:.2f}%" stop-color="{UP}" stop-opacity=".06"/>
    <stop offset="{zoff*100:.2f}%" stop-color="{DOWN}" stop-opacity=".06"/>
    <stop offset="100%" stop-color="{DOWN}" stop-opacity=".30"/></linearGradient></defs>
  {''.join(ticks)}
  <line x1="{pl}" y1="{zero_y:.1f}" x2="{pl+iw}" y2="{zero_y:.1f}" stroke="{INK}"
        stroke-width="1.2" stroke-dasharray="4,3" opacity=".7"/>
  <path d="{area}" fill="url(#eqf)"/>
  <path d="{path}" fill="none" stroke="{col}" stroke-width="2"
        stroke-linejoin="round" stroke-linecap="round"/>
  <circle cx="{X(len(pts)-1):.1f}" cy="{Y(end_v):.1f}" r="4" fill="{col}"
          stroke="#fff" stroke-width="2"/>
  <text x="{X(len(pts)-1)-6:.1f}" y="{Y(end_v)-10:.1f}" font-size="12" font-weight="650"
        fill="{col}" text-anchor="end">{_money(end_v)}</text>
  {''.join(lab)}
  <title>Cumulative realized P&amp;L across {len(pts)} closed trades</title>
</svg>'''


# --- 2. P&L by month ---------------------------------------------------------

def monthly_bars(trades: list[dict], w: int = 820, h: int = 208) -> str:
    """Diverging bars around zero. Position encodes sign redundantly."""
    by = defaultdict(float)
    for t in trades:
        d = t.get("closed_on")
        if d:
            by[d[:7]] += float(t.get("pnl", 0) or 0)
    if not by:
        return _empty("No closed trades in this period.")
    months = sorted(by)[-18:]
    vals = [by[m] for m in months]
    top = max(max(vals), 0.0)
    bot = min(min(vals), 0.0)
    span = (top - bot) or 1.0
    pl, pr, pt_, pb = 8, 62, 18, 44   # deeper foot: a negative bar's value label
    iw, ih = w - pl - pr, h - pt_ - pb   # sits below the bar and was colliding with the axis
    slot = iw / len(months)
    bw = min(slot * 0.62, 34)

    def Y(v): return pt_ + ih * (1 - (v - bot) / span)
    zero = Y(0.0)

    bars = []
    for i, m in enumerate(months):
        v = by[m]
        x = pl + slot * i + (slot - bw) / 2
        y = Y(v) if v >= 0 else zero
        bh = max(abs(Y(v) - zero), 1.5)
        c = UP if v >= 0 else DOWN
        # 4px rounded data-end anchored to the baseline
        r = min(4, bw / 2, bh)
        bars.append(
            f'<path d="{_bar_path(x, y, bw, bh, r, v >= 0)}" fill="{c}">'
            f'<title>{_esc(m)}: {_money(v)}</title></path>')
        if abs(v) / span > 0.16:   # selective direct labels only
            ly = y - 5 if v >= 0 else y + bh + 12
            bars.append(f'<text x="{x+bw/2:.1f}" y="{ly:.1f}" font-size="10" fill="{c}" '
                        f'text-anchor="middle" font-weight="600">{_money(v)}</text>')
        if i % max(len(months) // 7, 1) == 0:
            bars.append(f'<text x="{x+bw/2:.1f}" y="{h-6}" font-size="10" fill="{INK}" '
                        f'text-anchor="middle">{_esc(m[2:])}</text>')

    return f'''<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px" role="img"
 aria-label="Realized profit and loss by month">
  <line x1="{pl}" y1="{zero:.1f}" x2="{pl+iw}" y2="{zero:.1f}" stroke="{INK}"
        stroke-width="1.2" opacity=".65"/>
  <text x="{pl+iw+6}" y="{zero+4:.1f}" font-size="10.5" fill="{INK}">$0</text>
  {''.join(bars)}
</svg>'''


def _bar_path(x, y, w, h, r, up) -> str:
    """Rounded only on the data-end; square where it meets the baseline."""
    if up:
        return (f"M {x:.1f} {y+h:.1f} L {x:.1f} {y+r:.1f} Q {x:.1f} {y:.1f} {x+r:.1f} {y:.1f} "
                f"L {x+w-r:.1f} {y:.1f} Q {x+w:.1f} {y:.1f} {x+w:.1f} {y+r:.1f} "
                f"L {x+w:.1f} {y+h:.1f} Z")
    return (f"M {x:.1f} {y:.1f} L {x:.1f} {y+h-r:.1f} Q {x:.1f} {y+h:.1f} {x+r:.1f} {y+h:.1f} "
            f"L {x+w-r:.1f} {y+h:.1f} Q {x+w:.1f} {y+h:.1f} {x+w:.1f} {y+h-r:.1f} "
            f"L {x+w:.1f} {y:.1f} Z")


# --- 3. contribution by category --------------------------------------------

def category_bars(rows: list[dict], label_key: str = "key",
                  w: int = 400, h: int | None = None, cap: int = 14) -> str:
    """Horizontal diverging bars — magnitude by category, signed.

    ⛔ RANKED BY ABSOLUTE VALUE, NOT BY SIGNED P&L. The rows arrive sorted
    descending by pnl, so slicing the head kept the WINNERS and silently
    dropped the biggest losers: on the real book that hid QQQ -$240,600 and
    SQQQ -$298,641 while showing +$38k and +$31k, and the chart flatly
    contradicted the total beside it.

    If anything is still cut, the total of what was cut is DRAWN ON THE CHART.
    A truncated chart must never look complete.
    """
    rows = [r for r in rows if r.get("pnl") is not None]
    if not rows:
        return _empty("Nothing to compare yet.")
    rows = sorted(rows, key=lambda r: -abs(float(r["pnl"])))
    hidden = rows[cap:]
    rows = rows[:cap]
    hidden_total = sum(float(r["pnl"]) for r in hidden)
    # draw biggest-positive at the top, biggest-negative at the bottom
    rows = sorted(rows, key=lambda r: -float(r["pnl"]))
    rowh = 30
    foot = 22 if hidden else 0
    h = h or (len(rows) * rowh + 16 + foot)
    labw, valw = 96, 62
    iw = w - labw - valw
    m = max(abs(float(r["pnl"])) for r in rows) or 1.0
    mid = labw + iw / 2

    out = [f'<line x1="{mid:.1f}" y1="6" x2="{mid:.1f}" y2="{h-8}" stroke="{INK}" '
           f'stroke-width="1" opacity=".45"/>']
    for i, r in enumerate(rows):
        v = float(r["pnl"])
        y = 8 + i * rowh
        bw = abs(v) / m * (iw / 2 - 6)
        c = UP if v >= 0 else DOWN
        x = mid if v >= 0 else mid - bw
        rr = min(4, bw)
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(bw,1.5):.1f}" height="16" '
                   f'rx="{rr:.1f}" fill="{c}"><title>{_esc(r[label_key])}: {_money(v)}'
                   f'</title></rect>')
        out.append(f'<text x="{labw-8}" y="{y+12.5:.1f}" font-size="11.5" fill="{INK}" '
                   f'text-anchor="end">{_esc(r[label_key])}</text>')
        out.append(f'<text x="{w-6}" y="{y+12.5:.1f}" font-size="11" fill="{c}" '
                   f'text-anchor="end" font-weight="600">{_money(v)}</text>')
    if hidden:
        out.append(f'<text x="{labw-8}" y="{h-6}" font-size="10.5" fill="{INK}" '
                   f'text-anchor="end">{len(hidden)} smaller</text>')
        out.append(f'<text x="{w-6}" y="{h-6}" font-size="10.5" '
                   f'fill="{UP if hidden_total >= 0 else DOWN}" text-anchor="end">'
                   f'{_money(hidden_total)}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px" role="img" '
            f'aria-label="Profit and loss by category">{"".join(out)}</svg>')


# --- 4. where the money went -------------------------------------------------

def gross_vs_fees(realized: float, fees: float, w: int = 400, h: int = 112) -> str:
    """Gross trading result vs what fees took — the comparison that matters
    when fees are a large fraction of the outcome."""
    gross = realized + fees
    if abs(gross) < 0.01 and abs(fees) < 0.01:
        return _empty("No fee data for this period.")
    m = max(abs(gross), abs(fees), abs(realized)) or 1.0
    labw, valw = 104, 70
    iw = w - labw - valw
    out = []
    for i, (label, v, c) in enumerate((
            ("Gross result", gross, UP if gross >= 0 else DOWN),
            ("Fees paid", -abs(fees), DOWN),
            ("Net realized", realized, UP if realized >= 0 else DOWN))):
        y = 8 + i * 32
        bw = abs(v) / m * iw
        out.append(f'<rect x="{labw}" y="{y}" width="{max(bw,1.5):.1f}" height="16" rx="4" '
                   f'fill="{c}" opacity="{0.55 if i < 2 else 1}">'
                   f'<title>{_esc(label)}: {_money(v)}</title></rect>')
        out.append(f'<text x="{labw-8}" y="{y+12.5}" font-size="11.5" fill="{INK}" '
                   f'text-anchor="end">{_esc(label)}</text>')
        out.append(f'<text x="{w-6}" y="{y+12.5}" font-size="11" fill="{c}" '
                   f'text-anchor="end" font-weight="600">{_money(v)}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px" role="img" '
            f'aria-label="Gross result, fees paid, and net realized">{"".join(out)}</svg>')
