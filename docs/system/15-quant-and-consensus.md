# 15 — Quant layer & the combined read

*Levels from volume, and why the three inputs are never averaged.*

**Applies to build:** `0.009` · **Last reviewed:** 2026-09-20

---

## 1. The three inputs

| Input | What it measures | How it fails |
|---|---|---|
| **news** | What commentators say | Opinion; lags; a loud week is not a right week |
| **X** | The same substance, faster and noisier | **Not connected** — see §5 |
| **quant** | Arithmetic over traded volume | Cannot be wrong about the past; says nothing about the future |

> **Invariant:** they are **never blended into one score**. `consensus.read()` returns
> `"score": None` and a test asserts it. A weighted average produces a confident number whose
> inputs nobody can inspect, and when it is wrong nothing says which part was wrong.

> **Invariant:** the headline is whether they **agree**. Disagreement is surfaced —
> *"news bullish vs quant neutral"* — not smoothed away. That sentence is worth reading; an
> average of the two is not.

> **Invariant:** an unavailable input reports `unavailable`, never `neutral`. An absent input
> must not get a vote.

⚠ News counts **one vote per person per horizon**, not per article. A commentator covered by
five outlets in one week is still one opinion.

## 2. The quant layer — `core/quant.py`

Per session, from the local archive: **POC**, the 70% **value area** (VAH/VAL), **VWAP**, and
the session high/low. Then one question price can actually answer: *where is price relative to
the value the market built yesterday?*

> **Invariant:** the verdict is a **lookup, not a model** — above value / inside value / below
> value is a comparison of two numbers. Nothing is fitted, trained or tuned, so it cannot drift.

> **Invariant:** a missing price or a missing prior session returns `unknown`, never a default.
> "We could not compute this" and "price is inside value" are opposite statements.

⚠ Acceptance and **POC migration** are two independent signals, reported separately. They can
disagree, and when they do that is information rather than an error to average away. Migration
needs **three** sessions — two points is a line through any two numbers.

## 3. ⛔ Bin width comes from the SESSION'S RANGE, not the price level

`derive_bin(price)` gave SPY a **0.50** bin whatever the day looked like, so a quiet 4-point
session had **eight buckets** in it. A value area computed on four buckets is blocky enough that
the POC jumps half a point at a time and the 70% edge lands wherever a boundary happens to fall.

> **Invariant:** `session_bin()` targets ~40 bins across the range **that session actually
> traded**, then snaps to the instrument's real tick — so every level is a price you could work
> an order at, never 761.8375.

Measured after the change: 41–45 bins per session on both SPY and ES.

## 4. The edge band

> **Invariant:** price must clear the value area by **8% of that area's own width** before
> "above" or "below" means anything. A fixed point count would be noise on a quiet day and
> nothing on a volatile one.

## 5. ⛔ X is not connected, and why

Every free route is closed. Probed 2026-09-20:

| Route | Result |
|---|---|
| `syndication.twitter.com` | **429** rate limited |
| `cdn.syndication.twimg.com` | 200 with **0 bytes** |
| `x.com/<user>` | 200, but a **JavaScript shell** — no tweet text in the HTML |
| nitter.net / nitter.poast.org | **dead** — connection refused / DNS failure |
| `api.x.com/2` | **401** — needs a paid tier |
| rsshub | **404** |
| Chrome extension | **blocked from the domain at policy level** |

> **Invariant:** the X component renders as *not connected* with the reason. It does not
> quietly report neutral, and it does not vote.

Connecting it means the **X API v2 paid tier**. That is a cost decision, not an engineering one.

## 6. Where it renders

`/charts` carries the combined read at the top (per horizon), the candles, then **Levels from
volume** — POC/VAH/VAL/VWAP per session for the last 10 days, computed from the archive and
never from a live fetch.
