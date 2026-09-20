# 02 — Architecture

*Every moving part, and the path a request takes through them.*

**Applies to build:** `0.002` · **Last reviewed:** 2026-09-20

---

## 1. The whole system

GCP project **`patexia-vantage`**, region **`us-central1`**.

| Part | Name | Role |
|---|---|---|
| Cloud Run service | `vantage` | The web app. `min-instances=1`, 1 vCPU, 512 MiB |
| Cloud Run job | `vantage-daily-run` | Same image, `python -m jobs.daily_run` — computes and emails the briefing |
| Cloud Scheduler | `vantage-daily-run-trigger` | `0 5 * * 1-5` America/Los_Angeles |
| Firestore (native) | `(default)` | Every collection listed below |
| Secret Manager | — | Schwab app credentials; SES keys are referenced cross-project |
| Domain | `argentridge.com` | Cloud Run **domain mapping**, Cloudflare DNS |

> **Invariant:** the custom domain uses a domain mapping, not a load balancer. A load balancer
> would add roughly **$18/month** for no benefit — app-level login replaces what IAP gave us.

## 2. The stack

**FastAPI + Jinja2**, server-rendered HTML. Charts are **server-rendered SVG**; there is no
client-side framework and no build step for the front end.

> **Invariant:** SVG is fine in the browser and never in email. Mail clients strip it. See
> [10 — Email](./10-email.md).

## 3. Firestore collections

| Collection | Written by | Holds |
|---|---|---|
| `users` | admin console | account, scrypt hash, role, setup token hash |
| `sessions` | login | SHA-256 of the session token, never the token |
| `trades` | Schwab import | closed round trips |
| `transactions` | Schwab import | the raw parsed rows |
| `cash` | Schwab import | deposits, withdrawals, interest, dividends |
| `positions` | Schwab import | open holdings |
| `import_runs` | Schwab import | the coverage and reconciled totals of each import |
| `runs` | daily job | each briefing, including the rendered email HTML |
| `emails` | mailer | every send, with reset links redacted |
| `watchlists` | UI | user-defined lists |
| `author_levels`, `events`, `settings` | UI | levels, calendar, contract override |

## 4. A page request, end to end

1. **Cookie → session.** `auth.session_user()` reads `ar_session`, hashes it, looks it up, checks
   expiry and that the user is not disabled.
2. **Context.** `_ctx()` attaches the viewer and the build footer (a dict lookup — `core/version`
   reads `build_info.json` once at import).
3. **Data.** Portfolio reads come from the in-process cache; quotes come from the 45-second tape
   cache or a live fetch.
4. **Render.** Jinja renders the template; charts are built as SVG strings in the same pass.

> **Invariant:** nothing on the request path makes an uncached Secret Manager call. This is not
> hypothetical — an uncached key lookup cost **2,870 ms on every page load** until 2026-09-20.

## 5. Caches, and who clears them

| Cache | TTL | Cleared by |
|---|---|---|
| `portfolio._all()` — whole collections | 600 s | `portfolio.invalidate()`, called by the importer |
| `_tape()` — the header quote strip | 45 s | time only |
| `sysheath.snapshot()` | 60 s | `?force=1` |
| `bars` | 60 s | `fresh=True` on the Real Time page |

> **Invariant:** any cache holding money figures is invalidated by its writer, not merely aged
> out. A stale P&L that survives an import is a wrong number on a screen that reads as fact.

## 6. Why Firestore and not Cloud SQL

Measured 2026-09-20: a full period recomputation off the cache is **0.6 ms**; the whole trade
history is 771 rows. Cloud SQL would add a monthly instance cost and connection management to
replace a read that is already three orders of magnitude faster than the render around it.

> **Invariant:** before proposing a faster datastore, profile the request. The slowness this
> project actually had was an uncached secret and a live HTTP fetch in a header partial.
