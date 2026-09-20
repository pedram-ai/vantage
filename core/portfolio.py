"""Positions, trades and cash — the data model.

⛔ There is NO data here yet and none is invented. Schwab is not linked and no
CSV has been imported, so every accessor returns empty and the UI renders an
explicit "not connected" state. A placeholder number on a P&L screen is worse
than a blank one: it looks like a fact.

Collections:
  positions/{id}  open holdings   {symbol, kind, qty, cost, opened_at, ...}
  trades/{id}     closed round trips {symbol, opened_at, closed_at, pnl, zone, ...}
  cash/{id}       deposits/withdrawals {date, type, amount}
  import_runs/{id} coverage of each CSV import, so gaps are visible
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from .profile import ET, PT
from .store import db, now_iso


# --- the read cache ---------------------------------------------------------
# ⚠ WHY THIS EXISTS: every period click (today / this week / last quarter / …)
# used to re-stream the WHOLE `trades` and `cash` collections out of Firestore
# and recompute, so switching periods cost a full round trip per click on a
# collection that only changes when an import runs.
#
# This is NOT BigQuery and does not want a second database. The corporate
# account is 771 trades and 19 cash rows — it fits in memory with room to
# spare, so the collections are read once and filtered in process.
#
# ⛔ CORRECTNESS OVER SPEED: the cache is INVALIDATED BY THE IMPORTER, not
# merely aged out. A stale P&L figure is the exact failure this codebase
# refuses to ship, so `invalidate()` is called at the end of every import and
# the TTL is only a backstop for a writer we forgot about.
_TTL = 600.0
_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, list]] = {}


def invalidate() -> None:
    """Drop every cached collection. Called after any write."""
    with _LOCK:
        _CACHE.clear()


def _all(name: str) -> list[dict]:
    """Whole collection, cached. Returns a COPY so callers cannot mutate it."""
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(name)
        if hit and now - hit[0] < _TTL:
            return [dict(r) for r in hit[1]]
    rows = []
    for doc in db().collection(name).stream():
        d = doc.to_dict()
        d["id"] = doc.id
        rows.append(d)
    with _LOCK:
        _CACHE[name] = (now, rows)
    return [dict(r) for r in rows]


def cache_state() -> dict:
    """For System Health — what is warm, and how old."""
    now = time.monotonic()
    with _LOCK:
        return {k: {"rows": len(v[1]), "age_sec": round(now - v[0], 1)}
                for k, v in _CACHE.items()}


# --- reads ------------------------------------------------------------------

def open_positions() -> list[dict]:
    return sorted(_all("positions"), key=lambda r: r.get("opened_at", ""), reverse=True)


def held_symbols() -> list[str]:
    seen: list[str] = []
    for p in open_positions():
        s = (p.get("symbol") or "").upper()
        if s and s not in seen:
            seen.append(s)
    return seen


def traded_symbols_since(date_iso: str) -> list[str]:
    seen: list[str] = []
    for t in _all("trades"):
        if (t.get("closed_on") or "") < date_iso:
            continue
        s = (t.get("symbol") or "").upper()
        if s and s not in seen:
            seen.append(s)
    return seen


def trades_between(start_iso: str, end_iso: str) -> list[dict]:
    # ⚠ The bounds are INCLUSIVE at both ends, matching the Firestore
    # >= / <= query this replaced. `closed_on` is a YYYY-MM-DD string, so
    # string comparison is date comparison — but only while that holds: a
    # datetime or a differently-formatted date would compare wrong and
    # silently return the wrong period rather than failing.
    out = [t for t in _all("trades")
           if start_iso <= (t.get("closed_on") or "") <= end_iso]
    return sorted(out, key=lambda r: r.get("closed_on", ""), reverse=True)


def cash_flows() -> list[dict]:
    return sorted(_all("cash"), key=lambda r: r.get("date", ""), reverse=True)


def import_coverage() -> list[dict]:
    return sorted(_all("import_runs"), key=lambda r: r.get("start", ""))


def latest_import() -> dict | None:
    runs = import_coverage()
    return runs[-1] if runs else None


def account_state() -> dict:
    """Cash-truth account figures from the imports.

    ⭐ Realized P&L here is derived from CASH, not from lot matching: with no
    open positions the two must agree, and cash is the one Schwab states
    itself. The lot-matched figure is kept for per-trade attribution and the
    gap between them is reported, never hidden.
    """
    runs = import_coverage()
    if not runs:
        return {}
    dep = sum(float(r.get("net_deposits", 0) or 0) for r in runs)
    inc = sum(float(r.get("income", 0) or 0) for r in runs)
    cost = sum(float(r.get("costs", 0) or 0) for r in runs)
    cash = sum(float(r.get("net_cash", 0) or 0) for r in runs)
    fees = sum(float(r.get("total_fees", 0) or 0) for r in runs)
    pnl_cash = sum(float(r.get("realized_pnl_cash", 0) or 0) for r in runs)
    pnl_lots = sum(float(r.get("realized_pnl_lots", 0) or 0) for r in runs)
    return {
        "net_deposits": dep, "income": inc, "costs": cost,
        "net_cash": cash, "total_fees": fees,
        "realized_pnl": pnl_cash,
        "realized_pnl_lots": pnl_lots,
        "residual": round(pnl_lots - pnl_cash, 2),
        "return_pct": (pnl_cash / dep * 100) if dep else None,
        "start": min((r.get("start") or "9999") for r in runs),
        "end": max((r.get("end") or "") for r in runs),
        "accounts": sorted({r.get("account") for r in runs if r.get("account")}),
    }


def is_connected() -> dict:
    """What the portfolio pages can honestly show today."""
    has_trades = bool(_all("trades"))
    has_pos = bool(_all("positions"))
    imports = import_coverage()
    return {
        "has_trades": has_trades,
        "has_positions": has_pos,
        "imports": len(imports),
        "coverage": imports,
        "ready": has_trades or has_pos,
    }


# --- periods ----------------------------------------------------------------

PERIODS = [
    ("today", "Today", "current"),
    ("week", "Week", "current"),
    ("month", "Month", "current"),
    ("quarter", "Quarter", "current"),
    ("year", "Year", "current"),
    ("12m", "12 months", "current"),
    ("all", "All time", "current"),
    ("yesterday", "Yesterday", "prior"),
    ("last_week", "Last week", "prior"),
    ("last_month", "Last month", "prior"),
    ("last_quarter", "Last quarter", "prior"),
    ("last_year", "Last year", "prior"),
]


def period_range(key: str, today=None) -> tuple[str, str, str]:
    """(start_iso, end_iso, label). Dates are Pacific — Pedram's clock."""
    d = today or datetime.now(PT).date()
    q_start_month = 3 * ((d.month - 1) // 3) + 1

    def iso(x):
        return x.isoformat()

    if key == "today":
        return iso(d), iso(d), d.strftime("%b %-d")
    if key == "yesterday":
        y = d - timedelta(days=1)
        return iso(y), iso(y), y.strftime("%b %-d")
    if key == "week":
        s = d - timedelta(days=d.weekday())
        return iso(s), iso(d), f"{s.strftime('%b %-d')} – {d.strftime('%b %-d')}"
    if key == "last_week":
        e = d - timedelta(days=d.weekday() + 1)
        s = e - timedelta(days=6)
        return iso(s), iso(e), f"{s.strftime('%b %-d')} – {e.strftime('%b %-d')}"
    if key == "month":
        s = d.replace(day=1)
        return iso(s), iso(d), d.strftime("%B %Y")
    if key == "last_month":
        e = d.replace(day=1) - timedelta(days=1)
        return iso(e.replace(day=1)), iso(e), e.strftime("%B %Y")
    if key == "quarter":
        s = d.replace(month=q_start_month, day=1)
        return iso(s), iso(d), f"Q{(d.month-1)//3+1} {d.year}"
    if key == "last_quarter":
        s_cur = d.replace(month=q_start_month, day=1)
        e = s_cur - timedelta(days=1)
        s = e.replace(month=3 * ((e.month - 1) // 3) + 1, day=1)
        return iso(s), iso(e), f"Q{(e.month-1)//3+1} {e.year}"
    if key == "year":
        return iso(d.replace(month=1, day=1)), iso(d), str(d.year)
    if key == "last_year":
        return (iso(d.replace(year=d.year - 1, month=1, day=1)),
                iso(d.replace(year=d.year - 1, month=12, day=31)), str(d.year - 1))
    if key == "12m":
        s = d - timedelta(days=365)
        return iso(s), iso(d), "Last 12 months"
    return "1900-01-01", iso(d), "All time"


# --- P&L --------------------------------------------------------------------

def performance(period: str = "month", page: int = 1, per_page: int = 25) -> dict:
    """Realized P&L for a period, plus open P&L.

    ⛔ Deposits and withdrawals are NEVER counted as profit. A $25k transfer
    that lifts the account $25k is not a $25k gain; period figures are built
    from trades, not from the change in account value.
    """
    start, end, label = period_range(period)
    trades = trades_between(start, end)
    realized = sum(float(t.get("pnl", 0) or 0) for t in trades)
    fees = sum(float(t.get("fees", 0) or 0) for t in trades)
    wins = [t for t in trades if float(t.get("pnl", 0) or 0) > 0]
    losses = [t for t in trades if float(t.get("pnl", 0) or 0) < 0]
    positions = open_positions()
    open_pnl = sum(float(p.get("open_pnl", 0) or 0) for p in positions)

    by_symbol: dict[str, dict] = {}
    by_zone: dict[str, dict] = {}
    for t in trades:
        pnl = float(t.get("pnl", 0) or 0)
        for bucket, key in ((by_symbol, (t.get("symbol") or "?").upper()),
                            (by_zone, t.get("zone") or "Outside the map")):
            row = bucket.setdefault(key, {"key": key, "n": 0, "wins": 0, "pnl": 0.0})
            row["n"] += 1
            row["pnl"] += pnl
            if pnl > 0:
                row["wins"] += 1

    def finish(bucket):
        rows = list(bucket.values())
        for r in rows:
            r["win_rate"] = round(100 * r["wins"] / r["n"]) if r["n"] else None
        return sorted(rows, key=lambda r: -r["pnl"])

    state = account_state()
    # For "all time" prefer Schwab's own figures over our lot-derived ones.
    # Otherwise the page shows TWO fee totals ($32,997 stated vs $33,144
    # allocated) side by side, which reads as data to reconcile rather than
    # one fact — and the stated one is authoritative.
    if period == "all" and state.get("realized_pnl") is not None:
        realized = state["realized_pnl"]
        fees = state.get("total_fees", fees)

    return {
        "period": period, "label": label, "start": start, "end": end,
        "account": state,
        "n_trades": len(trades),
        "realized": realized, "fees": fees, "open_pnl": open_pnl,
        "total": realized + open_pnl,
        "win_rate": round(100 * len(wins) / len(trades)) if trades else None,
        "avg_win": (sum(float(t["pnl"]) for t in wins) / len(wins)) if wins else None,
        "avg_loss": (sum(float(t["pnl"]) for t in losses) / len(losses)) if losses else None,
        "by_symbol": finish(by_symbol),
        "by_zone": finish(by_zone),
        "trades": trades[(max(page, 1) - 1) * per_page: max(page, 1) * per_page],
        "all_trades": trades,
        "page": max(page, 1),
        "per_page": per_page,
        "pages": max(1, (len(trades) + per_page - 1) // per_page),
        "total_trades": len(trades),
        "empty": not trades and not positions,
    }
