# 09 — Admin console

*Every admin page, what it shows, what it can change, and who can reach it.*

**Applies to build:** `0.002` · **Last reviewed:** 2026-09-20

---

## 1. Shell and navigation

`app/templates/admin_base.html` wraps every admin page: a grouped left rail plus a content area.
The shape is copied from Lateral Compass's `AdminLayout.tsx` — the same grouped sections and the
same **longest-prefix active rule**, so `/admin` does not also light up on every sub-page.

LC's version is a React client component with a collapse toggle persisted in `localStorage`. This
one is server-rendered and does not collapse: there are six items, not fourteen, and a toggle that
saved nothing would be decoration.

| Group | Pages |
|---|---|
| **Data** | Data sources |
| **Access** | Users |
| **System** | System health · Documentation |

> **Invariant:** the rail is a UI convenience. Every admin route independently calls
> `require_owner`; a hidden nav item is never the security boundary.

Below 760 px the rail becomes a horizontal strip rather than disappearing.

## 2. Overview — `/admin`

Status, not navigation — the rail already carries the destinations. Five tiles: system level,
monthly cost, account count, commits logged, document count. Every one is read live.

## 3. Data sources — `/admin/sources`

Formerly the top-level **Settings** page. It holds:

- the **Schwab** app key/secret and link status;
- **trade history import**;
- the **ES contract override**;
- the events calendar.

> **Invariant:** it is owner-gated, because it holds broker credentials. `/settings` remains as a
> **301**, not a 404 — a moved page that 404s silently breaks every bookmark, and only the person
> who had the bookmark finds out.

Credentials go straight to Secret Manager and are never echoed back to the page.

## 4. System health — `/admin/health`

`core/sysheath.py`. Method from Lateral Compass: fan out to **independent, fail-soft sources** and
merge, so one dead source renders `—` instead of blanking the page.

Sources: the Cloud Run service, the daily job, Firestore collection counts, data freshness,
external dependencies (Yahoo, Schwab, SES), and cost.

> **Invariant:** `tests/test_admin.py` simulates a total GCP outage and asserts the page still
> renders, reports the problem, and marks the cost as not live.

### ⭐ Cost differs from LC, deliberately

LC's cost module is a hand-maintained table of monthly figures. Here the **resource shape is read
live** from the Cloud Run Admin API — CPU, memory, min/max instances, region — and only the **unit
prices** are a documented constant. The number moves when the infrastructure moves, and the page
states which half is live.

> **Invariant:** the page never presents an estimate as a billing figure. It says so in words.

### ⚠ Two traps that cost real time

> **Cloud Run v2: the overall Ready condition is `terminalCondition`, not an entry in
> `conditions[]`.** `conditions[]` holds only RoutesReady and ConfigurationsReady — looking there
> reports a perfectly healthy service as down.

> **Never name a template-facing dict key `items`.** In Jinja `cost.items` resolves to
> `dict.items`, the method, and the page dies with `'builtin_function_or_method' object is not
> iterable`. The key is `cost.lines`. The same applies to `keys`, `values` and `get`.

The app service account needs `roles/run.viewer` for the live shape.

## 5. Documentation — `/admin/docs`

See [11 — Deploy & versioning](./11-deploy-and-versioning.md) for the change log, and this page
itself for the document set.

## 6. Users — `/users`

Owner-only. Create an account, disable one, delete one, re-issue a setup link.

> **Invariant:** creating a user issues a token; it never sets a password. See
> [03 — Authentication](./03-authentication.md) §4.

## 7. Profile — `/profile`

Not admin — reached from the avatar menu and available to every signed-in user. Name, email, role,
account age, last sign-in, active sessions, and change password.
