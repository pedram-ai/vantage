"""Research — sources in, a read on SPY and QQQ out.

Shape: the ARTICLE is saved here in full (Firestore `articles`), and beside it
sits a model-written box saying what it implies for SPY and QQQ over the next
24 hours and to the end of the week.

⛔ SUMMARIES ARE NEVER GENERATED ON THE REQUEST PATH. Gemini takes seconds; the
page reads what is already stored and shows "not summarised yet" otherwise.
This codebase already shipped an LLM panel that cost 3.5 s on every render and
had to be deleted — see docs/system/08.

⛔ THE MODEL SUMMARISES THE ARTICLE. It does not forecast. The prompt forbids
inventing a level, a number or a view the article does not contain, and every
read carries `basis` — the sentence it rests on — so a fabrication is visible
rather than plausible.

⚠ SUBSTACK HAS NO PUBLIC API. RSS (`https://<pub>.substack.com/feed`) is the
route: no key, no account, ~20 most recent posts. Measured 2026-09-20:
smashelito returns full text (4.7k–6.6k chars); tictoctrading is mixed, with
paid-only posts truncated to ~200 characters. A truncated post is **marked
paywalled and never summarised** — summarising a teaser produces a confident
read of an advertisement.
"""

from __future__ import annotations

import hashlib
import html as _html
import re
from datetime import datetime, timedelta, timezone

from .profile import PT
from .store import _bust, _cached, db, now_iso

ARTICLES = "articles"
SOURCES = "research_sources"

# Verified live 2026-09-20 — `tictoc` is a dead 2020 stub, the real one is
# `tictoctrading`. Seeded, not hardcoded: they are editable in the UI.
SEED_SOURCES = [
    {"id": "smashelito", "kind": "substack", "handle": "smashelito",
     "label": "Smashelito · ES/SPX", "enabled": True},
    {"id": "tictoctrading", "kind": "substack", "handle": "tictoctrading",
     "label": "Tic Toc · OrderFlow", "enabled": True},
]

# Below this, a Substack item is a paywall teaser rather than a post.
PAYWALL_CHARS = 700


# --- sources ----------------------------------------------------------------

def seed_sources() -> None:
    col = db().collection(SOURCES)
    if any(True for _ in col.limit(1).stream()):
        return
    for s in SEED_SOURCES:
        col.document(s["id"]).set({**s, "created_at": now_iso()})
    _bust("sources")


def all_sources() -> list[dict]:
    def load():
        out = []
        for doc in db().collection(SOURCES).stream():
            d = doc.to_dict()
            d["id"] = doc.id
            out.append(d)
        return sorted(out, key=lambda r: r.get("label", r["id"]))
    return [dict(r) for r in _cached("sources", load)]


def add_source(kind: str, handle: str, label: str = "") -> str:
    handle = (handle or "").strip().lower()
    if not handle:
        raise ValueError("A handle is required.")
    # accept a pasted URL as well as a bare handle
    m = re.search(r"https?://([\w-]+)\.substack\.com", handle)
    if m:
        handle = m.group(1)
    sid = re.sub(r"[^a-z0-9-]", "", handle) or "source"
    db().collection(SOURCES).document(sid).set({
        "kind": kind or "substack", "handle": handle,
        "label": label.strip() or handle, "enabled": True,
        "created_at": now_iso(),
    })
    _bust("sources")
    return sid


def set_source_enabled(sid: str, enabled: bool) -> None:
    db().collection(SOURCES).document(sid).set({"enabled": enabled}, merge=True)
    _bust("sources")


def delete_source(sid: str) -> None:
    db().collection(SOURCES).document(sid).delete()
    _bust("sources")


# --- fetching ---------------------------------------------------------------

def _text(xml: str) -> str:
    """Tags out, entities decoded, whitespace collapsed — but paragraph breaks
    kept. ⚠ A bare `\\s+ -> ' '` destroys the structure of a trading plan,
    which is mostly short labelled lines. That exact flattening damaged 8,087
    emails in another repo before anyone noticed."""
    s = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6])[^>]*>", "\n", xml)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


