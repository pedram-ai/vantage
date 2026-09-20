"""Firestore persistence. Collections:

  author_levels/{id}  {author, source_url, published_at, session_date, kind,
                       price, label, direction, note, created_at}
  runs/{id}           archived daily runs (inputs, zones json, verdict)
  events/{id}         hand-maintained calendar events {date, kind, text}
  settings/main       {contract_override, x_accounts, recipients}
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

from google.cloud import firestore

_client: firestore.Client | None = None

# --- the config-read cache --------------------------------------------------
# ⚠ WHY: `levels_for_session()` and `events_for()` are called ONCE PER SYMBOL
# inside build_symbol, so a four-symbol Today page made eight Firestore round
# trips at ~150 ms each — measured at 1,073 ms per page load, on every single
# navigation, long after the bar cache had gone warm.
#
# These are small, operator-edited collections: levels, events, settings. They
# change when somebody types, not when the market moves.
#
# ⛔ INVALIDATED BY THE WRITER, not merely aged out. `_bust()` runs inside every
# add/delete/save below, so an edit is visible on the next render. The TTL is a
# backstop for a writer nobody remembered to wire up.
_CFG_TTL = 300.0
_CFG_LOCK = threading.Lock()
_CFG: dict[str, tuple[float, object]] = {}


def _cached(key: str, loader):
    now = time.monotonic()
    with _CFG_LOCK:
        hit = _CFG.get(key)
        if hit and now - hit[0] < _CFG_TTL:
            return hit[1]
    value = loader()
    with _CFG_LOCK:
        _CFG[key] = (now, value)
    return value


def _bust(*prefixes: str) -> None:
    """Drop cached reads whose key starts with any prefix. No prefix = all."""
    with _CFG_LOCK:
        if not prefixes:
            _CFG.clear()
            return
        for k in [k for k in _CFG if k.startswith(prefixes)]:
            _CFG.pop(k, None)


def cache_state() -> dict:
    now = time.monotonic()
    with _CFG_LOCK:
        return {k: round(now - v[0], 1) for k, v in _CFG.items()}


def db() -> firestore.Client:
    global _client
    if _client is None:
        _client = firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT", "patexia-vantage"))
    return _client


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- author levels -----------------------------------------------------------

def add_level(row: dict) -> str:
    row = dict(row)
    row["created_at"] = now_iso()
    ref = db().collection("author_levels").document()
    ref.set(row)
    _bust("levels:")
    return ref.id


def levels_for_session(session_date: str) -> list[dict]:
    def load():
        q = db().collection("author_levels").where("session_date", "==", session_date)
        out = []
        for doc in q.stream():
            d = doc.to_dict()
            d["id"] = doc.id
            out.append(d)
        return sorted(out, key=lambda r: -float(r.get("price", 0)))
    # ⚠ Returns a COPY: build_symbol runs this once per symbol and a caller
    # that mutated the list would corrupt every later render.
    return [dict(r) for r in _cached(f"levels:{session_date}", load)]


def recent_levels(limit: int = 200) -> list[dict]:
    return [dict(r) for r in _cached(f"levels:recent:{limit}",
                                     lambda: _recent_levels_uncached(limit))]


def _recent_levels_uncached(limit: int) -> list[dict]:
    q = (db().collection("author_levels")
         .order_by("created_at", direction=firestore.Query.DESCENDING)
         .limit(limit))
    out = []
    for doc in q.stream():
        d = doc.to_dict()
        d["id"] = doc.id
        out.append(d)
    return out


def delete_level(level_id: str) -> None:
    db().collection("author_levels").document(level_id).delete()
    _bust("levels:")


# --- runs --------------------------------------------------------------------

def save_run(run: dict) -> str:
    run = dict(run)
    run["created_at"] = now_iso()
    ref = db().collection("runs").document()
    ref.set(run)
    return ref.id


def list_runs(limit: int = 60) -> list[dict]:
    q = (db().collection("runs")
         .order_by("created_at", direction=firestore.Query.DESCENDING)
         .limit(limit))
    out = []
    for doc in q.stream():
        d = doc.to_dict()
        d["id"] = doc.id
        # keep listing light
        d.pop("es_sessions", None)
        d.pop("spy_sessions", None)
        out.append(d)
    return out


def get_run(run_id: str) -> dict | None:
    doc = db().collection("runs").document(run_id).get()
    if not doc.exists:
        return None
    d = doc.to_dict()
    d["id"] = doc.id
    return d


# --- events / settings -------------------------------------------------------

def events_for(date_iso: str) -> list[dict]:
    def load():
        q = db().collection("events").where("date", "==", date_iso)
        return [doc.to_dict() for doc in q.stream()]
    return [dict(r) for r in _cached(f"events:{date_iso}", load)]


def add_event(row: dict) -> str:
    ref = db().collection("events").document()
    ref.set(row)
    _bust("events:")
    return ref.id


def get_settings() -> dict:
    def load():
        doc = db().collection("settings").document("main").get()
        return doc.to_dict() if doc.exists else {}
    return dict(_cached("settings", load))


def save_settings(s: dict) -> None:
    db().collection("settings").document("main").set(s, merge=True)
    _bust("settings")
