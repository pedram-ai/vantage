"""The expectation strip — one consistent picture per article.

A horizontal SPY price axis with:
  * a tick for the level the article treats as "here" when it was written,
  * where it says price goes, as an arrow with the move in points and percent,
  * a shaded band when the call is a range rather than a direction,
  * today's SPY, so an old article is visibly old.

⛔ EVERY MARK IS A NUMBER THE READ ACTUALLY CARRIES. Nothing is inferred to
make the picture look complete: a missing target draws no arrow, a missing
reference draws no tick, and an article with neither draws the axis and says
so. A chart that invents a mark to avoid looking empty is the whole failure
this codebase keeps guarding against.

⚠ Colour is never the only channel — direction is also the arrow's heading and
the sign on the label, so the strip survives greyscale and colour blindness.
The pair is the validated one from perf_charts (teal-green / orange-red).
"""

from __future__ import annotations

UP = "#2f9e8f"
DOWN = "#c0563f"
FLAT = "#6e7c8a"
INK = "#26313d"
MUTED = "#6e7c8a"
FAINT = "#93a1b0"
LINE = "#e6eaef"
BAND = "#3e7c8f"

W = 520
H = 92
PAD = 46


def _span(values: list[float]) -> tuple[float, float]:
    """Axis bounds with a little air, never zero-width."""
    lo, hi = min(values), max(values)
    if hi - lo < 0.5:
        mid = (hi + lo) / 2
        lo, hi = mid - 1.5, mid + 1.5
    pad = (hi - lo) * 0.18
    return lo - pad, hi + pad


def _fmt(v: float) -> str:
    return f"{v:,.2f}"


def _orig(v: float | None, unit: str, ratio: dict | None) -> str:
    """The same level in the article's own units, for the toggle.

    ⛔ BOTH VALUES ARE RENDERED INTO THE MARKUP and the toggle swaps which is
    shown. Re-deriving on click would mean the browser doing the arithmetic —
    with today's ratio, not the article's — and the two units would quietly
    disagree with each other on the same card.

    ⚠ `ratio` is PASSED, never held in a module global. Summarising runs in a
    background thread while requests render, and a shared global would let one
    article's ratio convert another article's prices.
    """
    from . import convert
    if v is None:
        return ""
    u = (unit or "").upper()
    if u == "SPY" or u not in ("ES", "SPX", "MES", "/ES"):
        return _fmt(v)
    back = convert.spy_to(v, u, ratio)
    return _fmt(back) if back is not None else _fmt(v)


def _px(v: float, unit: str, ratio: dict | None = None) -> str:
    """A price span carrying both units. The toggle reads the data attributes."""
    return (f'<tspan class="px" data-spy="{_fmt(v)}" '
            f'data-orig="{_orig(v, unit, ratio)}">{_fmt(v)}</tspan>')


