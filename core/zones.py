"""Zone construction — the Action Map (handoff §4.3).

Inputs: price now P, prior RTH stats S1, session before S2, overnight ON,
author levels, weekly bands. Output: the zone stack with triggers, targets,
stops, verdict and distances.

Per §11's lesson, profile-derived candidates carry extra weight (the
profile band 7675-7691 beat the author band on Sep 18, 2026).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .profile import SessionStats, classify_open

CLUSTER_PTS = 10.0
PROFILE_WEIGHT = 1.5   # §2.1: profile levels outrank author levels
AUTHOR_WEIGHT = 1.0

INTERNALS = {
    "dip": "TICK > -400, TRIN < 1",
    "fade": "TICK fails above +700",
    "breakout": "TICK > 0, +700 prints",
    "breakdown": "TICK < 0, TRIN > 1",
}


@dataclass
class Candidate:
    price: float
    source: str      # "profile" | "tictoc" | "smashelito" | "weekly"
    label: str
    weight: float = 1.0


@dataclass
class Band:
    lo: float
    hi: float
    weight: float
    sources: list[str]
    labels: list[str]

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2

    def to_dict(self) -> dict:
        return {"lo": self.lo, "hi": self.hi, "weight": round(self.weight, 2),
                "sources": self.sources, "labels": self.labels}


def _cluster(cands: list[Candidate]) -> list[Band]:
    if not cands:
        return []
    cands = sorted(cands, key=lambda c: c.price)
    bands: list[Band] = []
    cur = [cands[0]]
    for c in cands[1:]:
        if c.price - cur[-1].price <= CLUSTER_PTS:
            cur.append(c)
        else:
            bands.append(_mk_band(cur))
            cur = [c]
    bands.append(_mk_band(cur))
    return bands


def _mk_band(group: list[Candidate]) -> Band:
    srcs = sorted({c.source for c in group})
    weight = sum({
        s: (PROFILE_WEIGHT if s == "profile" else AUTHOR_WEIGHT)
        for s in srcs
    }.values())
    return Band(
        lo=min(c.price for c in group),
        hi=max(c.price for c in group),
        weight=weight,
        sources=srcs,
        labels=[c.label for c in group],
    )


def _pick_band(bands: list[Band], min_weight: float = 2.0) -> Band | None:
    """Heaviest qualifying band nearest to price-ordering (bands arrive
    sorted by distance from P). Tie-break: profile presence, then order."""
    qual = [b for b in bands if b.weight >= min_weight]
    pool = qual or bands
    if not pool:
        return None
    return sorted(
        pool,
        key=lambda b: (-b.weight, 0 if "profile" in b.sources else 1, bands.index(b)),
    )[0]


def build_action_map(price: float,
                     s1: SessionStats,
                     s2: SessionStats | None,
                     on: SessionStats | None,
                     author_levels: list[dict],
                     is_quarterly_opex: bool = False) -> dict:
    """author_levels rows: {author, kind, price, label, direction}."""

    res_c: list[Candidate] = []
    sup_c: list[Candidate] = []

    def add(price_, source, label):
        (res_c if price_ > price else sup_c).append(Candidate(price_, source, label))

    # profile candidates
    add(s1.vah, "profile", f"prior VAH {s1.vah}")
    add(s1.val, "profile", f"prior VAL {s1.val}")
    add(s1.poc, "profile", f"prior POC {s1.poc}")
    if s2:
        add(s2.poc, "profile", f"prior-prior POC {s2.poc}")
    if on:
        add(on.high, "profile", f"overnight high {on.high}")
        add(on.low, "profile", f"overnight low {on.low}")
    for hvn in (s1.hvns or [])[:4]:
        add(float(hvn), "profile", f"HVN {hvn}")

    # author candidates
    weekly_up: list[float] = []
    weekly_dn: list[float] = []
    weekly_ext_hi = weekly_ext_lo = None
    fdt = fut = None
    for lv in author_levels:
        p = float(lv["price"])
        kind = lv.get("kind", "")
        author = lv.get("author", "author").lower()
        src = "tictoc" if "tic" in author else ("smashelito" if "smash" in author else "weekly")
        label = lv.get("label") or f"{lv.get('author', '')} {kind} {p}"
        if kind in ("weekly_target_up",):
            weekly_up.append(p); src = "weekly"
        if kind in ("weekly_target_down",):
            weekly_dn.append(p); src = "weekly"
        if kind == "weekly_extreme_hi":
            weekly_ext_hi = p; src = "weekly"
        if kind == "weekly_extreme_lo":
            weekly_ext_lo = p; src = "weekly"
        if kind == "fut":
            fut = p
        if kind == "fdt":
            fdt = p
        add(p, src, label)

    res_bands = _cluster(res_c)
    res_bands.sort(key=lambda b: b.lo)          # nearest above first
    sup_bands = _cluster(sup_c)
    sup_bands.sort(key=lambda b: -b.hi)         # nearest below first

    fade = _pick_band(res_bands)
    dip = _pick_band(sup_bands)

    # invalidation: next distinct support below the dip band
    invalidation = None
    if dip:
        below = [b for b in sup_bands if b.hi < dip.lo]
        if below:
            invalidation = below[0].hi
        elif fdt:
            invalidation = fdt
    # targets
    def two_above(x):
        ups = sorted({c.price for c in res_c if c.price > x})
        ups += sorted(w for w in weekly_up if w > x)
        ups = sorted(set(ups))
        return ups[:2] if ups else []

    def two_below(x):
        dns = sorted({c.price for c in sup_c if c.price < x}, reverse=True)
        return dns[:2] if dns else []

    open_type = classify_open(price, s1)
    preferred_fade_dip = open_type["code"] == "A" or is_quarterly_opex

    zones = {}
    if fade:
        zones["fade"] = {
            "band": fade.to_dict(),
            "trigger": f"tag {fade.lo:g}-{fade.hi:g}, reject",
            "internals": INTERNALS["fade"],
            "targets": two_below(fade.lo), "stop": fade.hi + 5,
            "preferred": preferred_fade_dip,
        }
        zones["breakout"] = {
            "above": fade.hi,
            "trigger": f"hold > {fade.hi:g}, 15 min",
            "internals": INTERNALS["breakout"],
            "targets": two_above(fade.hi),
            "stop": fade.lo,
            "cap": weekly_ext_hi,
            "low_odds": preferred_fade_dip,
        }
    if dip:
        t_up = [x for x in (s1.poc, s1.vah) if x > dip.hi][:2] or two_above(dip.hi)
        zones["dip"] = {
            "band": dip.to_dict(),
            "trigger": f"dip into {dip.lo:g}-{dip.hi:g}, hold",
            "internals": INTERNALS["dip"],
            "targets": t_up, "stop": (invalidation or dip.lo) - 5,
            "preferred": preferred_fade_dip,
        }
    if invalidation:
        zones["breakdown"] = {
            "below": invalidation,
            "trigger": f"hold < {invalidation:g}, 15 min",
            "internals": INTERNALS["breakdown"],
            "targets": two_below(invalidation),
            "stop": dip.hi if dip else invalidation + 10,
            "cap": weekly_ext_lo,
            "low_odds": preferred_fade_dip,
        }

    # verdict
    verdict = "NO TRADE - wait for a zone"
    zone_state = "none"
    if dip and dip.lo <= price <= dip.hi:
        verdict, zone_state = "IN CALL ZONE - buy the dip", "dip"
    elif fade and fade.lo <= price <= fade.hi:
        verdict, zone_state = "IN PUT ZONE - fade the band", "fade"
    elif fade and price > fade.hi:
        verdict, zone_state = "IN CALL ZONE - breakout", "breakout"
    elif invalidation and price < invalidation:
        verdict, zone_state = "IN PUT ZONE - breakdown", "breakdown"
    elif dip and invalidation and invalidation <= price < dip.lo:
        verdict, zone_state = "STAND ASIDE - between dip band and invalidation", "stand_aside"

    distances = {}
    if fade:
        distances["put_zone_above"] = {
            "pts": round(fade.lo - price, 2), "range": [fade.lo, fade.hi]}
    if dip:
        distances["call_zone_below"] = {
            "pts": round(price - dip.hi, 2), "range": [dip.lo, dip.hi]}

    # overnight tag/reject annotation (§4.3.12)
    notes = []
    if on and fade and on.high >= fade.lo and on.close < fade.lo:
        notes.append(f"overnight high {on.high:g} tagged the fade band and rejected")
    if on and dip and on.low <= dip.hi and on.close > dip.hi:
        notes.append(f"overnight low {on.low:g} tagged the dip band and held")

    return {
        "price": price,
        "verdict": verdict,
        "zone_state": zone_state,
        "open_type": open_type,
        "zones": zones,
        "distances": distances,
        "notes": notes,
        "resistance_bands": [b.to_dict() for b in res_bands],
        "support_bands": [b.to_dict() for b in sup_bands],
        "quarterly_opex": is_quarterly_opex,
    }
