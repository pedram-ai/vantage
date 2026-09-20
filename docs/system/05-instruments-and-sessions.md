# 05 — Instruments & sessions

*Why a futures session window is wrong for a stock, and what generalising cost.*

**Applies to build:** `0.002` · **Last reviewed:** 2026-09-20

---

## 1. Instruments are data, not code

`core/instruments.py` is the registry. An instrument carries its kind, session spec, histogram bin
width, symbol mapping, and whether author levels exist for it.

`resolve()` never raises — an unknown ticker is treated as a US equity.

> **Invariant:** adding a symbol is a watchlist row, not a release.

## 2. ⛔ Session windows are per-instrument

| Spec | RTH | Before RTH | Note |
|---|---|---|---|
| `CME_FUTURES` | 09:30–16:00 ET | **18:00 the previous day** → 09:30 | labelled "Overnight" |
| `US_EQUITY` | 09:30–16:00 ET | **04:00 the same day** → 09:30 | labelled "Pre-market" |

> **Invariant:** equities have **no overnight session**. Applying the futures window to SPY sweeps
> in the prior afternoon's post-market trade and labels it "overnight" — a profile built from the
> wrong hours, with nothing on screen to indicate it.

Guarded in `tests/test_instruments.py`.

All session arithmetic is **ET** (`America/New_York`). Everything shown to the operator is **PT**.

## 3. ⛔ Histogram bin width is per-instrument

`_histogram(bars, bw)` takes the bin width as a parameter.

> **Invariant:** a fixed 1-point bin is right for ES at 7,700 and collapses a $40 stock into four
> buckets. `derive_bin()` scales with price (~0.05%, snapped to a human step).

The four-bucket case is measured in the tests, not asserted from reasoning.

## 4. The 2026-09-19 redesign

The platform stopped being an ES email tool. Navigation became **Today · Markets · Positions ·
Performance**, with Settings moving under Admin on 2026-09-20.

- **History** and **Levels** left the top nav. "History" meant two unrelated things — archived
  briefings and the trading record — so briefings live under Today and the record is Performance.
  Levels is a property of an instrument, reached from Admin → Data sources.
- `core/symbol_view.py` replaced `run_builder` for the UI. **`run_builder` is still the email
  path** (ES-only) and is untouched, because the email guard test gates it.

## 5. Watchlists

`core/watchlists.py`. Any number of lists, plus two **automatic** ones — `__held__` and
`__traded__` — computed on read from the portfolio.

> **Invariant:** the automatic lists are never stored. A stored copy of "what I hold" drifts from
> what is actually held, and nothing goes red when it does.
