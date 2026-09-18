# Market data sources for Vantage — Schwab, thinkorswim, and the alternatives

Researched 2026-09-18. Every figure below carries its source; anything unverified is
flagged as such. Vantage runs on Yahoo today (free, unofficial, TLS-fingerprint
sensitive — see `CLAUDE.md`).

## The short answer on Schwab

**Schwab has a free public API for individual retail clients** — the *Schwab Trader
API – Individual* at https://developer.schwab.com/products/trader-api--individual.
Free with any Schwab brokerage account. Register an app, pick products, set a
callback URL, wait a few days for manual approval. Status "Approved - Pending" means
**not yet usable** despite the name.

**But it cannot be Vantage's ES data source, for one specific reason:**

> Schwab provides price history for equities and ETFs. It does **not** provide price
> history for options, futures, or any other instruments.
> — https://schwab-py.readthedocs.io/en/latest/client.html

So:

| What we need | Schwab |
|---|---|
| ES historical 5-min bars (the whole profile engine) | ❌ **Not available** |
| ES live quote | ✅ REST `get_quotes(['/ES'])` — use the plural form, `/` breaks the singular |
| ES live minute bars | ✅ streaming `CHART_FUTURES`, `LEVELONE_FUTURES` |
| ES Level 2 book | ❌ confirmed broken |
| Futures order entry | ❌ `assetType` is EQUITY/OPTION only; a futures order 400s |
| Accounts, positions, orders, transactions | ✅ this is what it's good at |

You could record the stream and build history from day one, but you cannot backfill —
which means no session profiles until months of recording accumulate. **That alone
disqualifies Schwab as the bar source.**

**The other operational catch: the refresh token expires every 7 days**, hard, with no
programmatic renewal (https://developer.schwab.com/user-guides/apis-and-apps/oauth-restart-vs-refresh-token).
An expired one returns `invalid_client`. An unattended 5 AM job would break weekly and
need a manual browser login. Access tokens last 30 minutes.

Rate limit ~120 req/min (community-reported; only the order throttle is documented).

## thinkorswim

**There is no public thinkorswim API.** The TD Ameritrade API — which many tools were
built on — **shut down after market close on May 10, 2024** when account migration to
Schwab completed. The Schwab Trader API is its successor. thinkorswim survives as
Schwab's active-trader platform, but scripting there is in-platform thinkScript only.
Python migration path: `tda-api` → `schwab-py`.

## What Schwab IS worth using for

**Accounts and positions — free, and exactly the portfolio module's input** (handoff §7:
positions, marks, P&L). When we build the portfolio, Schwab's *Accounts and Trading
Production* scope replaces manual entry and CSV import. The 7-day re-auth is tolerable
for a page Pedram opens himself; it is not tolerable for the unattended 5 AM job, so
**keep the two concerns separate**: bars from a data vendor, positions from Schwab.

## ES data alternatives, priced for one non-professional

⚠️ **Correcting a figure that circulates widely:** CME's non-pro **top-of-book** fee is
**$1.55/mo** per exchange, not $11–15. The $12.10 figure is *depth of market*. Source:
CME Fee List effective 2026-01-01 (CME's own PDF, mirrored at
https://api.databento.com/static/licensing/cme/cme-market-data-fee-list.pdf).
IBKR passes through exactly $1.55 / $12.10, confirming list.

Two structural facts that drive everything:

1. **Historical bars older than 24 hours carry no exchange licence fee.** Vendors
   embargo historical data at T+1 specifically to avoid real-time licensing. So our
   5-min and daily bars are nearly free; only *live quotes* trigger fees.
2. **The cheap non-pro rate requires an order-routing-capable terminal at a broker.**
   A pure data API is not one — which is why pure-data vendors sell a $199 plan that
   absorbs the licence instead of passing through $1.55.

| Vendor | History | Real-time | Total /mo | Notes |
|---|---|---|---|---|
| **Databento** ⭐ | ~$0 pay-as-you-go (~$0.50/GB; ES OHLCV is MBs; **$125 free credit**) | $199 Standard | **~$0 history-only**, $199 with live | Best API. **Continuous contracts `ES.c.0` on both historical and live** — solves our roll problem. No native 5-min; resample from 1m. |
| **Massive** (ex-Polygon.io, rebranded 2026) | $29 delayed / $79 | $199 Advanced | $199 | **Native 5-min and session bars.** ⛔ No continuous contracts — you stitch ESZ6/ESH7 yourself. |
| **Interactive Brokers** | included | **$1.55** (waived over $20 commissions) | **$1.55** + $500 min equity | Cheapest real-time by far. ⛔ Local gateway process, **manual re-auth every Sunday 1 AM ET**, 250 ms snapshots not ticks, expired-futures history capped at 2 yrs. Use `ib_async` (`ib_insync` is archived since the author's death). |
| **Sierra Chart + Denali** | included | $36 ($23.40 prepaid) + $2.00 CME | **~$38** | Cheapest *licensed* feed; open DTC protocol for external apps, but you implement it. |
| **IQFeed (DTN)** | included | $108 + $25 futures + $5.25 | ~$138 | Mature, `pyiqfeed`. ⛔ **Requires a Windows process** — bad fit for Cloud Run. |
| **Tradovate** (NinjaTrader → **Kraken, $1.5B, May 2025**) | ~90 days only | ⛔ | **$290–$1,500** | Order API is fine; **market data over the API requires CME sub-vendor registration**. NinjaTrader: "We only offer professional prices." Not viable. |
| **Norgate** | $270/yr | ⛔ none | $22.50 | Proper back-adjusted continuous ES from 1997. **Daily only — cannot do 5-min.** |
| **CME direct / DataMine** | — | $610/mo/exchange + $29,280/yr distribution | ⛔ | Plus writing an MDP 3.0 decoder. No. |

**Recommendation for Vantage:** split history from real-time.

- **Bars (the profile engine): Databento pay-as-you-go.** Likely **$0** against the $125
  free credit for a decade of ES OHLCV, no subscription, no licence, and its continuous
  contract symbology handles rolls we currently hand-code in `core/contracts.py`. Size
  any pull with `metadata.get_cost()` first.
- **Live quote: stay on Yahoo for now** (it is working and free), or move to IBKR at
  $1.55/mo if a funded account is acceptable. Databento Standard at $199 only becomes
  worth it if Yahoo's blocking gets worse.
- **Portfolio positions: Schwab**, when we build §7.

### Flagged — verify before spending

- Whether Databento's $199 Standard is genuinely all-in: the pricing page says "No
  license fees" but other Databento pages still quote a $36.50/mo non-pro pass-through.
  Budget $199–$236 and run their licensing questionnaire.
- Whether Massive's $199 carries a separate CME fee — their ToS implies bundled, never
  says it outright.
- Whether IBKR's pacing limits apply to 5-min/daily bars or only to bars ≤30 secs (the
  current docs title scopes them to small bars; the widely-quoted unscoped version is on
  a deprecated page).
- developer.schwab.com returns 403 to automated fetching, so Schwab findings rest on
  `schwab-py`'s docs, an archived copy of the official API docs, and community sources.
  Worth one manual browser check before building against it.
