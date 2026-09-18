"""Firestore persistence. Collections:

  author_levels/{id}  {author, source_url, published_at, session_date, kind,
                       price, label, direction, note, created_at}
  runs/{id}           archived daily runs (inputs, zones json, verdict)
  events/{id}         hand-maintained calendar events {date, kind, text}
  settings/main       {contract_override, x_accounts, recipients}
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from google.cloud import firestore

_client: firestore.Client | None = None


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
    return ref.id


def levels_for_session(session_date: str) -> list[dict]:
    q = db().collection("author_levels").where("session_date", "==", session_date)
    out = []
    for doc in q.stream():
        d = doc.to_dict()
        d["id"] = doc.id
        out.append(d)
    return sorted(out, key=lambda r: -float(r.get("price", 0)))


def recent_levels(limit: int = 200) -> list[dict]:
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
    q = db().collection("events").where("date", "==", date_iso)
    return [doc.to_dict() for doc in q.stream()]


def add_event(row: dict) -> str:
    ref = db().collection("events").document()
    ref.set(row)
    return ref.id


def get_settings() -> dict:
    doc = db().collection("settings").document("main").get()
    return doc.to_dict() if doc.exists else {}


def save_settings(s: dict) -> None:
    db().collection("settings").document("main").set(s, merge=True)
