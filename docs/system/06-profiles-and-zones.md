# 06 — Profiles & zones

*The volume-profile math, and how a day's action map is built from it.*

**Applies to build:** `0.002` · **Last reviewed:** 2026-09-20

---

## 1. What a session profile is

For a given session window, `core/profile.py` bins every bar's volume by price and derives:

| Level | Meaning |
|---|---|
| **POC** | Point of Control — the single price with the most traded volume |
| **VAH / VAL** | The high and low of the **value area**: the contiguous 70% of volume around the POC |
| **VWAP** | Volume-weighted average price over the window |

Definitions shown to the operator live in `core/glossary.py` and render as hover text.

> **Invariant:** glossary terms are added in `core/glossary.py`, never inline in a template.
> A definition duplicated into a page drifts from the one beside it.

## 2. The histogram

`_histogram(bars, bw)` returns a map of **bin lower edge → volume**, as floats. Bin width comes
from the instrument ([05](./05-instruments-and-sessions.md) §3).

## 3. Value area construction

Start at the POC bin and expand outward, each step taking whichever neighbouring bin holds more
volume, until 70% of session volume is enclosed. VAH and VAL are the outer edges of that span.

## 4. Zones and the action map

`core/zones.py` turns prior-session levels plus published author levels into labelled zones — dip,
fade, breakout, breakdown — each with a price band and a rationale.

`PROFILE_WEIGHT = 1.5` deliberately favours the profile-derived band over the author band.

> **Invariant:** that weight is not a tuning knob to adjust casually. It encodes a specific
> observed outcome (2026-09-18, where the profile-derived dip band was the one that worked) and
> changing it changes every historical comparison.

## 5. Regression test

`tests/test_regression.py` reproduces the 24 reference values from the original handoff §11
(2026-09-16 to 09-18) while Yahoo's 5-minute window still reaches those dates; afterwards it falls
back to structural invariants.

All 24 matched exactly on 2026-09-18.

> **Invariant:** when the 5-minute window rolls past the reference dates, the test degrades to
> invariants rather than silently passing on no data. A test that cannot fail is decoration.

## 6. Author levels

Published levels are entered by hand at `/levels` (reached from Admin → Data sources). They are
inputs to the zone builder, and the map states which levels it used.
