"""The Argent Ridge mark.

Concept: *argent* is silver in heraldry and a *ridge* is a mountain skyline —
which is also the shape of a price chart. The mark is one form doing both jobs:
a silver-to-teal ridge that climbs slowly and drops fast, the silhouette of a
breakout.

A marker dot once sat on the summit. It was cut: at 16 px it fused with the
apex into a white spike. Special-casing it by size would have meant the SVG and
the PNG were different shapes — the precise failure this module is built to
avoid — so the dot lost.

Two peaks, not three: at 16 px a third peak turns to mush. Everything here is
drawn from ONE geometry table, so the favicon and the header logo are the same
shape by construction — there is deliberately no outline stroke, because a
stroke the SVG had and the rasteriser did not would make them silently differ.

⛔ No rasteriser is installed (no Pillow, no cairosvg). The PNG/ICO are written
directly with zlib — a flat-colour icon needs nothing more, and adding an image
dependency to ship a favicon would be a poor trade.
"""

from __future__ import annotations

import struct
import zlib

# Palette — the app's accent, plus the silver the name asks for.
TEAL = (62, 124, 143)      # #3e7c8f  the app accent
TEAL_DEEP = (36, 82, 97)   # #245261  shadowed face of the ridge
SILVER = (203, 213, 221)   # #cbd5dd  argent
SUMMIT = (245, 250, 252)   # #f5fafc  the marker on the peak

# Ridge geometry in a 0..64 box. Baseline at y=50.
# Left peak lower, right peak higher — the silhouette rises to the right.
BASE_Y = 50.0
# One foothill, one saddle, one dominant peak right of centre, then a short
# steep fall. Asymmetry is what stops it reading as a generic mountain: the
# silhouette climbs slowly and drops fast, the shape of a breakout.
RIDGE = [(2, 51), (15, 34), (23, 42), (38, 12), (49, 29), (62, 51)]
SUMMIT_PT = (38, 12)   # apex, used for the gradient origin


def svg(size: int = 64, bg: str | None = "#0f1a20", rounded: bool = True) -> str:
    """The mark as SVG. `bg=None` gives a transparent mark for the header."""
    pts = " ".join(f"{x},{y}" for x, y in RIDGE)
    r = size * 0.22 if rounded else 0
    bg_rect = (f'<rect width="64" height="64" rx="{64*0.22 if rounded else 0:.1f}" '
               f'fill="{bg}"/>') if bg else ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="{size}" height="{size}" role="img" aria-label="Argent Ridge">
  <defs>
    <linearGradient id="ar-face" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="rgb{SILVER}"/>
      <stop offset="100%" stop-color="rgb{TEAL}"/>
    </linearGradient>
  </defs>
  {bg_rect}
  <polygon points="{pts}" fill="url(#ar-face)"/>
