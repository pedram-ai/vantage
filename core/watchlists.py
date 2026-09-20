"""Watchlists — any number of lists, any number of symbols.

Two of them build themselves from the portfolio ("Positions I hold",
"Traded this month"). Auto lists are computed on read, never stored, so they
cannot drift out of date and cannot be edited into a lie.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from google.cloud import firestore

from .instruments import resolve
from .profile import ET
from .store import _bust, _cached, db, now_iso

COLL = "watchlists"

DEFAULT_LISTS = [
    {"name": "Core", "symbols": ["ES", "SPY", "QQQ"], "order": 0},
]

AUTO_HELD = "__held__"
AUTO_TRADED = "__traded__"


def _norm(sym: str) -> str:
    return (sym or "").strip().upper()


_SEEDED = False


def ensure_seed() -> None:
    """Create the default list the first time, and only the first time.

    ⚠ This is called at the top of BOTH `/` and `/markets`, so before the guard
    it was a Firestore round trip on every navigation to either page — to ask a
    question whose answer, once true, can never become false again. Seeding
    happens once per process; `all_lists()` is cached, so the check itself is
    free after that.
    """
    global _SEEDED
    if _SEEDED:
        return
    if all_lists():
        _SEEDED = True
        return
    for row in DEFAULT_LISTS:
        create(row["name"], row["symbols"])
    _SEEDED = True


def create(name: str, symbols: list[str] | None = None) -> str:
    ref = db().collection(COLL).document()
    n = len(list(db().collection(COLL).stream()))
    ref.set({
        "name": name.strip() or "Untitled",
        "symbols": [_norm(s) for s in (symbols or []) if _norm(s)],
        "order": n,
        "created_at": now_iso(),
    })
    _bust("lists")
    return ref.id


def rename(list_id: str, name: str) -> None:
    db().collection(COLL).document(list_id).set(
        {"name": name.strip() or "Untitled"}, merge=True)
    _bust("lists")


def delete(list_id: str) -> None:
    db().collection(COLL).document(list_id).delete()
    _bust("lists")


def add_symbol(list_id: str, symbol: str) -> None:
    s = _norm(symbol)
    if not s:
        return
    db().collection(COLL).document(list_id).update(
        {"symbols": firestore.ArrayUnion([s])})
    _bust("lists")


def remove_symbol(list_id: str, symbol: str) -> None:
    db().collection(COLL).document(list_id).update(
        {"symbols": firestore.ArrayRemove([_norm(symbol)])})
    _bust("lists")


def get(list_id: str) -> dict | None:
    if list_id in (AUTO_HELD, AUTO_TRADED):
        return _auto_list(list_id)
    # ⚠ Served from the same cached read as all_lists(), so opening a list does
    # not cost a second round trip. Every mutator below calls _bust("lists").
    for d in all_lists():
        if d["id"] == list_id:
            return dict(d)
    return None


def all_lists() -> list[dict]:
    def load():
        out = []
        for doc in db().collection(COLL).stream():
            d = doc.to_dict()
            d["id"] = doc.id
            d["auto"] = False
            out.append(d)
        out.sort(key=lambda r: (r.get("order", 99), r.get("name", "")))
        return out
    return [dict(r) for r in _cached("lists", load)]


def _auto_list(kind: str) -> dict:
    """Derived from the portfolio. Empty until positions/trades exist —
    shown as empty, never padded with placeholders."""
    from . import portfolio
    if kind == AUTO_HELD:
        syms = portfolio.held_symbols()
        return {"id": AUTO_HELD, "name": "Positions I hold",
                "symbols": syms, "auto": True}
    cutoff = (datetime.now(ET) - timedelta(days=30)).date().isoformat()
    return {"id": AUTO_TRADED, "name": "Traded recently",
            "symbols": portfolio.traded_symbols_since(cutoff), "auto": True}


def auto_lists() -> list[dict]:
    return [_auto_list(AUTO_HELD), _auto_list(AUTO_TRADED)]


def symbols_for(list_id: str | None) -> list[str]:
    lst = get(list_id) if list_id else None
    if lst:
        return lst.get("symbols", [])
    lists = all_lists()
    return lists[0].get("symbols", []) if lists else []


def all_tracked_symbols() -> list[str]:
    """Every symbol across every list — what the morning job may need bars for."""
    seen: list[str] = []
    for lst in all_lists() + auto_lists():
        for s in lst.get("symbols", []):
            if s not in seen:
                seen.append(s)
    return seen