def _tag(block: str, name: str) -> str:
    m = re.search(rf"<{name}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{name}>", block, re.S)
    return m.group(1).strip() if m else ""


def _parse_date(s: str) -> str | None:
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(s.strip(), fmt).astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def fetch_source(src: dict, limit: int = 20) -> list[dict]:
    """Parse one source's feed into article dicts. Never raises."""
    handle = src.get("handle", "")
    url = (f"https://{handle}.substack.com/feed" if src.get("kind") == "substack"
           else handle)
    try:
        from curl_cffi import requests as cr
        r = cr.get(url, impersonate="chrome", timeout=20)
        if r.status_code != 200:
            return []
        xml = r.text
    except Exception:  # noqa: BLE001
        return []

    out = []
    for block in re.findall(r"<item>(.*?)</item>", xml, re.S)[:limit]:
        link = _tag(block, "link")
        title = _text(_tag(block, "title"))
        body = _text(_tag(block, "content:encoded") or _tag(block, "description"))
        if not link or not title:
            continue
        out.append({
            "id": hashlib.sha256(link.encode()).hexdigest()[:24],
            "source": src["id"],
            "source_label": src.get("label", src["id"]),
            "title": title,
            "url": link,
            "published_at": _parse_date(_tag(block, "pubDate")),
            "body": body,
            "chars": len(body),
            # ⛔ A teaser is marked, not summarised. A confident read of an
            # advertisement is worse than an honest gap.
            "paywalled": len(body) < PAYWALL_CHARS,
            "fetched_at": now_iso(),
        })
    return out


def refresh(limit_per_source: int = 20) -> dict:
    """Pull every enabled source. Existing articles keep their summary."""
    seed_sources()
    added = kept = 0
    col = db().collection(ARTICLES)
    for src in all_sources():
        if not src.get("enabled", True):
            continue
        for art in fetch_source(src, limit_per_source):
            ref = col.document(art["id"])
            if ref.get().exists:
                # ⛔ Do NOT overwrite: the stored doc carries the summary, and
                # a re-fetch of unchanged text must not destroy it.
                kept += 1
                continue
            ref.set(art)
            added += 1
    _bust("articles")
    return {"added": added, "already_had": kept, "at": now_iso()}


def add_manual(title: str, body: str, url: str = "", source_label: str = "") -> dict:
    """Save an article pasted in by hand, or UPGRADE a teaser already held.

    ⭐ THIS IS THE PAYWALL ANSWER. Substack has no API and the browser
    extension is blocked from the domain at policy level, so the full text of a
    paid post cannot be fetched automatically. Pasting it in takes one
    copy — and because the id is the hash of the URL, pasting the full text of
    a post we already hold as a 213-character teaser REPLACES the teaser in
    place: same row, same position in the feed, now readable.

    ⛔ It never overwrites a LONGER body with a shorter one. A mis-paste (an
    empty clipboard, a partial selection) must not destroy text already held.
    """
    title = (title or "").strip()
    body = (body or "").strip()
    if not title:
        raise ValueError("A title is required.")
    if len(body) < 200:
        raise ValueError("That is too short to be a post — paste the full text.")

    url = (url or "").strip()
    aid = (hashlib.sha256(url.encode()).hexdigest()[:24] if url
           else hashlib.sha256(f"manual:{title}".encode()).hexdigest()[:24])

    existing = get_article(aid) or {}
    if len(existing.get("body") or "") > len(body):
        raise ValueError(
            f"We already hold a longer version of this ({len(existing['body'])} "
            f"chars vs {len(body)} pasted). Nothing was changed.")

    patch = {
        "id": aid,
        "source": existing.get("source") or "pasted",
        "source_label": (source_label.strip() or existing.get("source_label")
                         or "Pasted in"),
        "title": existing.get("title") or title,
        "url": url or existing.get("url", ""),
        "published_at": existing.get("published_at") or now_iso(),
        "body": body,
        "chars": len(body),
        "paywalled": False,
        "pasted": True,
        "fetched_at": now_iso(),
    }
    # ⛔ merge=True: an existing row keeps its `read` until a new one replaces
    # it, so a paste is never a silent deletion of work already done.
    db().collection(ARTICLES).document(aid).set(patch, merge=True)
    _bust("articles")
    return {"id": aid, "upgraded": bool(existing),
            "was": len(existing.get("body") or ""), "now": len(body)}


