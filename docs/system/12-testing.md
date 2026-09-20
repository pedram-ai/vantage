# 12 — Testing

*Nine suites, and why "it compiles" proves nothing about whether a feature works.*

**Applies to build:** `0.002` · **Last reviewed:** 2026-09-20

---

## 1. The rule

> **Invariant:** before any deploy, the change is covered by a test that **fails without the fix**.
> A green typecheck, a successful build and a page that renders are all compatible with a control
> that returns the wrong answer.

> **Invariant:** every guard carries a **mutation proof** — reintroduce the bug, watch the test go
> red, revert. A test that passes against broken code manufactures false confidence.

## 2. The suites

| Suite | Guards |
|---|---|
| `test_regression` | Profile math against the 24 reference values |
| `test_email_guard` | No images/SVG/scripts, no SPY number, verdict survives — with its own mutation proof |
| `test_instruments` | Session windows and bin widths per instrument |
| `test_quote_source` | Quote routing between Schwab and Yahoo |
| `test_schwab_import` | Same-day ordering, reverse splits, reconciliation refusal |
| `test_auth` | Hashing, sessions, lockout, enumeration resistance |
| `test_admin` | The health page survives a total GCP outage |
| `test_portfolio_cache` | Cached filter == Firestore query, id-for-id; charts reconcile; paging is lossless |
| `test_platform` | Version arithmetic, password rules, role escalation, the 301, Anthropic removal |

Run one:

```
PYTHONPATH=. python -m tests.test_portfolio_cache
```

## 3. What these exist to catch

Assertions are on **results**, never on rendering. The bug class that reaches production is *the
control looks right and returns the wrong answer*, and no amount of type checking sees it.

Concrete examples this codebase has shipped and since guarded:

- a chart that displayed the top 8 by signed P&L and **hid −$575,488** of losses;
- an import that fabricated **+$894,000** of profit by matching pre-split lots to post-split sells;
- a session window that swept the prior afternoon's post-market into a stock's "overnight".

## 4. Specific disciplines

> **Invariant:** any enum, field name or facet value copied from a document or another session is
> a **claim**, not a fact. Prove it against the live source.

> **Invariant:** prove a read path can return non-zero before trusting a zero. A shape mismatch
> never errors — it returns a convincing empty result.

> **Invariant:** an aggregate rendered in a chart is reconciled against the total shown beside it,
> within stated rounding. Two numbers on one screen that disagree is the failure this catches.

## 5. Zero new dependencies

The suites are plain Python modules with a `main()` returning an exit code. There is no test
framework, because ten assertions do not need one.
