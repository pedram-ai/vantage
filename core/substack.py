"""Authenticated Substack fetch — the paid posts.

⛔ SUBSTACK HAS NO API AND THE BROWSER EXTENSION IS BLOCKED from the domain at
policy level (tried 2026-09-20). A subscriber session cookie is the only
automatic route to text you have paid for, and Pedram chose it explicitly.

⛔⛔ HOW THE CREDENTIAL IS HANDLED, AND WHY THIS SHAPE:
  * it is pasted into the app's own form and written STRAIGHT to Secret
    Manager — the same path the Schwab app key already takes;
  * it is NEVER rendered back to any page, never logged, never put in a URL,
    and never returned by `status()`;
  * only its LENGTH and a short fingerprint are surfaced, which is enough to
    answer "is the one I saved still the one that's there?" without exposing
    it.

⚠ WHAT THIS CREDENTIAL IS. A Substack session cookie is a bearer token: anyone
holding it is logged in as Pedram on substack.com, with no second factor. It is
stored server-side only, read by the Cloud Run service identity, and it expires
on its own — Substack sessions are not permanent, so `status()` reports the
last time a fetch actually succeeded rather than claiming it is "configured"
forever. A cookie that silently stopped working looks exactly like a
publication that stopped posting.

⛔ IT IS USED FOR ONE THING: reading posts this account subscribes to. Nothing
here writes, comments, subscribes, cancels, or touches account settings.
"""

from __future__ import annotations

import hashlib
import html as _html
import re
import threading
import time

SECRET_NAME = "substack-cookie"
TIMEOUT = 25.0

_LOCK = threading.Lock()
_COOKIE: tuple[float, str | None] | None = None
_COOKIE_TTL = 600.0
_LAST_OK: float | None = None
_LAST_ERROR: str | None = None


# --- the credential ---------------------------------------------------------

def save_cookie(raw: str) -> None:
    """Store the cookie. Accepts a whole `Cookie:` header or a bare value."""
    raw = (raw or "").strip()
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1].strip()
    if len(raw) < 20:
        raise ValueError("That does not look like a session cookie.")
    if "substack.sid" not in raw:
        # Not fatal — Substack has changed cookie names before — but say so.
        raise ValueError(
            "No `substack.sid` in that value. Copy the whole Cookie header for "
            "substack.com, or the substack.sid cookie itself.")
    from .schwab import write_secret
    write_secret(SECRET_NAME, raw)
    with _LOCK:
        global _COOKIE
        _COOKIE = None


def cookie() -> str | None:
    global _COOKIE
    now = time.monotonic()
    with _LOCK:
        if _COOKIE and now - _COOKIE[0] < _COOKIE_TTL:
            return _COOKIE[1]
    try:
        from .schwab import read_secret
        v = read_secret(SECRET_NAME)
    except Exception:  # noqa: BLE001
        v = None
    with _LOCK:
        _COOKIE = (now, v)
    return v


def status() -> dict:
    """⛔ Never returns the cookie. Length and fingerprint only."""
    c = cookie()
    if not c:
        return {"configured": False, "working": None, "detail": "no cookie saved"}
    return {
        "configured": True,
        "chars": len(c),
        "fingerprint": hashlib.sha256(c.encode()).hexdigest()[:8],
        "last_success_ago": (int(time.monotonic() - _LAST_OK) if _LAST_OK else None),
        "last_error": _LAST_ERROR,
        # ⚠ "working" is only ever known from an actual fetch. Having a cookie
        # is not evidence it is still valid.
        "working": True if _LAST_OK else None,
    }


def forget() -> None:
    """Drop the in-process copy. (The stored version is removed in the console.)"""
    global _COOKIE
    with _LOCK:
        _COOKIE = None


# --- fetching ---------------------------------------------------------------

def _clean(fragment: str) -> str:
    """HTML -> text, keeping paragraph structure. Same rule as the RSS parser:
    a bare `\\s+ -> ' '` destroys a trading plan, which is mostly short lines."""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    s = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6]|/blockquote)[^>]*>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


def fetch_post(url: str) -> dict:
    """Full text of one post. Returns {'ok', 'body'|'reason', 'chars'}.

    Never raises, and never returns a paywall teaser as if it were the post.
    """
    global _LAST_OK, _LAST_ERROR
    c = cookie()
    if not c:
        return {"ok": False, "reason": "no Substack cookie saved"}
    try:
        from curl_cffi import requests as cr
        r = cr.get(url, impersonate="chrome", timeout=TIMEOUT,
                   headers={"Cookie": c})
        if r.status_code != 200:
            _LAST_ERROR = f"HTTP {r.status_code}"
            return {"ok": False, "reason": _LAST_ERROR}
        page = r.text
    except Exception as e:  # noqa: BLE001
        _LAST_ERROR = str(e)[:140]
        return {"ok": False, "reason": _LAST_ERROR}

    # Substack wraps the post body in `available-content`; the paywalled tail
    # lives in `paywall`. Take the first and never the second.
    body = ""
    m = re.search(r'<div[^>]+class="[^"]*available-content[^"]*"[^>]*>(.*?)'
                  r'(?=<div[^>]+class="[^"]*(?:paywall|subscribe-widget)[^"]*")',
                  page, re.S)
    if not m:
        m = re.search(r'<div[^>]+class="[^"]*available-content[^"]*"[^>]*>(.*)',
                      page, re.S)
    if m:
        body = _clean(m.group(1))

    if not body:
        _LAST_ERROR = "could not find the post body in the page"
        return {"ok": False, "reason": _LAST_ERROR}

    # ⛔ THE SIGNATURE OF A COOKIE THAT NO LONGER WORKS is a short body plus a
    # subscribe prompt — i.e. exactly what an unauthenticated fetch returns.
    # Reporting that as success would overwrite a teaser with another teaser
    # and mark the article readable.
    if len(body) < 700 or re.search(r"(?i)subscribe to (read|continue)|"
                                    r"this post is for paid subscribers", body):
        _LAST_ERROR = ("got a teaser, not the post — the cookie is probably "
                       "expired or this account does not subscribe")
        return {"ok": False, "reason": _LAST_ERROR, "chars": len(body)}

    _LAST_OK = time.monotonic()
    _LAST_ERROR = None
    return {"ok": True, "body": body, "chars": len(body)}


def unlock(articles: list[dict], limit: int = 20) -> dict:
    """Fetch the full text for held teasers and store it.

    ⭐ The text is SAVED LOCALLY on first fetch and never re-fetched — the
    point is to own the archive, not to depend on the cookie forever.
    """
    from .research import ARTICLES, _bust
    from .store import db, now_iso

    done, failed = 0, []
    for a in articles[:limit]:
        if not a.get("paywalled") or not a.get("url"):
            continue
        res = fetch_post(a["url"])
        if not res.get("ok"):
            failed.append({"title": a.get("title"), "reason": res.get("reason")})
            continue
        # ⛔ Never replace a longer body with a shorter one.
        if res["chars"] <= len(a.get("body") or ""):
            failed.append({"title": a.get("title"),
                           "reason": f"fetched {res['chars']} chars, already hold "
                                     f"{len(a.get('body') or '')}"})
            continue
        db().collection(ARTICLES).document(a["id"]).set({
            "body": res["body"], "chars": res["chars"], "paywalled": False,
            "unlocked_at": now_iso(), "unlocked_by": "substack-cookie",
        }, merge=True)
        done += 1
    _bust("articles")
    return {"unlocked": done, "failed": failed}