# --- reading ----------------------------------------------------------------

PREVIEW_CHARS = 1400


def list_articles(limit: int = 60, source: str = "", preview: bool = False,
                  page: int = 1, per_page: int = 12) -> dict | list[dict]:
    """`preview=True` returns a PAGE of articles with shortened bodies.

    ⚠ The full set is 40 posts of up to 13k characters — rendering every body
    made the page a 218 KB document to show twelve headlines. The list carries
    a preview and links to the full text; nothing is hidden, it is one click.
    """
    rows = _list_all(source)
    if not preview:
        return rows[:limit]
    per_page = max(1, per_page)
    page = max(1, page)
    total = len(rows)
    out = []
    for r in rows[(page - 1) * per_page: page * per_page]:
        body = r.get("body") or ""
        r = dict(r)
        r["truncated"] = len(body) > PREVIEW_CHARS
        r["body"] = body[:PREVIEW_CHARS]
        out.append(r)
    return {"rows": out, "page": page, "per_page": per_page, "total": total,
            "pages": max(1, (total + per_page - 1) // per_page)}


def _list_all(source: str = "") -> list[dict]:
    def load():
        out = []
        for doc in db().collection(ARTICLES).stream():
            d = doc.to_dict()
            d["id"] = doc.id
            out.append(d)
        return sorted(out, key=lambda r: (r.get("published_at") or ""), reverse=True)
    rows = [dict(r) for r in _cached("articles", load)]
    if source:
        rows = [r for r in rows if r.get("source") == source]
    return rows


def get_article(aid: str) -> dict | None:
    doc = db().collection(ARTICLES).document(aid).get()
    if not doc.exists:
        return None
    d = doc.to_dict()
    d["id"] = doc.id
    return d


def counts() -> dict:
    rows = list_articles(limit=10_000)
    return {
        "total": len(rows),
        "summarised": sum(1 for r in rows if r.get("read")),
        "paywalled": sum(1 for r in rows if r.get("paywalled")),
        "pending": sum(1 for r in rows
                       if not r.get("read") and not r.get("paywalled")),
    }


def pt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PT) \
            .strftime("%b %-d · %-I:%M %p PT")
    except Exception:  # noqa: BLE001
        return "—"


# --- the read ---------------------------------------------------------------

SYSTEM = """You read one market-commentary article and report what IT says about \
SPY and QQQ. You are writing for the trader who saved it.

Hard rules:
- Report only what the article states or plainly implies. NEVER invent a price \
level, a number, a date or a view the article does not contain.
- If the article does not address a horizon or an instrument, say so in the \
`note` and set direction to "not stated". An honest gap beats a guess.
- `basis` must quote or closely paraphrase the actual sentence you relied on. \
If you cannot point to one, the direction is "not stated".
- Many of these articles are about ES/SPX futures. ES and SPX track SPY \
closely, so a view on them IS a view on SPY — say which the article used.
- No advice, no recommendation to buy or sell, no price targets of your own.

Write plainly. One or two sentences per field."""

_DIR = {"type": "string", "enum": ["up", "down", "choppy", "not stated"]}


def _horizon(desc: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "direction": _DIR,
            "note": {"type": "string", "description": f"One or two sentences: {desc}"},
            "basis": {"type": "string",
                      "description": "The sentence from the article this rests on, "
                                     "or empty if none."},
        },
        "required": ["direction", "note", "basis"],
    }


