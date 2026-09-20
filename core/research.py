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


# Substack serves the same post under several URLs: the publication URL, the
# reader URL (`substack.com/home/post/p-<id>`), and either with tracking query
# strings. They hash differently, so without normalising, pasting from the
# reader creates a DUPLICATE instead of upgrading the post already held.
# ⚠ Measured 2026-09-20: that is exactly what happened on the first real use.
_READER_URL = re.compile(r"^https?://(?:www\.)?substack\.com/(?:home/post|p)/", re.I)


def canonical_url(url: str) -> str:
    """The publication URL for a post. Falls back to the input, cleaned."""
    url = (url or "").strip()
    if not url:
        return ""
    # strip tracking + fragment first — they never identify a different post
    url = url.split("#", 1)[0]
    if "?" in url:
        head, q = url.split("?", 1)
        keep = [kv for kv in q.split("&")
                if kv.split("=", 1)[0].lower() not in
                ("utm_source", "utm_medium", "utm_campaign", "utm_content",
                 "utm_term", "r", "showwelcome", "triedredirect", "post_id",
                 "publication_id", "isfreemail", "token", "email")]
        url = head + ("?" + "&".join(keep) if keep else "")
    url = url.rstrip("/")

    if _READER_URL.match(url):
        # ⚠ One network call, and only for the reader form. It 200s and lands
        # on the publication URL; if it fails we keep what we were given
        # rather than inventing a canonical form.
        try:
            from curl_cffi import requests as cr
            r = cr.get(url, impersonate="chrome", timeout=15, allow_redirects=True)
            final = str(r.url).split("#", 1)[0].split("?", 1)[0].rstrip("/")
            if final and "substack.com" in final and not _READER_URL.match(final):
                return final
        except Exception:  # noqa: BLE001
            pass
    return url


def _strip_page_chrome(body: str, teaser: str = "") -> str:
    """Remove the header a browser copy picks up above the post itself.

    ⭐ THE TEASER WE ALREADY HOLD IS THE ANCHOR. Its opening words are the
    post's real first words, so finding them in the pasted text says exactly
    where the article begins — no guessing at what a byline looks like.

    With no teaser, only lines that are plainly chrome are dropped, and the cut
    is abandoned if it would remove more than a quarter of the text. Losing
    real analysis is worse than keeping a byline.
    """
    body = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        return body

    anchor = " ".join((teaser or "").split())[:60].strip()
    if len(anchor) >= 25:
        flat = " ".join(body.split())
        i = flat.find(anchor)
        if i > 0:
            # map the position in the flattened text back to the real one
            words = anchor.split()
            j = body.find(words[0])
            while j > 0:
                if " ".join(body[j:j + 400].split()).startswith(anchor):
                    return body[j:].strip()
                j = body.find(words[0], j + 1)

    lines = body.split("\n")
    drop = 0
    for k, ln in enumerate(lines[:8]):
        t = ln.strip()
        if not t:
            drop = k + 1
            continue
        # publication name, title, byline, a bare date, share/subscribe chrome
        if (len(t) < 60 and (re.match(r"(?i)^(share|subscribe|listen|\d+ likes?|"
                                      r"\d+ comments?)\b", t)
                             or re.match(r"(?i)^[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}$", t)
                             or t.endswith("Newsletter"))):
            drop = k + 1
        else:
            break
    if drop and drop < len(lines) and len("\n".join(lines[drop:])) > len(body) * 0.75:
        return "\n".join(lines[drop:]).strip()
    return body


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

    url = canonical_url(url)
    aid = (hashlib.sha256(url.encode()).hexdigest()[:24] if url
           else hashlib.sha256(f"manual:{title}".encode()).hexdigest()[:24])

    existing = get_article(aid) or {}
    # ⛔ Strip the page furniture a browser copy drags in, using the teaser we
    # already hold as the anchor for where the post actually starts.
    body = _strip_page_chrome(body, existing.get("body", "") if existing.get("paywalled") else "")
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


