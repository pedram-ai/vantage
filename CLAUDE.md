# Vantage — ES/SPY action-map platform

Staff-only web platform per `VANTAGE_HANDOFF.md` (copy in `docs/`). Computes daily
ES (front-month) and SPY session profiles (POC/VAH/VAL/VWAP/session prints), builds
the zone Action Map from profile + author levels, archives a run every weekday.

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

## Not built yet (handoff M2+)

Push notification, Substack/X scrapers + LLM level extraction, portfolio module,
history scorecard. Author levels are entered manually at `/levels` for now.
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
