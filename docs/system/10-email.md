# 10 — Email

*The weekday briefing, the transport, and the archive of everything sent.*

**Applies to build:** `0.002` · **Last reviewed:** 2026-09-20

---

## 1. Transport

**AWS SES v2**, via `core/mailer.py` — hand-rolled SigV4 over `curl_cffi`, no boto3 dependency.

It reuses Connect's existing verified sender `notify.patexia.com`. The Vantage compute service
account was granted `secretmanager.secretAccessor` on Connect's AWS key secrets **in
patexia-connect**, and Cloud Run references them cross-project.

> **Invariant:** cross-project secret references must use the project **NUMBER**
> (`projects/349112552843/secrets/...`). The project **ID** form is rejected as "not a valid secret
> name".

No credential was copied. There is still exactly one key.

## 2. ⛔ No email on Saturday or Sunday

Two independent guards:

1. the Scheduler cron is `0 5 * * 1-5`;
2. `jobs/daily_run.py` **refuses to send** when `weekday() >= 5`, regardless of what triggered it.

> **Invariant:** the app-level guard is not redundant. A manual job execution, a retry, or a
> changed cron would otherwise send on a weekend. Override for testing only with `--force-email`.

## 3. ⛔ What the email may not contain

> **Invariant:** no SPY or SPX numbers, strikes or expiries. The email is ES-only; SPY lives on
> the platform. This is asserted by `tests/test_email_guard.py`.

> **Invariant:** no images, no SVG, no scripts, and no `background:` shorthand. Mail clients strip
> or mangle all of them. The guard test **carries its own mutation proof** — it injects an `<img>`
> and a SPY price and fails if the guard does not catch them.

## 4. Archive

`mailer.send_html(to, subject, html, kind=...)` wraps the real sender and writes to the `emails`
collection.

> **Invariant:** archiving happens at that single choke point, so nothing can send unlogged.

> **Invariant:** setup and reset tokens are **redacted** in the archive (`/setup/<token>` →
> `/setup/[redacted]`). An admin browsing sent mail must not be able to lift a live
> password-reset link.

## 5. Where to read a briefing

The rendered HTML is archived on the run document and viewable at `/history/<id>/email`.
`/email-preview` renders today's without sending.

First live send verified 2026-09-18, SES message id `010001a0b6ab5af9-…`.