def _within(rows: list[dict], days: int) -> list[dict]:
    """⚠ days=0 means ALL, deliberately — a filter that silently defaults to a
    window would hide articles and look like an empty archive."""
    if not days:
        return rows
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [r for r in rows if (r.get("published_at") or "") >= cutoff]


def timeline(source: str = "", days: int = 0) -> list[dict]:
    """The left rail: every article newest-first, grouped by month."""
    rows = _within(_list_all(source), days)
    out, seen = [], None
    for r in rows:
        month = (r.get("published_at") or "")[:7] or "unknown"
        if month != seen:
            out.append({"month": month, "heading": True,
                        "label": _month_label(month)})
            seen = month
        rd = r.get("read") or {}
        out.append({
            "heading": False, "id": r["id"], "title": r.get("title", ""),
            "day": (r.get("published_at") or "")[8:10],
            "date": r.get("published_at"),
            "bias": rd.get("bias"), "paywalled": r.get("paywalled"),
            "read": bool(rd),
        })
    return out


def _month_label(m: str) -> str:
    from datetime import datetime
    try:
        return datetime.strptime(m, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return m


def list_articles(limit: int = 60, source: str = "", preview: bool = False,
                  page: int = 1, per_page: int = 12, days: int = 0) -> dict | list[dict]:
    """`preview=True` returns a PAGE of articles with shortened bodies.

    ⚠ The full set is 40 posts of up to 13k characters — rendering every body
    made the page a 218 KB document to show twelve headlines. The list carries
    a preview and links to the full text; nothing is hidden, it is one click.
    """
    rows = _within(_list_all(source), days)
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

SYSTEM = """You read one market-commentary article and extract the trade it \
describes, for SPY.

These articles are usually written about ES futures or SPX. That is fine — a \
view on ES or SPX IS a view on SPY. Report every price EXACTLY AS THE ARTICLE \
WROTE IT and say which instrument it is in; the conversion to SPY is done \
afterwards in code, with the exchange rate that applied on the day the article \
was published.

⛔ NEVER convert a price yourself. Never divide ES by 10. Never compute a \
percentage. Report the numbers on the page.

Hard rules:
- Only what the article states or plainly implies. Never invent a level, a \
target, a date or a view it does not contain.
- `basis` must quote the sentence you relied on. No sentence ⇒ "not stated".
- Most daily plans are CONDITIONAL — "above X target Y, below X target Z". \
That is shape "range" with the pivot as `activation`, not a direction. Only \
call it directional when the article actually commits to one side.
- `reference_level` is the price the article treats as "here" — the last \
close, the spot, or the pivot it builds everything around.
- No advice, no recommendation, no target of your own."""

_LEVELS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "description": "What the article calls it."},
            "price": {"type": "number", "description": "Verbatim, in the article's units."},
            "kind": {"type": "string",
                     "enum": ["support", "resistance", "pivot", "target", "other"]},
        },
        "required": ["label", "price", "kind"],
    },
}


def _leg(desc: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "shape": {"type": "string", "enum": ["directional", "range", "not stated"],
                      "description": "range = conditional on a pivot."},
            "direction": {"type": "string", "enum": ["up", "down", "none"],
                          "description": "Only meaningful when shape is directional."},
            "target": {"type": "number",
                       "description": f"Where it goes, in the article's units — {desc}. "
                                      "Omit if none is given."},
            "range_low": {"type": "number", "description": "Lower bound, article's units."},
            "range_high": {"type": "number", "description": "Upper bound, article's units."},
            "activation": {"type": "string",
                           "description": "What has to happen first, in words — the trigger "
                                          "or pivot. Empty if unconditional."},
            "activation_level": {"type": "number",
                                 "description": "The trigger PRICE, in the article's own "
                                                "units. Omit if there is no single level."},
            "activation_low": {"type": "number",
                               "description": "If the trigger is a zone, its lower bound, "
                                              "article's units."},
            "activation_high": {"type": "number",
                                "description": "If the trigger is a zone, its upper bound."},
            "note": {"type": "string", "description": "One sentence, plain."},
            "basis": {"type": "string", "description": "The sentence this rests on."},
        },
        "required": ["shape", "direction", "activation", "note", "basis"],
    }


