# Connecting Schwab to Vantage

What this unlocks, in one line each:

| | Without Schwab | With Schwab |
|---|---|---|
| **Live prices** | Yahoo, **10 minutes delayed**, one HTTP call per symbol (lists >12 truncated) | Real-time, **whole watchlist in one call** |
| **Positions** | nothing — the page says "not connected" | your open holdings, marked against the day's zones |
| **Transactions** | nothing | **rolling last 60 days**, synced daily |

⚠ **Schwab does not replace Yahoo.** It has no price *history* for futures, so session
profiles keep coming from Yahoo. And its API reaches back only **60 days** for transactions,
so your full trading history still needs the CSV export (see the end of this doc).

---

## ⛔ Part 0 — PRODUCT ACCESS COMES FIRST (and it is its own approval)

**Corrected 2026-09-19.** This doc originally started at "Create App". That was wrong, and
Pedram hit the real wall: **you cannot create an app until Schwab has approved your access to
the API product.** There are TWO separate approvals, days apart:

```
  1. Subscription:  request access to "Trader API - Individual"   <- Schwab approves
  2. App:           create an app under that product              <- Schwab approves again
```

**Where to look:** Dashboard -> **Subscriptions** tab shows the request and its Access status.
API Products -> *Trader API - Individual* shows the same status in the top right.

**Trader API - Individual** is the individual-developer line of business; it bundles **both**
Market Data Production and Accounts and Trading Production. One request covers both — which is
why Part 1 step 2 below has nothing left to choose if this is still pending.

### How to tell you are blocked here

| What you see | Meaning |
|---|---|
| Subscriptions: **Pending** | waiting on Schwab; nothing to click |
| API Products: buttons greyed, **"Pending - Request Access"** | same — the request is already in, do not re-request |
| Create App: **"You do not have access to any Active API products"**, empty dropdown | the symptom of the above, not a separate problem |
| Apps Pending Approval: empty | expected — no app can exist yet |

⚠ **Do not re-submit the request.** A duplicate does not jump the queue and may reset it.

### How long

⚠ **The "1–3 business days" figure below applies to the APP approval, not this one.**
Community reports put the *product access* step anywhere from a few days to **about a month**.

**If it has been sitting more than ~a week, email `traderapi@schwab.com`** — that is the
official Trader API support channel. Include your account name and that *Trader API -
Individual* is showing Pending in Subscriptions. There is no self-service escalation.

---

## Part 1 — Register the app (only once Part 0 says approved; ~10 min, then another wait)

### 1. Create the app

Go to **https://developer.schwab.com** -> sign in with your Schwab credentials ->
**Dashboard -> Apps -> Create App**.

### 2. Select the API product

Pick **Trader API - Individual** (it bundles Market Data Production *and* Accounts and Trading
Production). If the dropdown is empty, you are still in Part 0 — nothing here is
misconfigured.

### 3. Callback URL — this must be EXACT

```
https://vantage-424459368059.us-central1.run.app/schwab/callback
```

⚠ **Copy it character for character.** Schwab requires an exact string match — a trailing
slash, `http` instead of `https`, or a different host all cause the login to fail with an
unhelpful error. It must be HTTPS and 256 characters or fewer. Vantage sends exactly this
string (pinned as `VANTAGE_BASE_URL`, not derived from the request, precisely so it cannot
drift).

### 4. Order limit

Set **120 requests per minute** — the standard value. Vantage is far below it: one batched
quote call per refresh, not one per symbol.

### 5. Name and description

Anything descriptive, e.g. *"Vantage — personal market dashboard"*. A human at Schwab reads
this during review.

### 6. Wait for approval — and watch the status word

Approval is manual and takes **1–3 business days** — this is the *app* approval, and it is
the second of the two waits (see Part 0).

| Status | Means |
|---|---|
| `Approved - Pending` | ⛔ **NOT usable yet**, despite the word "Approved" |
| `Ready For Use` | ✅ usable |

Do not try to connect while it says *Approved - Pending* — it will fail and look like a
configuration problem.

---

## Part 2 — Connect it to Vantage (2 minutes, once it says Ready For Use)

1. Open the app's page on developer.schwab.com and copy the **App Key** (sometimes labelled
   Consumer Key) and the **App Secret**.
2. Go to **https://vantage-424459368059.us-central1.run.app/settings**
3. Paste both into the **Schwab** card and press **Save**.
   They go straight into Google Secret Manager. The secret is never displayed again and never
   appears in logs or in the page source.
4. Press **Connect Schwab →**. You'll be sent to Schwab, log in, approve the account, and land
   back on Settings.
5. The card should read **Connected**, with a live price next to it.

If it says *Linked, no quote*, the token has expired — press **Reconnect**.

---

## Part 3 — The weekly re-login (the one real annoyance)

⛔ **Schwab's refresh token expires every 7 days.** There is no programmatic renewal; this is
Schwab's design, not a limitation of Vantage.

- **What breaks:** live real-time quotes. The Settings card flips to *Linked, no quote*.
- **What does NOT break:** everything else. Prices fall back to Yahoo and are **labelled
  delayed** rather than silently going stale; the profiles, zones, maps and the 5 AM email all
  keep working, because history comes from Yahoo regardless.
- **The fix:** Settings → **Reconnect Schwab** → log in. About 20 seconds, once a week.

Vantage is deliberately built so a dead Schwab token degrades the display rather than stopping
the product.

---

## Part 4 — Transactions

Once **Accounts and Trading Production** is live, Vantage can sync transactions — but only the
**rolling last 60 days**. `start_date` is capped at 60 days by Schwab; there is no parameter
that reaches further back.

So trade history is two legs:

1. **Backfill — a CSV you export once.** schwab.com → **Accounts → History → Transactions** →
   set the range to **one calendar year** → Export. Repeat per year back to when you opened the
   account.
   ⚠ **When an export exceeds Schwab's row cap it downloads EMPTY instead of erroring.** That is
   why one year at a time — and why Vantage rejects a zero-row file as a suspected truncation
   rather than recording it as "no trades that year".
2. **Ongoing — the daily API sync**, covering the last 60 days automatically. The two overlap
   on purpose; rows are matched on transaction id, so re-importing is harmless.

### ⚠ One thing to check when you first look

**Are your ES futures and options-on-futures trades actually in that account's history?**
Schwab's API is documented as equities and options only for orders, and futures sit on a
separate platform. If your ES trades are absent from both the API and the export, the CSV
becomes the only source and the daily sync covers nothing that matters. This cannot be verified
without your account — worth checking before we invest in the sync half.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| "You do not have access to any Active API products" | Part 0 — the product subscription is still Pending. Not an app problem |
| Login redirects to an error page | Callback URL mismatch — re-copy it exactly from Part 1 step 3 |
| `invalid_client` | App still `Approved - Pending`, or the key/secret was pasted with whitespace |
| Connected, but prices still say Yahoo | Token expired — Reconnect (Part 3) |
| Quotes work, positions empty | Only *Market Data Production* was enabled; add *Accounts and Trading Production* (new approval wait) |
| Futures quote missing | Schwab futures symbols are `/ES`, not `ES` — Vantage handles this automatically |

---

## What Vantage never does with these credentials

- Never places, modifies or cancels an order. Vantage only reads.
- Never stores the app secret or the refresh token anywhere but Secret Manager.
- Never displays the secret after you save it.
- Never sends credentials to any third party — Schwab is called directly from the Cloud Run
  service.
