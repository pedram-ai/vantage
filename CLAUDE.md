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

Email renderer + Gmail send (the Claude scheduled task `Daily ES Action Map` still
does the 5 AM email; keep until parity — handoff §9 M6), push notification,
Substack/X scrapers + LLM level extraction, portfolio module, history scorecard.
Author levels are entered manually at `/levels` for now.
