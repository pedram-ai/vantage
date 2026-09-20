# 08 — Performance & charts

*Periods, the read cache, and the rule every chart on this page must obey.*

**Applies to build:** `0.002` · **Last reviewed:** 2026-09-20

---

## 1. Periods

today · yesterday · this/last week · this/last month · this/last quarter · this/last year ·
last 12 months · all time · custom. Each is a date range over `trades.closed_on`.

> **Invariant:** `trades_between` bounds are **inclusive at both ends**, matching the Firestore
> `>= / <=` query it replaced. It relies on `closed_on` being a `YYYY-MM-DD` **string** so string
> order is date order; a datetime there would silently return the wrong period.

## 2. ⛔⛔ A chart may not hide a row without saying so

The by-instrument chart drew `rows[:8]` from a list sorted by **signed** P&L — so it kept the
winners and dropped the losers. On the real book it displayed SPY +$38k and UCO +$31k while
omitting:

| hidden | |
|---|---:|
| QQQ | −$240,600 |
| SQQQ | −$298,641 |
| TQQQ | −$36,247 |
| **total hidden** | **−$575,488** |

The page beside it read **−$519,390**. Neither number was flagged.

> **Invariant:** `category_bars` ranks by **absolute** value, and anything still folded has its
> count and its summed remainder drawn on the chart. A truncated chart must never look complete.

`tests/test_portfolio_cache.py` reconciles the drawn bars against the period total and carries a
mutation proof that reintroduces the original slice.

## 3. ⚠ The equity fill switches hue at the zero line

Tinting the whole filled area by the **ending** value rendered a genuinely profitable stretch in
the loss colour. The gradient now changes at zero.

## 4. ⚠ The obvious green/red pair fails colour-vision separation

`#35875c` vs `#c05b4d` is ΔE **5.9** under protanopia against a floor of 8 — validated with a
palette checker, not judged by eye.

Shipped pair: **`#2f9e8f` / `#c0563f`** — all six checks pass in light and dark, worst CVD ΔE 10.6
(deutan), normal-vision 23.9.

> **Invariant:** position is a redundant encoding everywhere. Bars sit above or below zero and
> every value carries a sign, so colour is never the only channel.

## 5. The AI panel was removed

A `claude-opus-5` panel wrote observations from the aggregate figures. Removed 2026-09-20.

Profiled per page load: the API key lookup cost **2,870 ms** (two uncached Secret Manager round
trips) and the call then took ~600 ms to be rejected by a revoked key — **3.5 s on every render**,
to restate figures already on the page.

> **Invariant:** no Anthropic dependency, import, key lookup or panel. A test greps the tree.

## 6. The read cache

Every period click used to re-stream the whole `trades` and `cash` collections from Firestore and
recompute: **3,507 ms**. They only change on import, and 771 trades fit in memory.

`portfolio._all(name)` caches each collection (TTL 600 s) and returns **copies**, so a caller
cannot corrupt it. Measured after: **1–3 ms**.

> **Invariant:** `portfolio.invalidate()` is called by the importer. The TTL is a backstop for a
> writer somebody forgot to wire up, not the mechanism.

The cache is primed by a daemon thread at startup so the first page load does not pay the read.

## 7. Pagination

50 trades per page.

> **Invariant:** `perf["trades"]` is the current page; **`perf["all_trades"]` is the full period**
> and is what the charts read. Paging the table must not reshape the analysis beside it. A test
> pages the whole set and asserts 771 rows appear exactly once each.

## 8. "By setup"

The action-map zone a trade was taken on — dip, fade, breakout, breakdown.

Every imported trade reads **"Outside the map"**, because Schwab's export carries no zone and
Argent Ridge did not exist when those trades were placed.

> **Invariant:** the page states that plainly rather than drawing a one-bar chart. A single
> category labelled "Outside the map" reads as a verdict on the trading rather than an absence of
> data. It fills in going forward.
