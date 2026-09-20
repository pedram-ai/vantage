# Vantage — multi-instrument trading platform

Single-user platform (IAP, pedram@patexia.com only). Session profiles
(POC/VAH/VAL/VWAP), zone action maps and watchlists for **any instrument** — ES and NQ
futures, ETFs, stocks — plus a weekday ES briefing email. Originated from
`VANTAGE_HANDOFF.md` (copy in `docs/`), which describes the ES-only ancestor; read the
2026-09-19 redesign section at the bottom for what it is now.

## Live infrastructure (GCP project `patexia-vantage`, us-central1)

| thing | name | notes |
|---|---|---|
| Cloud Run service | `vantage` | https://vantage-424459368059.us-central1.run.app — behind **IAP**, access = `user:pedram@patexia.com` ONLY (role `roles/iap.httpsResourceAccessor` on the service). App double-checks the IAP header against `VANTAGE_ALLOWED_EMAILS`. |
| Cloud Run job | `vantage-daily-run` | same image, `python -m jobs.daily_run`; computes + archives a run to Firestore |
| Cloud Scheduler | `vantage-daily-run-trigger` | `0 5 * * 1-5` America/Los_Angeles → runs the job |
| Firestore (native) | `(default)` | collections: `author_levels`, `runs`, `events`, `settings` |
| Billing | patexia cloud account | everything sits in free tier except pennies of Cloud Build/Artifact Registry |

Deploy: `gcloud run deploy vantage --source . --project=patexia-vantage --region=us-central1`
then update the job image:
`gcloud run jobs update vantage-daily-run --image=$(gcloud run services describe vantage --region=us-central1 --project=patexia-vantage --format='value(spec.template.spec.containers[0].image)') --region=us-central1 --project=patexia-vantage`

## Traps

- **Yahoo TLS-fingerprints plain Python HTTP clients** — httpx/requests get 429 even
  with browser headers and cookies; curl works. `core/bars.py` uses `curl_cffi`
  with `impersonate="chrome"`. Do not "simplify" it back to httpx.
- Session windows are **ET** (`America/New_York`); everything shown to Pedram is PT.
- Contract roll: front month, ~8 days before 3rd Friday of Mar/Jun/Sep/Dec
  (`core/contracts.py`); override in Settings. Never back-adjust history.
- Regression test: `python -m tests.test_regression` reproduces the handoff §11
  numbers (Sep 16–18, 2026) while Yahoo's 5m window still covers them; afterwards
  it falls back to invariants. All 24 values matched exactly on 2026-09-18.
- The profile weight in `core/zones.py` (`PROFILE_WEIGHT=1.5`) is deliberate —
  §11's lesson: the profile-derived dip band beat the author band on 2026-09-18.

## Not built yet

Push notification, Substack/X scrapers + LLM level extraction, **CSV trade import**
(parser waits on a real Schwab export — see `docs/DATA-SOURCES.md`), Schwab positions
sync, history scorecard. Author levels are entered manually at `/levels` for now.
The Claude scheduled task `Daily ES Action Map` still runs in parallel — retire it
once Vantage's email has run clean for two weeks (handoff §9 M6).

## Email (added 2026-09-18)

