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

---

## ⚠ Correction + new finding (2026-09-19): "real time" is not real time on ES

Two things this doc got wrong by omission, both material:

### 1. Yahoo's ES quote is 10 MINUTES DELAYED

Yahoo's own exchange table lists **Chicago Mercantile Exchange (CME), suffix `.CME`,
delay 10 min, provider ICE Data Services** — https://help.yahoo.com/kb/SLN2310.html.
So `/` (the page titled **Real Time**) and the 5 AM email both show an ES price that can
be up to 10 minutes stale, and every zone distance computed from it inherits that.

⛔ **This is not a rounding issue.** ES routinely travels several points in 10 minutes;
on 2026-09-18 it moved 7675 → 7695 in about 20. A "NO TRADE, 11 pts from the dip band"
verdict can be wrong at the moment it is displayed.

⚠ **Not yet measured live.** The delay is documented, not observed — attempts to measure
it on Sat 2026-09-19 returned a 20-hour-old quote because the market was closed, which is
the weekend, not latency. **Measure on a trading day** before quoting a number:
`python -c "..."` comparing `meta.regularMarketTime` to now. Until then this is Yahoo's
claim about Yahoo, which is good enough to act on but not a measurement of ours.

Yahoo's US equity feed is a different entitlement (the table shows Nasdaq real-time,
NYSE *Indices* 15 min; SPY on NYSE Arca is not broken out) — **SPY's delay is UNVERIFIED**
and must be measured the same way, not assumed.

### 2. Schwab IS a real-time ES source — and it is free for Pedram

The headline above ("Schwab cannot be Vantage's ES data source") is right about
**history** and was over-generalised to mean market data as a whole. Split the two:

| | Schwab |
|---|---|
| ES **historical** bars (the profile engine) | ❌ none — REST `/pricehistory` is equities/ETFs only |
| ES **real-time** quote | ✅ REST `get_quotes(['/ES'])` |
| ES **real-time** 1-min OHLCV | ✅ streaming `CHART_FUTURES` |
| ES **real-time** top of book | ✅ streaming `LEVELONE_FUTURES` |

Real-time futures data comes with the **brokerage entitlement** — no extra market-data
subscription and no additional entitlement for an individual developer
([schwab-py streaming docs](https://schwab-py.readthedocs.io/en/latest/streaming.html)).
Every genuinely free real-time CME feed works this way: the entitlement rides on a
broker account. There is **no free public real-time CME API** — CME's own WebSocket
starts at $0.50/GB plus ILA fees, and free tiers at TradingView/Yahoo/Massive are all
10-min delayed. Non-pro real-time list is **$1.55/mo** top-of-book (see fee list above).

**"Can we save the datapoints?" — yes, going forward only.** Recording the
`CHART_FUTURES` stream into Firestore builds true 1-minute history from day one, at
full real-time fidelity. It **cannot backfill**, so it does not replace Yahoo/Databento
for the prior-session profiles the map depends on.

### Recommended shape

- **History / session profiles** → Yahoo today, Databento when it matters. A 10-min
  delay is irrelevant for yesterday's completed session.
- **Live price on the Real Time page + the email's "price now"** → Schwab quotes.
- **Portfolio positions** → Schwab (unchanged).

⛔ **The 7-day refresh-token expiry still rules Schwab out of the unattended 5 AM job**
as a hard dependency. Design it as an *enhancement*: if a valid Schwab token exists, use
it for the live price; otherwise fall back to Yahoo and **label the source and age on
screen**. Never let a broken token stop the map from being produced.

### Blocked on Pedram

Schwab needs an app registered at developer.schwab.com under his login, plus an
interactive OAuth sign-in with his Schwab credentials, and re-auth weekly. Those are his
to perform — this session will not handle brokerage credentials. Approval of a new app
has historically taken a few days ("Approved - Pending" ≠ usable).

---

## Transaction history (2026-09-19): three routes, two of them closed

For the Portfolio / P&L section. Goal was "every transaction since the account opened".

| Route | Verdict |
|---|---|
| **Schwab API** `get_transactions` | ⛔ **60-day ceiling.** `start_date` "must be within 60 days of the current date" ([schwab-py](https://schwab-py.readthedocs.io/en/latest/client.html)). No parameter reaches further. Fine for the DAILY sync, useless for backfill. |
| **Read schwab.com in Pedram's Chrome** | ⛔ **Hard-blocked.** `navigate` to `client.schwab.com` *and* `www.schwab.com` both return **"Navigation to this domain is not allowed"** — an extension-level block on brokerage domains, distinct from the per-domain "Permission denied" seen elsewhere. Not something Pedram can grant from his side, and not a login problem. Do not retry this route. |
| **Schwab CSV export, downloaded by Pedram** | ✅ **The only path to full history.** |

### ⚠ The CSV export fails SILENTLY when it is too big

Schwab caps an export at roughly 1,500–10,000 rows depending on surface, and **when the cap is
exceeded the file downloads with NO records rather than an error**. An importer that trusts it
would record "no trades in 2024" and be confidently wrong — the
[[wrong-shape-yields-plausible-nothing]] failure exactly.

**Therefore the importer MUST:**
1. Refuse to treat a zero-row export as "no activity" — flag it as a suspected truncation and
   name the date range that needs re-exporting.
2. Ask for **one file per year** (or per quarter for heavy years), not one giant file.
3. Track coverage per date range and render **gaps** on the Sync tab, so "all time" never
   silently means "since whenever the import happened to start".
4. Dedupe imported vs API-synced rows on transaction id, so the 60-day overlap is harmless and
   re-importing the same file is a no-op.

### ⚠ Unverified and important: are FUTURES trades even in there?

Schwab's API is documented as EQUITY/OPTION only for orders, and futures sit on a separate
platform. Whether ES futures and ES options on futures appear in `get_transactions` — or in the
web export — **has not been confirmed** and cannot be from this session. If they do not, the CSV
is the only source for the trades Pedram actually cares about and the daily API sync covers
nothing. Confirm before building the sync half.
