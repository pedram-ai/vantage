# 14 — ES / SPX → SPY conversion

*Why the model never does this arithmetic, and why the ratio is dated.*

**Applies to build:** `0.006` · **Last reviewed:** 2026-09-20

---

## 1. The split

The model reports every price **exactly as the article wrote it**, plus which instrument it is
in (`quoted_in`). `core/convert.py` does the conversion.

> **Invariant:** the model never converts, never divides ES by 10, never computes a percentage.
> A wrong number is then a bug with a stack trace rather than a hallucination that reads like
> analysis. The prompt says this in those words and a test asserts the clause is present.

## 2. ⛔ The ratio is not 10

Measured live 2026-09-20: ES 7,712.50 against SPY 761.69 is **10.1255**. Dividing by 10 puts
ES 7697 at SPY 769.70 when it is really **760.16** — **9.54 SPY points out, 1.25%**.

## 3. ⛔ And it must be the ratio on the article's own date

The futures basis decays toward expiry and jumps at each quarterly roll; SPY drifts against the
index as dividends accrue. Measured across 126 trading days to 2026-09-18:

| | |
|---|---:|
| min | 10.0172 |
| max | 10.1680 |
| spread | **1.51%** |
| on ES 7697 | **11.40 SPY points** |

> **Invariant:** `ratio_on(date)` reads that date's ES and SPY closes. A weekend resolves to the
> **prior** session — the last thing the author could have seen. Applying today's ratio to the
> oldest article in the feed misplaces it by 3.17 points, which is enough to put a level on the
> wrong side of price and invert the read built on it.

> **Invariant:** the ratio used, and the date it came from, are **stored on the article** and
> printed under every card. A converted level has to be checkable later without guessing which
> number was in force.

## 4. ⛔ The band guard

> **Invariant:** a converted level outside `50 ≤ SPY ≤ 3000` is dropped, not rendered. An ES
> level that escaped conversion arrives as ~7700 and is absurd but perfectly renderable — it
> would sit in a SPY sentence looking like a price.

> **Invariant:** an unrecognised unit returns `None` rather than guessing a divisor. A level
> converted by the wrong divisor is off by a factor of ten and still looks like a price.

## 5. The unit toggle

Every price is rendered **twice** into the markup — as SPY and in the article's own unit — and
the toggle only changes which is displayed.

> **Invariant:** the browser never does the arithmetic. Re-deriving on click would use today's
> ratio rather than the article's, and the two units would quietly disagree on the same card.

⚠ The **point** move differs by unit (3.79 SPY ≈ 37.98 ES); the **percent** does not. Both are
carried.

⚠ `ratio` is a parameter, never a module global — summarising runs in a background thread while
requests render, and a shared global would let one article's ratio convert another's prices.

## 6. The level ladder

> **Invariant:** bar length is **how many articles name the level** — not conviction, not
> strength, not volume. The count is printed beside it so the encoding is checkable.

> **Invariant:** rows are selected by **proximity to the current price**, then sorted high to
> low. Selecting the top N by article count instead shows a cluster far from price and omits the
> levels you are about to trade through. A pre-truncated price-sorted list produced a one-sided
> ladder with the "now" marker at the bottom; a test carries that as a mutation proof.

## 7. ⚠ maxOutputTokens includes Gemini's reasoning

Measured on a real article: **1,414 thought tokens against 610 of answer**. At 4,096 a longer
think truncates the JSON, and the failure surfaced as "model returned non-JSON" — which sent me
looking at the schema when the cause was a budget.

> **Invariant:** the budget is 12,288, and a parse failure reports `finishReason`. A truncated
> answer and a malformed one need different fixes and must not share an error message.