</svg>'''


# --- rasteriser ------------------------------------------------------------
# Scanline fill of the ridge polygon + the summit marker, supersampled 3x for
# clean edges. Small enough to be obviously correct.

def _blend(dst, src, a):
    return tuple(round(d + (s - d) * a) for d, s in zip(dst, src))


def _raster(size: int, bg: tuple[int, int, int] | None, ss: int = 3) -> list[list[tuple]]:
    n = size * ss
    scale = n / 64.0
    # accumulate coverage in the supersampled grid, then box-down
    grid = [[(0, 0, 0, 0.0) for _ in range(n)] for _ in range(n)]
    poly = [(x * scale, y * scale) for x, y in RIDGE]
    corner = 0.22 * n

    for py in range(n):
        yc = py + 0.5
        # polygon scanline crossings
        xs = []
        for i in range(len(poly)):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % len(poly)]
            if (y1 <= yc < y2) or (y2 <= yc < y1):
                if y2 != y1:
                    xs.append(x1 + (yc - y1) * (x2 - x1) / (y2 - y1))
        xs.sort()
        spans = [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)]
        for px in range(n):
            xc = px + 0.5
            # rounded-rect background
            col, alpha = (0, 0, 0), 0.0
            if bg is not None:
                inside_bg = True
                for ox, oy in ((corner, corner), (n - corner, corner),
                               (corner, n - corner), (n - corner, n - corner)):
                    if ((xc < corner and yc < corner and (ox, oy) == (corner, corner)) or
                        (xc > n - corner and yc < corner and (ox, oy) == (n - corner, corner)) or
                        (xc < corner and yc > n - corner and (ox, oy) == (corner, n - corner)) or
                        (xc > n - corner and yc > n - corner and (ox, oy) == (n - corner, n - corner))):
                        if (xc - ox) ** 2 + (yc - oy) ** 2 > corner ** 2:
                            inside_bg = False
                if inside_bg:
                    col, alpha = bg, 1.0
            # ridge body: vertical gradient silver -> teal
            if any(a <= xc <= b for a, b in spans):
                t = max(0.0, min(1.0, (yc - SUMMIT_PT[1] * scale)
                                 / (BASE_Y * scale - SUMMIT_PT[1] * scale)))
                face = tuple(round(SILVER[k] + (TEAL[k] - SILVER[k]) * t) for k in range(3))
                col, alpha = (_blend(col, face, 1.0) if alpha else face), 1.0
            grid[py][px] = (*col, alpha)

    out = [[(0, 0, 0, 0) for _ in range(size)] for _ in range(size)]
    f = ss * ss
    for y in range(size):
        for x in range(size):
            rs = gs = bs = a_s = 0.0
            for dy in range(ss):
                for dx in range(ss):
                    r_, g_, b_, a_ = grid[y * ss + dy][x * ss + dx]
                    rs += r_ * a_; gs += g_ * a_; bs += b_ * a_; a_s += a_
            if a_s > 0:
                out[y][x] = (round(rs / a_s), round(gs / a_s), round(bs / a_s),
                             round(255 * a_s / f))
    return out


def png(size: int = 180, bg: tuple[int, int, int] | None = (15, 26, 32)) -> bytes:
    px = _raster(size, bg)
    raw = b"".join(b"\x00" + b"".join(struct.pack("4B", *px[y][x]) for x in range(size))
                   for y in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def ico(size: int = 32) -> bytes:
    """ICO wrapping a PNG — valid since Vista and far simpler than BMP+mask."""
    data = png(size, bg=(15, 26, 32))
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                        len(data), 22)
    return header + entry + data


# --- inline data URIs -------------------------------------------------------
# ⛔⛔ THE FAVICON MUST NOT BE A SEPARATE REQUEST. IAP fronts this service and
# gates EVERY path, so a browser fetching /favicon.ico gets a 302 to Google
# sign-in and renders HTML as an image — i.e. no icon at all. Verified live
# 2026-09-20: /favicon.ico, /icon-32.png and /apple-touch-icon.png all returned
# `302 text/html`.
#
# Inlining sidesteps it entirely: the icon arrives inside the page the browser
# has already been authorised to load. The routes stay for anything that asks
# for them directly, but the <link> tags no longer depend on them.
#
# ⚠ Built ONCE at import. The mark is fixed at build time and rasterising a
# 180px PNG per request would be absurd.

import base64 as _b64
from urllib.parse import quote as _q

_URIS: dict[str, str] = {}


def data_uris() -> dict:
    """{'svg':…, 'ico':…, 'png180':…} as data: URIs. Never raises."""
    if _URIS:
        return dict(_URIS)
    try:
        # SVG goes in percent-encoded — smaller than base64 for markup and it
        # stays readable in view-source.
        _URIS["svg"] = "data:image/svg+xml," + _q(svg(64), safe="")
        _URIS["ico"] = ("data:image/x-icon;base64,"
                        + _b64.b64encode(ico(32)).decode())
        _URIS["png180"] = ("data:image/png;base64,"
                           + _b64.b64encode(png(180)).decode())
    except Exception:  # noqa: BLE001
        _URIS.clear()
    return dict(_URIS)