SCHEMA = {
    "type": "object",
    "properties": {
        "gist": {"type": "string", "description": "One or two sentences."},
        "quoted_in": {"type": "string", "enum": ["ES", "SPX", "SPY", "unclear"],
                      "description": "Which instrument the article's prices are in."},
        "reference_level": {"type": "number",
                            "description": "The price the article treats as 'here', "
                                           "in its own units."},
        "bias": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
        "conviction": {"type": "string", "enum": ["high", "medium", "low"]},
        "h24": _leg("over the next 24 hours"),
        "week": _leg("through the end of this week"),
        "levels": _LEVELS,
    },
    "required": ["gist", "quoted_in", "bias", "conviction", "h24", "week", "levels"],
}


def _convert_read(raw: dict, published_at: str | None) -> dict:
    """Turn the model's verbatim numbers into SPY levels. ⛔ ALL ARITHMETIC IS HERE."""
    from . import convert

    unit = raw.get("quoted_in") or "unclear"
    r = convert.ratio_on((published_at or "")[:10] or None)
    out = dict(raw)
    out["ratio"] = round(r["es_spy"], 4)
    out["ratio_asof"] = r.get("asof")
    out["ratio_measured"] = r.get("measured", False)

    def conv(v):
        spy = convert.to_spy(v, unit, r)
        # ⛔ A level that fails the band did not convert — most likely the unit
        # was wrong. Drop it rather than render 7697 as a SPY price.
        return spy if convert.plausible_spy(spy) else None

    ref = conv(raw.get("reference_level"))
    out["spy_ref"] = ref

    for key in ("h24", "week"):
        leg = dict(raw.get(key) or {})
        leg["spy_target"] = conv(leg.get("target"))
        leg["spy_low"] = conv(leg.get("range_low"))
        leg["spy_high"] = conv(leg.get("range_high"))
        # ⛔ The trigger is a PRICE and must convert like any other. It shipped
        # in the prototype as a bare ES number sitting in a SPY sentence.
        leg["spy_activation"] = conv(leg.get("activation_level"))
        leg["spy_act_low"] = conv(leg.get("activation_low"))
        leg["spy_act_high"] = conv(leg.get("activation_high"))
        # move, as a signed percentage of the reference — derived, never asked for
        t = leg["spy_target"]
        leg["move_pct"] = (round((t - ref) / ref * 100, 2)
                           if (t is not None and ref) else None)
        leg["move_pts"] = round(t - ref, 2) if (t is not None and ref) else None
        out[key] = leg

    lv = []
    for l in (raw.get("levels") or []):
        spy = conv(l.get("price"))
        if spy is None:
            continue
        lv.append({**l, "spy": spy})
    out["levels"] = lv
    return out


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
    out = _convert_read(out, article.get("published_at"))

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


# --- synthesis --------------------------------------------------------------

def _cluster(levels: list[float], tol: float = 1.5) -> list[list[float]]:
    """Group SPY levels that are effectively the same price.

    ⚠ Two authors writing 7697 and 7700 in ES are talking about the same
    shelf; at ~10.1 to the point that is 0.3 SPY apart. Listing them separately
    would imply two levels where the market has one. `tol` is in SPY points.

    ⛔ THE WIDTH OF A CLUSTER IS BOUNDED, NOT THE GAP BETWEEN NEIGHBOURS.
    Comparing each price to the PREVIOUS one chains: a ladder of levels each
    1.4 apart merges end to end, and the real book produced a single fake
    level reading "765.99 ×63" spanning tens of points. Comparing to the
    cluster's FIRST member caps every cluster at `tol` wide, so a cluster is
    always a price you could actually trade against.
    """
    out: list[list[float]] = []
    for p in sorted(levels):
        if out and p - out[-1][0] <= tol:
            out[-1].append(p)
        else:
            out.append([p])
    return out


