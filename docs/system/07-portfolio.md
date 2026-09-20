# 07 — Portfolio

*Trades, cash, and the two different ways to compute P&L — both of which are shown.*

**Applies to build:** `0.002` · **Last reviewed:** 2026-09-20

---

## 1. Where the data comes from

A Schwab JSON transaction export, imported by `core/schwab_import.py` via
`python -m jobs.import_schwab <file.json> [--account NAME]`.

As imported (corporate account, 2023-10-11 → 2026-09-18): **1,153 transactions, 771 round trips,
19 cash transfers.**

## 2. ⭐ The export states its own totals, so the parse is provable

`TotalTransactionsAmount` and `TotalFeesAndCommAmount` are reconciled **to the cent**, and the
import is **refused** when they do not match.

> **Invariant:** do not weaken the reconciliation gate. These figures land on a P&L screen where
> they read as fact, and a parse that is wrong by a cent is wrong in a way nothing else detects.

## 3. ⛔ Two bugs that produced confident wrong money

### 3.1 Same-day ordering

Schwab lists transactions **newest-first**, so a day trade's *Sell to Close* appears **before** its
*Buy to Open*. Sorting by date alone closes a position that does not exist yet — fabricating an
unmatched close **and** a phantom open position from one real round trip.

Measured before the fix: **76 unmatched closes, 54 phantom positions.**

> **Invariant:** within a date, opens are processed before closes (`_order` in
> `build_round_trips`). Date order alone is not sufficient ordering for a ledger.

### 3.2 Reverse splits

Schwab books a split as a **pair on one date**: old shares leave under the CUSIP as a negative
quantity, new shares arrive under the ticker. SQQQ did 5:1 on 2024-11-07 (−59,800 / +11,960).

Un-adjusted pre-split lots matched against post-split sells fabricated **roughly +$894,000 of
profit**.

> **Invariant:** `detect_splits()` runs before lot matching. This is not a rounding error — the
> lots are in different share units and the arithmetic is meaningless without the adjustment.

## 4. ⚠ Two P&L figures, and the gap between them is reported

| Method | How | Use |
|---|---|---|
| **Cash-truth** | `net_cash − deposits − income − costs` | Totals. It is Schwab's own arithmetic |
| **Lot-matched** | FIFO matching of opens to closes | Per-trade attribution |

With no open positions the two must agree. On this file they differ by **$146.50**, from per-lot
fee rounding.

> **Invariant:** `account_state()` reports both figures and the gap. The gap is never hidden and
> never silently reconciled — a difference you cannot see is a difference you cannot investigate.

## 5. ⛔ Deposits are never profit

> **Invariant:** transfers in and out change the cash balance and are excluded from P&L entirely.
> Counting a deposit as a gain is the single most flattering possible error.

## 6. The account as imported

| | |
|---|---:|
| Deposits | $565,100.00 |
| Cash now | $94,651.65 |
| Realized (cash-truth) | **−$519,390.02** |
| Interest & dividends | +$50,676.37 |
| Fees | $32,996.57 |

Flat — nothing open since 2026-09-18.

> ⛔ **There are no futures in this account.** It trades QQQ options (350), SPY options (255) and
> SQQQ/TQQQ shares. The ES-centric origin of the project does not match what this account does;
> ES may live in a separate personal account.

## 7. Empty is a valid state

> **Invariant:** with nothing imported and Schwab unlinked, every accessor returns empty and the
> pages render an explicit "not connected" state. There are no placeholder numbers — a figure on
> a P&L screen looks like a fact.
