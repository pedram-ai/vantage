# 04 — Market data

*Where every price comes from, and the traps in each source.*

**Applies to build:** `0.002` · **Last reviewed:** 2026-09-20

---

## 1. The two sources

| Source | Gives | Does not give |
|---|---|---|
| **Yahoo** (`core/bars.py`) | OHLCV bars for futures, ETFs, stocks; all history | Real-time; ~10-minute delay |
| **Schwab** (`core/schwab.py`) | Real-time quotes for the whole watchlist in ONE request | **No ES history** — equities and ETFs only |

The split is deliberate: Schwab for live quotes, Yahoo for everything historical.

## 2. ⛔ Yahoo TLS-fingerprints plain Python clients

`httpx` and `requests` receive **429** from Yahoo even with browser headers, cookies and HTTP/2.
`curl` succeeds. The difference is the TLS handshake fingerprint, not anything in the request.

`core/bars.py` uses **`curl_cffi` with `impersonate="chrome"`**.

> **Invariant:** do not "simplify" this back to `httpx`. It will appear to work in testing and
> then return 429 in production, which surfaces as empty charts rather than an error.

## 3. ⚠ Yahoo is one HTTP call PER SYMBOL

A 20-symbol watchlist is 20 requests, and Yahoo already rate-limits this app. `core/quotes_batch.py`
caps a refresh at `YAHOO_MAX_PER_REFRESH = 12`.

> **Invariant:** a symbol that was not fetched renders as **"not fetched"**, never as blank and
> never as a stale price. A blank cell on a quote screen reads as "no data exists".

Schwab quotes the whole list in one request, which is why live watchlists effectively require the
Schwab link.

## 4. Schwab's limits, and why it cannot run the daily job

- **No futures history.** The API covers equities and ETFs; ES bars must come from Yahoo.
- **The refresh token expires every 7 days** and re-linking is interactive. An unattended weekday
  job cannot depend on it.
- Access needs two approvals: the *product* subscription, then the *app*. Status
  **"Approved - Pending"** is not usable — wait for **"Ready For Use"**.

See [`docs/SCHWAB-SETUP.md`](../SCHWAB-SETUP.md) for the full walkthrough.

## 5. Price formatting

> **Invariant:** every price renders at two decimals. Yahoo returns floats like
> `759.5399780273438`; printing a raw price is how an implausible number reaches a screen.

## 6. Contract roll (futures only)

`core/contracts.py` picks the front month, rolling ~8 days before the third Friday of Mar/Jun/Sep/Dec.
Overridable in Admin → Data sources.

> **Invariant:** history is never back-adjusted. A profile is a statement about a specific
> contract's trading session; adjusting it retroactively makes past levels unverifiable.