def strip(leg: dict, ref: float | None, spy_now: float | None = None,
          label: str = "", unit: str = "SPY", ratio: dict | None = None) -> str:
    """One horizon (24h or the week) as an SVG strip. Never raises.

    Every price is emitted twice — as SPY and in the article's own unit — so
    the page can switch between them without another request and without the
    browser recomputing anything.
    """
    leg = leg or {}
    target = leg.get("spy_target")
    low = leg.get("spy_low")
    high = leg.get("spy_high")
    shape = leg.get("shape") or "not stated"

    marks = [v for v in (ref, target, low, high, spy_now) if isinstance(v, (int, float))]
    if not marks or ref is None:
        return _empty(label, "no level stated")

    lo, hi = _span(marks)
    def x(v: float) -> float:
        return PAD + (v - lo) / (hi - lo) * (W - 2 * PAD)

    y = 52.0
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px" role="img" '
           f'aria-label="{label} expectation">']

    # the range band, drawn first so marks sit on top
    if low is not None and high is not None and high > low:
        out.append(f'<rect x="{x(low):.1f}" y="{y-16:.1f}" width="{x(high)-x(low):.1f}" '
                   f'height="32" rx="5" fill="{BAND}" opacity="0.13"/>')
        for v, anchor in ((low, "end"), (high, "start")):
            out.append(f'<line x1="{x(v):.1f}" y1="{y-16:.1f}" x2="{x(v):.1f}" '
                       f'y2="{y+16:.1f}" stroke="{BAND}" stroke-width="1.5" opacity="0.55"/>')
            out.append(f'<text x="{x(v):.1f}" y="{y+30:.1f}" font-size="10" fill="{BAND}" '
                       f'text-anchor="middle">{_px(v, unit, ratio)}</text>')

    # the axis
    out.append(f'<line x1="{PAD}" y1="{y:.1f}" x2="{W-PAD}" y2="{y:.1f}" '
               f'stroke="{LINE}" stroke-width="2"/>')

    # today's SPY, for age
    if spy_now is not None:
        out.append(f'<line x1="{x(spy_now):.1f}" y1="{y-21:.1f}" x2="{x(spy_now):.1f}" '
                   f'y2="{y+21:.1f}" stroke="{FAINT}" stroke-width="1.2" '
                   f'stroke-dasharray="2 3"/>')
        out.append(f'<text x="{x(spy_now):.1f}" y="{y-26:.1f}" font-size="9.5" '
                   f'fill="{FAINT}" text-anchor="middle">now {_px(spy_now, unit, ratio)}</text>')

    # the reference level
    out.append(f'<circle cx="{x(ref):.1f}" cy="{y:.1f}" r="4.5" fill="{INK}"/>')
    out.append(f'<text x="{x(ref):.1f}" y="{y+30:.1f}" font-size="10.5" fill="{INK}" '
               f'text-anchor="middle" font-weight="600">{_px(ref, unit, ratio)}</text>')
    out.append(f'<text x="{x(ref):.1f}" y="{y-12:.1f}" font-size="9" fill="{MUTED}" '
               f'text-anchor="middle">at writing</text>')

    # the arrow
    if target is not None and abs(target - ref) > 0.01:
        up = target > ref
        c = UP if up else DOWN
        x0, x1 = x(ref), x(target)
        # leave room for the head
        head = 7.0 if up else -7.0
        out.append(f'<line x1="{x0:.1f}" y1="{y:.1f}" x2="{x1 - head:.1f}" y2="{y:.1f}" '
                   f'stroke="{c}" stroke-width="2.6" stroke-linecap="round"/>')
        tip = x1
        back = tip - head
        out.append(f'<path d="M{tip:.1f},{y:.1f} L{back:.1f},{y-5.4:.1f} '
                   f'L{back:.1f},{y+5.4:.1f} Z" fill="{c}"/>')
        out.append(f'<text x="{x1:.1f}" y="{y+30:.1f}" font-size="10.5" fill="{c}" '
                   f'text-anchor="middle" font-weight="650">{_px(target, unit, ratio)}</text>')
        pts = leg.get("move_pts")
        pct = leg.get("move_pct")
        if pts is not None and pct is not None:
            mid = (x0 + x1) / 2
            arrow = "▲" if up else "▼"
            # ⚠ The POINT move differs by unit (3.76 SPY == ~38 ES); the
            # PERCENT does not. Both are carried so the toggle stays truthful.
            opts = abs(pts)
            if (unit or "").upper() in ("ES", "SPX", "MES", "/ES") and ratio:
                opts = abs(pts) * ratio.get("es_spy", 1.0)
            out.append(f'<text x="{mid:.1f}" y="{y-12:.1f}" font-size="10.5" fill="{c}" '
                       f'text-anchor="middle" font-weight="650">{arrow} '
                       f'<tspan class="px" data-spy="{abs(pts):,.2f}" '
                       f'data-orig="{opts:,.2f}">{abs(pts):,.2f}</tspan>'
                       f' ({pct:+.2f}%)</text>')
    elif shape == "range":
        out.append(f'<text x="{W/2:.1f}" y="{y-12:.1f}" font-size="10.5" fill="{BAND}" '
                   f'text-anchor="middle" font-weight="620">range</text>')

    out.append("</svg>")
    return "".join(out)