def outlook(days: int = 10, now_spy: float | None = None) -> dict:
    """What the recent articles, taken together, say about SPY.

    ⛔ THIS IS ARITHMETIC OVER WHAT WAS EXTRACTED, NOT A SECOND OPINION. No
    model runs here. Every figure traces to an article you can open, and the
    count of articles behind each one is shown — a consensus of two is not a
    consensus and must not look like one.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = [a for a in _list_all()
            if isinstance(a.get("read"), dict)
            and "spy_ref" in a["read"]
            and (a.get("published_at") or "") >= cutoff]

    if not rows:
        return {"empty": True, "days": days, "n": 0}

    bias = {"bullish": 0, "bearish": 0, "neutral": 0}
    for a in rows:
        b = (a["read"].get("bias") or "neutral").lower()
        if b in bias:
            bias[b] += 1

    def leg_view(key: str) -> dict:
        legs = [a["read"].get(key) or {} for a in rows]
        shapes = [l.get("shape") for l in legs]
        targets = [l["spy_target"] for l in legs if l.get("spy_target") is not None]
        lows = [l["spy_low"] for l in legs if l.get("spy_low") is not None]
        highs = [l["spy_high"] for l in legs if l.get("spy_high") is not None]
        ups = sum(1 for l in legs if l.get("direction") == "up"
                  and l.get("shape") == "directional")
        downs = sum(1 for l in legs if l.get("direction") == "down"
                    and l.get("shape") == "directional")
        return {
            "n": len(legs),
            "stated": sum(1 for s in shapes if s and s != "not stated"),
            "ranges": sum(1 for s in shapes if s == "range"),
            "up": ups, "down": downs,
            # ⛔ MEDIAN, not mean. One article quoting a far target would drag
            # a mean somewhere no author actually named.
            "target": (sorted(targets)[len(targets) // 2] if targets else None),
            "targets_n": len(targets),
            "low": min(lows) if lows else None,
            "high": max(highs) if highs else None,
            "band_n": len(lows) + len(highs),
        }

    # levels, clustered and ranked by how many articles name them
    named: list[tuple[float, str, str]] = []
    for a in rows:
        for l in (a["read"].get("levels") or []):
            if l.get("spy") is not None:
                named.append((float(l["spy"]), l.get("kind", "other"),
                              a.get("title", "")))
    groups = _cluster([p for p, _, _ in named])
    levels = []
    for g in groups:
        mid = round(sum(g) / len(g), 2)
        kinds = [k for p, k, _ in named if g[0] - 0.01 <= p <= g[-1] + 0.01]
        kind = max(set(kinds), key=kinds.count) if kinds else "other"
        levels.append({"spy": mid, "n": len(g), "kind": kind,
                       "above": (now_spy is not None and mid > now_spy)})
    levels.sort(key=lambda r: (-r["n"], r["spy"]))

    return {
        "empty": False, "days": days, "n": len(rows),
        "bias": bias,
        "lean": max(bias, key=bias.get) if any(bias.values()) else "neutral",
        "h24": leg_view("h24"),
        "week": leg_view("week"),
        # ⛔ `levels` is the TOP-N BY ARTICLE COUNT, for a list.
        # `levels_all` is EVERYTHING, for the ladder — which selects by
        # proximity to price itself. Handing the ladder a pre-truncated set
        # sorted by price gave it only levels ABOVE the market, so the "now"
        # marker sat at the bottom with nothing beneath it.
        "levels": levels[:10],
        "levels_all": sorted(levels, key=lambda r: -r["spy"]),
        "newest": rows[0].get("published_at"),
        "oldest": rows[-1].get("published_at"),
    }
