# 01 — Overview

*What Argent Ridge is, who it serves, and what it deliberately refuses to be.*

**Applies to build:** `0.002` · **Last reviewed:** 2026-09-20

---

## 1. What it is

A single-operator trading platform: session structure for any instrument, live watchlists, the
full trading record with P&L by period, and a weekday briefing by email.

It began as one scheduled email about ES futures levels. It is now a multi-instrument platform,
and most of the original framing no longer applies — see [05 — Instruments & sessions](./05-instruments-and-sessions.md)
for what generalising cost.

## 2. Who it is for

**One person.** There is exactly one account and no way to create a second except from the admin
console. This is not a product with users; it is an operator's tool.

> **Invariant:** there is no sign-up route, no invite-by-link self-registration, and no password
> reset that reveals whether an address has an account. See [03 — Authentication](./03-authentication.md).

## 3. The five surfaces

| Surface | What it answers |
|---|---|
| **Today** | Where is price relative to the structure that formed overnight? |
| **Markets** | What is every symbol I track doing, right now? |
| **Positions** | What am I holding? |
| **Performance** | What did I make or lose, over which period, on what? |
| **Admin** | Is the system healthy, what changed, who can sign in, where does data come from? |

## 4. What it deliberately is not

- **Not a broker.** It places no orders and holds no order-entry surface. Schwab is a read-only
  quote and history source.
- **Not advice.** Every page carries the disclaimer, and nothing ranks, recommends, or predicts.
- **Not multi-tenant.** No organisations, no sharing, no roles beyond `owner` and `staff`.
- **Not an AI product.** A Claude-written commentary panel shipped on Performance and was
  **removed on 2026-09-20**: it cost 3.5 s per page load and told the operator what the figures
  beside it already said. See [08 — Performance & charts](./08-performance-and-charts.md) §5.

## 5. The rules that outrank convenience

> **Invariant:** every number on a page came from data. Not a constant, not a plausible default.
> `0` and `100` are measurements and must be computed like any other. A failed read renders `—`,
> never `0` — "we could not compute this" and "this is zero" are opposite facts.

> **Invariant:** a blank beats a wrong value. On a P&L screen a placeholder reads as fact.

> **Invariant:** a chart may not hide a row without saying so. Truncation is drawn on the chart,
> with the total of what was folded. See [08](./08-performance-and-charts.md) §2.
