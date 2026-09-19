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