SCHEMA = {
    "type": "object",
    "properties": {
        "gist": {"type": "string",
                 "description": "One or two sentences: what this article is about."},
        "spy_24h": _horizon("what it implies for SPY over the next 24 hours"),
        "qqq_24h": _horizon("what it implies for QQQ over the next 24 hours"),
        "spy_7d": _horizon("what it implies for SPY through the end of this week"),
        "qqq_7d": _horizon("what it implies for QQQ through the end of this week"),
        "levels": {"type": "array", "items": {"type": "string"},
                   "description": "Price levels the article names, verbatim, with "
                                  "the instrument. Empty if it names none."},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"],
                       "description": "How clearly the article supports the above."},
    },
    "required": ["gist", "spy_24h", "qqq_24h", "spy_7d", "qqq_7d", "levels", "confidence"],
}

MAX_CHARS = 24_000


def summarise(article: dict, force: bool = False) -> dict:
    """Generate and STORE the read for one article. Returns {'ok', ...}."""
    if article.get("paywalled") and not force:
        return {"ok": False, "reason": "paywalled — only a teaser was published"}
    if article.get("read") and not force:
        return {"ok": True, "cached": True}

    from . import llm
    body = (article.get("body") or "")[:MAX_CHARS]
    if len(body) < 200:
        return {"ok": False, "reason": "too little text to read"}

    prompt = (f"Publication: {article.get('source_label')}\n"
              f"Title: {article.get('title')}\n"
              f"Published: {article.get('published_at')}\n\n{body}")
    out, err = llm.generate(SYSTEM, prompt, SCHEMA)
    if out is None:
        return {"ok": False, "reason": err or "no answer"}

    db().collection(ARTICLES).document(article["id"]).set({
        "read": out,
        "read_at": now_iso(),
        "read_model": llm.status().get("model"),
    }, merge=True)
    _bust("articles")
    return {"ok": True, "read": out}


def summarise_pending(limit: int = 8) -> dict:
    """Summarise the newest unread, non-paywalled articles."""
    done, failed = 0, []
    for a in list_articles(limit=200):
        if done >= limit:
            break
        if a.get("read") or a.get("paywalled"):
            continue
        r = summarise(a)
        if r.get("ok"):
            done += 1
        else:
            failed.append({"title": a.get("title"), "reason": r.get("reason")})
    return {"summarised": done, "failed": failed}


def recent_window_days(n: int = 14) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


# --- background summarising -------------------------------------------------
# ⛔ A read takes ~18 s. Eight of them is two and a half minutes, which is far
# past any HTTP timeout — so the button starts a worker and the page polls a
# status dict. A request that blocks on an LLM is the defect this codebase
# already deleted once.

import threading as _th

_JOB: dict = {"running": False, "done": 0, "total": 0, "started": None,
              "finished": None, "failed": []}
_JOB_LOCK = _th.Lock()


def job_status() -> dict:
    with _JOB_LOCK:
        return dict(_JOB)


def start_summarise(limit: int = 8) -> dict:
    """Begin a background pass. Returns immediately; safe to call twice."""
    with _JOB_LOCK:
        if _JOB["running"]:
            return {"started": False, "reason": "already running", **_JOB}
        pending = [a for a in list_articles(200)
                   if not a.get("read") and not a.get("paywalled")][:limit]
        if not pending:
            return {"started": False, "reason": "nothing pending"}
        _JOB.update({"running": True, "done": 0, "total": len(pending),
                     "started": now_iso(), "finished": None, "failed": []})

    def run():
        for a in pending:
            try:
                r = summarise(a)
                with _JOB_LOCK:
                    if r.get("ok"):
                        _JOB["done"] += 1
                    else:
                        _JOB["failed"].append({"title": a.get("title"),
                                               "reason": r.get("reason")})
            except Exception as e:  # noqa: BLE001
                with _JOB_LOCK:
                    _JOB["failed"].append({"title": a.get("title"),
                                           "reason": str(e)[:120]})
        with _JOB_LOCK:
            _JOB["running"] = False
            _JOB["finished"] = now_iso()

    _th.Thread(target=run, daemon=True).start()
    return {"started": True, "total": len(pending)}