def _empty(label: str, why: str) -> str:
    return (f'<svg viewBox="0 0 {W} 46" width="100%" style="max-width:{W}px" role="img" '
            f'aria-label="{label}: {why}">'
            f'<line x1="{PAD}" y1="26" x2="{W-PAD}" y2="26" stroke="{LINE}" '
            f'stroke-width="2"/>'
            f'<text x="{W/2}" y="20" font-size="10.5" fill="{FAINT}" '
            f'text-anchor="middle">{why}</text></svg>')


def bias_bar(bias: str, conviction: str) -> str:
    """A small, consistent bullish/bearish meter.

    ⚠ Conviction is the model's own word, not a probability. It is drawn as
    one of three discrete steps and labelled, never as a percentage — a number
    here would imply a precision nobody measured.
    """
    steps = {"low": 1, "medium": 2, "high": 3}.get((conviction or "").lower(), 0)
    c = {"bullish": UP, "bearish": DOWN}.get((bias or "").lower(), FLAT)
    arrow = {"bullish": "▲", "bearish": "▼"}.get((bias or "").lower(), "■")
    pips = "".join(
        f'<rect x="{34 + i*11}" y="5" width="8" height="12" rx="2" '
        f'fill="{c}" opacity="{1.0 if i < steps else 0.18}"/>' for i in range(3))
    return (f'<svg viewBox="0 0 72 22" width="72" height="22" role="img" '
            f'aria-label="{bias}, {conviction} conviction">'
            f'<text x="0" y="16" font-size="13" fill="{c}" font-weight="700">{arrow}</text>'
            f'{pips}</svg>')


def ladder(levels: list[dict], spy_now: float | None, unit: str = "SPY",
           ratio: dict | None = None, rows: int = 11) -> str:
    """Clustered levels stacked around the live price, as HTML.

    ⛔ THE BAR LENGTH IS HOW MANY ARTICLES NAME THE LEVEL — nothing else. It is
    not conviction, not strength, not volume. The count is printed beside it so
    the encoding is checkable rather than trusted.

    ⚠ Rows are chosen by proximity to the CURRENT price, then re-sorted high to
    low. Taking the top N by article-count instead would show a cluster of
    levels far from price and omit the ones you are about to trade through.
    """
    if not levels:
        return '<p class="faint">No levels named yet.</p>'
    if spy_now is not None:
        near = sorted(levels, key=lambda r: abs(r["spy"] - spy_now))[:rows]
    else:
        near = sorted(levels, key=lambda r: -r["n"])[:rows]
    near = sorted(near, key=lambda r: -r["spy"])
    top = max((r["n"] for r in near), default=1) or 1

    out = ['<div class="ladder">']
    placed = spy_now is None
    for r in near:
        if not placed and r["spy"] < spy_now:
            out.append(_now_rung(spy_now, unit, ratio))
            placed = True
        pct = max(8, round(r["n"] / top * 100))
        out.append(
            f'<div class="rung {r.get("kind","other")}">'
            f'<span class="px-cell">{_px(r["spy"], unit, ratio)}</span>'
            f'<span class="wtwrap"><i class="wt" style="width:{pct}%"></i>'
            f'<span class="n">{r["n"]}</span></span></div>')
    if not placed:
        out.append(_now_rung(spy_now, unit, ratio))
    out.append("</div>")
    return "".join(out)


def _now_rung(spy_now: float, unit: str, ratio: dict | None) -> str:
    return (f'<div class="rung now"><span class="px-cell">'
            f'{_px(spy_now, unit, ratio)}</span>'
            f'<span class="nowlab">SPY now</span></div>')