Vantage sends its own email via **AWS SES v2** (`core/mailer.py`, hand-rolled SigV4 over
curl_cffi — no boto3 dependency). It reuses **Connect's existing verified sender**
`notify.patexia.com`: the Vantage compute SA was granted `secretmanager.secretAccessor`
on `connect-redesign-aws-access-key-id` / `-secret-access-key` **in patexia-connect**, and
Cloud Run references them cross-project by PROJECT NUMBER
(`projects/349112552843/secrets/...` — the project *ID* form is rejected as "not a valid
secret name"). No credential was copied; there is still one key.

- ⛔ **No email on Sat/Sun.** Two independent guards: Scheduler cron is `1-5`, and
  `jobs/daily_run.py` refuses to send when `weekday() >= 5` regardless of trigger.
  Override for testing only with `--force-email`.
- ⛔ **No SPY/SPX numbers, strikes or expiries in the email** (handoff §5.1). SPY lives
  on the platform only. The email is ES-only.
- Rendered HTML is archived on the run doc (`email_html`) and viewable at
  `/history/<id>/email`; `/email-preview` renders today's without sending.
- First live send verified 2026-09-18, SES message id `010001a0b6ab5af9-...`.

## UI (added 2026-09-18)

- `/` is **Real Time** — recomputes from fresh bars on every load (`fresh=True` bypasses
  the 60s bar cache) with an explicit Refresh button.
- **Candlestick charts** (`core/charts.py`) for ES and SPY, server-rendered SVG, with
  prior POC/VAH/VAL drawn as dashed lines. Timeframes: 15 min / 1 hour (resampled from
  5m bars) and Daily / Weekly (separate Yahoo fetch). SVG is fine in the UI, never email.
- **Hover definitions** for POC/VAH/VAL/VWAP/RTH/TICK/TRIN/etc. live in `core/glossary.py`
  and render as `<abbr title=...>`. Add terms there, not in templates.
- All prices render at **2 decimals** (`'%.2f'|format`). SPY comes off Yahoo with float
  noise like 759.5399780273438 — never print a raw price.

## Repo / CI (added 2026-09-18)

Repo: **https://github.com/pedram-ai/vantage** (private). Pushed 2026-09-19 after Pedram
created the empty repo — SSH authenticates as `pedram-ai`; `gh` was never authenticated
(two device codes expired), and SSH alone cannot CREATE a repo, only push to one.

`.github/workflows/deploy.yml` deploys on push to `main` via **Workload Identity
Federation — no service-account key exists.** Pool `github` / provider
`github-provider` in patexia-vantage, scoped by `attribute.repository_owner=='pedram-ai'`,
impersonating `vantage-deployer@patexia-vantage.iam.gserviceaccount.com`. The workflow
gates the deploy on two tests and then repoints the daily job at the new image.

- `tests/test_regression.py` — profile math vs handoff §11.
- `tests/test_email_guard.py` — asserts the email has no images/SVG/scripts, no
  `background:` shorthand, no SPY/SPX **number**, and that the verdict + both distances
  survive. **Carries its own mutation proof** (injects an `<img>` and a SPY price and
  fails if the guard doesn't catch them) per the standing test rule.

## Market data (added 2026-09-18)

See `docs/DATA-SOURCES.md`. ⚠ The headline below is about HISTORY only — Schwab **is** a
real-time quote source and is now wired in (`core/schwab.py`). Headline:
**Schwab's API cannot supply ES price history**
(equities/ETFs only) and its **refresh token dies every 7 days** — unusable for the
unattended job. It is still the right source for *portfolio positions* later. thinkorswim
has no public API; the TD Ameritrade API shut down 2024-05-10. Best bar source if we
leave Yahoo: **Databento pay-as-you-go (~$0 against their $125 credit)**, which also has
continuous-contract symbology that would replace `core/contracts.py` roll handling.


---

## Platform redesign (2026-09-19) — multi-instrument

Vantage stopped being an ES email tool. Navigation is now
**Today · Markets · Positions · Performance · Settings**.

- ⛔ **`History` and `Levels` are no longer top-level.** History meant two unrelated things
  (archived briefings vs the trading record); briefings live under Today, the record is
  Performance. Levels is a property of an instrument, reachable from Settings.
- **`core/instruments.py` is the registry.** Instruments are DATA: kind, session spec, bin
  width, symbol mapping, whether author levels exist. `resolve()` never raises — an unknown
  ticker is a US equity. Adding a symbol is a watchlist row, not a release.
- ⚠ **Session windows are per-instrument.** Futures overnight = 18:00 prev day → 09:30;
  equities have NO overnight, only a 04:00 pre-market. Applying the futures window to SPY
  sweeps in the prior afternoon's post-market and calls it "overnight". Guarded in
  `tests/test_instruments.py`.
- ⚠ **Histogram bin width is per-instrument** (`_histogram(bars, bw)`). A fixed 1-point bin is
  right for ES at 7,700 and collapses a $40 stock into 4 bins — measured in the tests.
  `derive_bin()` scales with price (~0.05%, snapped).
- **`core/symbol_view.py` replaces `run_builder` for the UI.** `run_builder` is still the
  EMAIL path (ES-only) and is untouched — the email guard test still gates it.
- **`core/watchlists.py`** — any number of lists. Two AUTO lists (`__held__`, `__traded__`)
  are computed on read from the portfolio, never stored, so they cannot drift.
- ⚠ **`core/quotes_batch.py` — Yahoo is one HTTP call PER SYMBOL.** A 20-symbol list is 20
  calls and Yahoo already 429s this app, so the Yahoo path is capped at
  `YAHOO_MAX_PER_REFRESH = 12` and skipped symbols render as "not fetched", never blank.
  Schwab quotes the whole list in ONE request (`schwab.quotes([...])`) — which is why live
  watchlists effectively require the Schwab link.
- **`core/portfolio.py` returns EMPTY** until Schwab is linked or a CSV is imported. Positions
  and Performance render explicit "not connected" states. ⛔ No placeholder numbers — a figure
  on a P&L screen reads as a fact.

### UI
Soft rounded face (`ui-rounded` → SF Pro Rounded), **no monospace anywhere** — figures use
`font-variant-numeric: tabular-nums` so columns align without the typewriter look. Palette is
muted teal / sage / clay, not stoplight. Pedram asked for both explicitly.


---

## Trade history import (2026-09-20) — REAL DATA IS LOADED

`core/schwab_import.py` + `python -m jobs.import_schwab <file.json> [--account NAME]`.
Imported the corporate account: **1,153 transactions, 2023-10-11 → 2026-09-18, 771 round
trips, 19 cash transfers.** Firestore: `transactions`, `trades`, `cash`, `import_runs`,
`positions`.

⭐ **THE EXPORT STATES ITS OWN TOTALS, SO THE PARSE IS PROVABLE.**
`TotalTransactionsAmount` / `TotalFeesAndCommAmount` are reconciled to the cent and the
import is **REFUSED** if they do not match. Do not weaken this gate — these numbers land on
a P&L screen where they read as fact.

### ⛔ Two bugs that produced confident wrong money. Both are now tested.

1. **SAME-DAY ORDERING.** Schwab lists newest-first, so a day trade's `Sell to Close`
   appears BEFORE its `Buy to Open`. Sorting by date alone closes a position that does not
   exist yet, fabricating an unmatched close AND a phantom open position from ONE real round
   trip. Measured before the fix: **76 unmatched closes, 54 phantom positions.** Within a
   date, opens MUST be processed before closes (`_order` in `build_round_trips`).
2. **REVERSE SPLITS.** Schwab books a split as a PAIR on one date — old shares leave under
   the CUSIP (negative qty), new shares arrive under the ticker. SQQQ did 5:1 on 2024-11-07
   (−59,800 / +11,960). Un-adjusted pre-split lots matched against post-split sells
   fabricated **~+$894k of profit**. `detect_splits()` + `_apply_split()`.

### ⚠ Prefer CASH-derived P&L for totals
With no open positions, realized P&L is recoverable from cash alone
(`net_cash − deposits − income − costs`) and that is Schwab's own arithmetic. Lot matching
is for PER-TRADE attribution and drifts ~$147 on this file from per-lot fee rounding.
`portfolio.account_state()` reports both and the gap; the Performance page shows it.

### The account, as imported
Deposits **$565,100** · cash now **$94,651.65** · realized **−$519,390.02** (−91.9% of
deposits) · interest & dividends **+$50,676.37** · fees **$32,996.57** · **flat, nothing
open** since 2026-09-18.

⛔ **NO FUTURES IN THIS ACCOUNT.** It trades QQQ options (350), SPY options (255), and
SQQQ/TQQQ shares. The ES-centric origin of this project does not match what the corporate
account actually does — ES may live in a different (personal) account. The daily Schwab
transaction sync, when built, therefore covers equities/options, which IS what matters here.

### Jinja trap
`'%,.0f'|format(x)` **raises** — Python %-formatting has no comma flag. Use the
`money` filter registered in `app/main.py` (`{{ x|money }}`, `{{ x|money(2, true) }}`).

---

## Admin console (2026-09-20)

Avatar (top right) → Settings · Admin console · Sign out. `/admin` is owner-only and holds
three things; **Users moved under it** (`/users`, still owner-gated).

### System Health — `core/sysheath.py`, `/admin/health`
Method copied from Lateral Compass (`api/src/systemhealth/*`): fan out to INDEPENDENT,
FAIL-SOFT sources and merge, so one dead source shows "—" instead of a blank page.
`tests/test_admin.py` simulates a total GCP outage and asserts the page still renders,
reports the problem, and marks the cost as NOT live.

⭐ **Differs from LC on cost, deliberately.** LC's `cost.ts` is a hand-maintained table of
monthly figures. Here the **resource shape is read LIVE** from the Cloud Run Admin API
(CPU, memory, min/max instances) and only the **unit prices** are a documented constant,
so the number moves when the infrastructure moves. The page states which half is live.

- ⚠ **Cloud Run v2: the overall Ready condition is `terminalCondition`, NOT an entry in
  `conditions[]`.** `conditions[]` only holds RoutesReady / ConfigurationsReady; looking
  there reports a perfectly healthy service as down. Cost me one red herring.
- ⚠ **Never name a template-facing dict key `items`.** In Jinja, `cost.items` resolves to
  `dict.items` (the method) and the page dies with `'builtin_function_or_method' object is
  not iterable`. The key is `cost.lines`. Same trap applies to `keys`, `values`, `get`.
- The app SA needs `roles/run.viewer` for the live shape.

### Documentation — `core/docs_store.py`, `/admin/docs`
Also LC's method: documentation is the repo's own markdown, served read-only, so it ships
in the same image as the code and cannot drift.

- **Change log = every commit**, generated from `git log` by `scripts/gen_build_info.py`
  into `app/build_info.json`. "Every commit is logged" is therefore structural, not a
  discipline someone has to remember.
- ⛔ **It must be generated BEFORE the upload, never in the Dockerfile.** `.gcloudignore`
  excludes `.git/`, so a `RUN` step inside the image would silently write
  `available:false` and wipe a good log. CI does it (`Bake the change log from git`), and
  `scripts/deploy.sh` does it for manual deploys.
- `app/build_info.json` is gitignored — it is a build artifact.
- Markdown is rendered by a small escaping renderer; repo text is escaped BEFORE any
  formatting, so a doc containing HTML cannot inject. Slug lookup is exact and confined to
  the repo — `../../etc/passwd` and friends are covered by tests.
- ⚠ Finder/iCloud keeps regenerating `Name 2.md` byte-identical duplicates. They are
  deleted and also **filtered defensively** in `list_docs()`, because deleting alone has
  not held.

---

## Performance charts, AI review, sent-email archive (2026-09-20)

### Charts — `core/perf_charts.py`
Server-rendered SVG, no JS. Equity curve, monthly diverging bars, gross-vs-fees, by-category.

⚠ **The obvious green/red pair FAILS colour-vision separation** — `#35875c` vs `#c05b4d` is
ΔE **5.9** under protanopia against a floor of 8. Validated with the dataviz palette checker,
not guessed. Shipped pair is **`#2f9e8f` / `#c0563f`** (teal-green vs orange-red): all six
checks pass in both light and dark (worst CVD ΔE 10.6 deutan, normal-vision 23.9). Position is
a redundant encoding everywhere — bars sit above/below zero and values carry a sign.

⚠ **The equity fill switches hue AT THE ZERO LINE.** Tinting the whole area by the ENDING
value made a genuinely profitable stretch render red.

⛔ **Never name a template-facing dict key `items`** — in Jinja `cost.items` resolves to
`dict.items` (the method) and the page dies with `'builtin_function_or_method' object is not
iterable`. Same trap for `keys`, `values`, `get`.

### AI review — `core/ai_insights.py`, on `/performance`
`claude-opus-5` via the official SDK with structured outputs.

⛔ **The model NEVER sees raw trades.** `build_payload()` passes only already-computed
aggregates, and the schema forces a `figure` field per observation quoting the exact value it
reasoned from — rendered next to the text, so a fabricated number is visible rather than
plausible. Cached per (period, data fingerprint): re-runs only when the numbers move.

⚠ **The shared `lexdana/anthropic-api-key` is REVOKED (401 as of 2026-09-20).** Key lookup is
env → `patexia-vantage/anthropic-api-key` → `lexdana/...`, Vantage's own first so a key pasted
in Settings overrides the rotated shared one. With no valid key the panel simply does not
render — it never shows an error.

### Sent email — every send archived
`mailer.send_html(to, subject, html, kind=...)` wraps the real sender and writes to the
`emails` collection. Archiving is at that single choke point, so nothing can send unlogged.
⛔ **Setup/reset tokens are REDACTED in the archive** (`/setup/<token>` → `/setup/[redacted]`)
— an admin browsing sent mail must not be able to lift a live password-reset link.

### Self-service password reset
`/forgot` emails a single-use link. ⛔ The confirmation is **byte-identical** for known and
unknown addresses (no user enumeration), and a *requested* reset no longer clears the existing
password — otherwise anyone who can trigger one could lock the owner out.

---

## Performance page fixes (2026-09-20)

### ⛔ The by-instrument chart was hiding money
`category_bars` drew `rows[:8]` from a list sorted by **signed** P&L, so it kept the
winners and dropped the losers. Live on the real book it showed SPY +$38k / UCO +$31k
and omitted **QQQ −$240,600, SQQQ −$298,641, TQQQ −$36,247** — **−$575,488 invisible**
beside a total that read −$519,390. Pedram spotted it: *"on the left you are talking
about $500K loss but the right side doesn't add up."*

Now ranked by **absolute** value, and whatever is still folded has its **count and its
remainder drawn on the chart**. A truncated chart must never look complete. Same failure
family as [[wrong-shape-yields-plausible-nothing]].

### ⚠ Period switching was 3.5 s — Firestore, not BigQuery
Every period click re-streamed the whole `trades` + `cash` collections and recomputed.
There is no BigQuery anywhere in this app and it does not need a second database: 771
trades fit in memory.

`core/portfolio._all(name)` caches each collection in process (TTL 600 s, returns copies
so callers cannot corrupt it). `trades_between` is now an in-memory filter.
**3,507 ms → 1–3 ms**, measured.

- ⛔ **`portfolio.invalidate()` is called by the importer**, at the end of `jobs/import_schwab.run()`.
  The TTL is only a backstop for a writer someone forgets to wire up — a cached P&L that
  outlives an import is exactly the wrong-number-on-a-money-screen failure this repo refuses.
- ⚠ `trades_between` bounds are **inclusive at both ends**, matching the Firestore
  `>= / <=` query it replaced. It relies on `closed_on` being a `YYYY-MM-DD` **string**
  so string order is date order; a datetime there would silently return the wrong period.
- Cache is primed by a daemon thread in `@app.on_event("startup")`, fail-soft, so the
  first page load doesn't pay the read.

### Trades pagination
50/page. ⛔ `perf["trades"]` is the current page; **`perf["all_trades"]` is the full
period** and is what the charts and the AI payload read — paging the table must not
reshape the analysis beside it.

### "By setup" is honest now
It is the action-map zone (dip / fade / breakout / breakdown). Schwab's export carries no
zone, so every imported trade is `zone: None` → "Outside the map", which read like a
verdict on the trading. The page now says the zone wasn't recorded and that it fills in
going forward, instead of drawing a one-bar chart.

### `tests/test_portfolio_cache.py`
Cached filter asserted **id-for-id against the original Firestore query** (5 windows incl.
a single day and an empty range), paging proven lossless (771 paged = 771 unique = 771
total), chart reconciled to the period total within label-rounding tolerance. Three
mutation proofs — reintroducing the top-8 truncation is caught at $575,041.

⚠ Chart labels are **abbreviated** (`$38k`), so scraping them back is lossy; the test's
tolerance is derived from the abbreviation, not hand-picked.

---

## Speed, versioning, IA and system docs (2026-09-20)

### ⛔ The slow page was never the database
Profiled per `/performance` render, warm:

| call | time |
|---|---:|
| `portfolio.performance()` (cached) | **0.6 ms** |
| all five charts | 0.7 ms |
| `_tape()` — live Yahoo, on EVERY page | **156 ms** |
| `ai_insights.review()` | **3,074 ms** |

Inside that last one: `_api_key()` was **2,870 ms** — two Secret Manager round trips, **uncached,
on every page load** — then ~600 ms for Anthropic to reject the revoked key. Pedram read the
symptom as "the database is slow" and asked for Cloud SQL. It would not have helped.

⭐ **Profile before proposing a datastore.** 771 trades, 0.6 ms. The answer was an in-process cache
and deleting a feature.

### The AI panel is DELETED — no Anthropic anywhere
*(Pedram: "Not sure what that AI panel is. If we dont need it get rid of it. don't use anthropic at
all. And even if you need LLM model, use gemini from model garden.")* `core/ai_insights.py`, the
route, the template block, the Settings card and the `anthropic` dependency are gone.
`tests/test_platform.py` greps `app/ core/ scripts/` and fails on any match. **If an LLM is ever
needed here it is Gemini via Vertex/Model Garden, not Anthropic.**

⚠ The `anthropic-api-key` secret still exists in `patexia-vantage` and was NOT deleted — deleting
secrets is gated. It is simply unread.

### Versioning — `VERSION`, +0.001 per deploy
`scripts/bump_version.py` · `core/version.py` · footer on **every** page including signed-out.

- ⛔ **`VERSION` is COMMITTED.** `app/build_info.json` is gitignored; a version living only there
  resets to 0.001 on a clean checkout and two deploys claim the same number.
- ⛔ **Integer thousandths, never a float.** 0.001 steps reach `0.029000000000000002`; the test
  runs both and compares.
- ⛔ **`deploy.sh` bumps BEFORE baking** — `gen_build_info.py` reads `VERSION`, so bumping after
  ships the previous number and the footer claims a build that is not live.
- ⛔ **Absent stamp renders "dev build", never a guess.** Three signed-out routes built their
  context by hand and would have shown that on a real deploy; a static guard now fails the build
  on any hand-built `TemplateResponse` context.

### IA — Settings is gone from the top nav
**Today · Markets · Positions · Performance.** Settings became **Admin → Data sources**
(owner-gated — it holds broker credentials); `/settings` is a **301**, not a 404. Avatar →
**Profile · Admin console · Sign out**.

`/profile`: name, email, role, last sign-in, active sessions, change password.
⛔ The **current password is required** even though the caller is signed in — a live session is not
proof of identity at the keyboard. Success revokes every other session and **re-mints this one**,
or you get signed out of the device you just used. Role and email are not editable.

Admin console uses LC's `AdminLayout.tsx` shape — grouped left rail (Data / Access / System),
longest-prefix active — server-rendered, no collapse toggle (six items, not fourteen).

### System documentation — `docs/system/`, LexDana's SystemDocs shape
13 numbered documents, each stamping **Applies to build**, rules written as `> **Invariant:**`.
Viewer: grouped index (order IS the IA — alphabetical buried `01 — Overview` between
`DATA-SOURCES` and `SCHWAB-SETUP`), **search that ranks by hit count**, build badge.

- ⚠ **Search filters the INDEX, not the open document** — so you can search a term, see which
  pages carry it, and keep reading the one you were on.
- ⛔ **The renderer was dropping table headers** — every row emitted as `<td>`. And 19 cross-doc
  `./NN-x.md` links were dead; they now become `/admin/docs?doc=<slug>`, and a test asserts every
  one resolves.
- ⚠ **My first mutation proof for the header fix was wrong**: it asserted a single-row table has no
  header, and a single row **is** the header — so it failed against correct code. A proof that
  fails on working code proves nothing.

### ⚠ IAP IS STILL IN FRONT OF THE APP — two sign-ins
`run.googleapis.com/iap-enabled: true`, and the only `run.invoker` is the IAP service account. So
`argentridge.com` gates at **Google IAP** first and then at the app's **own password**.
`VANTAGE_IAP_MODE` is unset, so the app does **not** trust the IAP header — both are real gates.

This is defence in depth and costs nothing ($18/mo LB is avoided either way, per
[[cloud-run-domain-mapping-no-lb]]), but it means signing in twice. **Not changed unilaterally —
removing IAP weakens a control and is Pedram's call.**

---

## Research, autocomplete, and the REAL cause of slow navigation (2026-09-20, V0.004)

### ⛔ Profiled every route. The database was not the problem — again.
| route | before | after |
|---|---:|---:|
| `/` (Today) | 1,353 ms | **159 ms** |
| `/markets` | 357 ms | **1 ms** |
| `/admin` | 3,594 ms | **1 ms** |
| `/performance` | 3 ms | 1 ms |

**`levels_for_session()` and `events_for()` were called ONCE PER SYMBOL** inside `build_symbol`
— eight Firestore round trips at ~150 ms on every load of a four-symbol Today page, long after
the bar cache went warm. And `ensure_seed()` was a round trip on **every** hit of `/` and
`/markets`, to ask a question whose answer can never become false again.

`core/store.py` now has `_cached()/_bust()`; every writer busts. Same for watchlists, the doc
list (baked into the image — it cannot change while the process runs) and `user_count`.

⭐ **Pedram said "if you need to add sql database, please do it." It would have fixed nothing
measured here.** 771 trades and 40 articles fit in memory; the cost was N round trips on the
request path, not the store.

### Symbol autocomplete — `core/symbol_search.py`
Your own watchlist + traded symbols first, then **Yahoo's symbol index** (`/v1/finance/search`),
merged, cached 15 min, ~150 ms. Keyboard navigable; the plain POST form still works with JS off.
⛔ A typed ticker list would offer symbols that no longer resolve and omit everything listed
since it was written, with nothing going red.

### Research — `core/research.py`, `core/llm.py`, `/research`
Left rail **Articles · Tweets · News · Charts · Sources**. Article saved in full on the left, a
Gemini read on the right: SPY and QQQ, next 24 h and through Friday.

- ⚠ **Substack has NO public API.** RSS only. Measured 2026-09-20: `smashelito` full text
  4,746–6,561 chars · `tictoctrading` mixed, paid posts truncated to ~210 · **`tictoc` is a dead
  2020 stub, the real one is `tictoctrading`**. 40 articles in, **12 paywalled**.
- ⛔ **A teaser is NEVER summarised.** A confident read of an advertisement is indistinguishable
  from a read of the analysis. `PAYWALL_CHARS = 700` sits between the measured teaser (~210) and
  the shortest real post (2,843); a test asserts it stays there.
- ⛔ **Every direction is `up/down/choppy/not stated` and carries `basis`** — the sentence it
  rests on. The first live read returned **`not stated` for QQQ** on an ES-only article. That is
  the feature working.
- ⛔ **Reads are ~18 s and run in a daemon thread**; the route starts a job and polls. A test
  asserts the route never calls `summarise` directly — that is the deleted AI panel's defect.
- ⛔ **`refresh()` skips ids it already holds** — overwriting would drop `read` on every poll.
- **Gemini 2.5 Pro on Vertex**, service-identity auth, **no API key anywhere**. ⚠
  `aiplatform.googleapis.com` had to be **enabled** on `patexia-vantage` (403 until then).
  ⚠ The model id is resolved once per process from a preference list, never pinned in code.
- ⚠ List renders a **1,400-char preview** + full-text link: **218 KB → 51 KB**.
- **Tweets / News are explicit empty states.** No sample feed.

### ⚠ The Anthropic guard was matching PROSE
It went red on `core/llm.py`, whose docstring exists to state the rule. A guard that punishes
documenting the rule is a guard people delete. It now matches the **eight ways the dependency
could actually return** (import, client construction, API host, key env var, secret name,
`claude-*` model id, the deleted module), each with its own proof — plus a proof that prose
about the rule is not a violation.
