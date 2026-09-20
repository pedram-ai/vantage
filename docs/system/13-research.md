# 13 — Research

*Articles in, a read on SPY and QQQ out — and the rules that keep it honest.*

**Applies to build:** `0.004` · **Last reviewed:** 2026-09-20

---

## 1. The shape

Left rail: **Articles · Tweets · News · Charts · Sources**.

On the Articles tab each row is split: the **article as published** on the left, saved here in
full, and on the right a model-written box saying what it implies for **SPY** and **QQQ** over
the **next 24 hours** and **through the end of the week**.

## 2. ⚠ Substack has no public API

RSS is the route — `https://<publication>.substack.com/feed`. No key, no account, about 20
recent posts per publication.

Measured live 2026-09-20:

| feed | items | content |
|---|---:|---|
| `tictoc` | 1 | a dead 2020 stub — **not the real publication** |
| `tictoctrading` | 20 | full text 2,843–13,378 chars; **paid posts truncated to ~210** |
| `smashelito` | 20 | full text 4,746–6,561 chars |

> **Invariant:** a post shorter than `PAYWALL_CHARS` (700) is marked **paywalled** and is never
> summarised. Summarising a teaser produces a confident read of an advertisement — which looks
> exactly like a read of the analysis.

The threshold is not a guess: it sits between the measured teaser length (~210) and the shortest
real post (2,843), and a test asserts it stays there.

## 3. ⛔ The model summarises; it does not forecast

`core/research.py` holds the prompt and the schema. The model receives **one article's text and
nothing else** — no prices, no positions, no other articles.

> **Invariant:** every direction is one of `up · down · choppy · **not stated**`, and every
> horizon carries a **`basis`** — the sentence from the article it rests on. A direction with no
> sentence behind it is `not stated`.

This is what makes a fabrication visible rather than plausible. Verified on the first live read:
the *ES Weekly Plan* returned `choppy` for SPY with the 7697 pivot quoted, and **`not stated` for
QQQ** — correctly, because that article never mentions NQ or QQQ.

> **Invariant:** no advice, no recommendation, no price target of the model's own.

## 4. ⛔ Reads never run on the request path

A read takes about **18 seconds**. Eight of them is two and a half minutes.

> **Invariant:** the button starts a background worker and the page polls a status dict. The page
> renders what is already stored and says "not read yet" otherwise.

This codebase already shipped an LLM panel that cost 3.5 s on every render and had to be deleted
— see [08 — Performance & charts](./08-performance-and-charts.md) §5. A test asserts the route
calls `start_summarise` and never `summarise` directly.

## 5. ⛔ A re-fetch must not destroy a read

`refresh()` skips any article id it already holds.

> **Invariant:** the stored document carries the summary. Overwriting on re-fetch would drop
> every read each time the feed was polled, and nothing would go red.

## 6. The model

**Gemini 2.5 Pro on Vertex AI**, via `core/llm.py`. Auth is the Cloud Run service identity — no
API key exists to store, rotate or leak.

> **Invariant:** Anthropic is not used anywhere in this app (Pedram, 2026-09-20). A test greps
> for imports, client construction, the API host, key names and `claude-*` model ids — **the
> ways it could actually return**, not the word itself, so documenting the rule is not a
> violation.

> **Invariant:** the model id is resolved once per process from a preference list against the
> live endpoint, and `status()` reports which one answered. A pinned id is a constant that starts
> 404-ing on a day nobody deployed anything.

⚠ `aiplatform.googleapis.com` had to be **enabled** on `patexia-vantage` (2026-09-20); before
that every call returned 403.

## 7. The page is a preview, not the archive

> **Invariant:** the list renders the first 1,400 characters per article and links to the full
> text. Rendering all 40 bodies made it a **218 KB** document to show twelve headlines; it is
> **51 KB** now, and nothing is hidden — the full text is one click.

## 8. Tweets, News, Charts

Not connected. They render an explicit empty state.

> **Invariant:** an empty panel that looks populated is worse than one that says it is empty.
> No sample feed, no placeholder headlines.
