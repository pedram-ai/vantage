"""Import a Schwab transactions export into Firestore.

    python -m jobs.import_schwab <file.json> [--account NAME] [--dry-run]

⛔ REFUSES TO IMPORT unless the parse reconciles against Schwab's own stated
totals to the cent. A file we cannot reproduce is a file we do not understand,
and these numbers end up on a P&L screen where they read as fact.

Idempotent: rows are keyed by a content-derived txn_id, so re-running the same
file overwrites rather than duplicates.
"""

from __future__ import annotations

import sys

from core import store
from core.schwab_import import summarize

BATCH = 400


def _commit(col: str, rows: list[dict], key: str) -> int:
    db = store.db()
    n = 0
    for i in range(0, len(rows), BATCH):
        batch = db.batch()
        for r in rows[i:i + BATCH]:
            doc_id = str(r[key]).replace("/", "_")[:400]
            batch.set(db.collection(col).document(doc_id), r)
        batch.commit()
        n += len(rows[i:i + BATCH])
    return n


def run(path: str, account: str = "corporate", dry: bool = False) -> dict:
    s = summarize(path)
    rec = s["reconcile"]

    if not rec["ok"]:
        raise SystemExit(
            f"REFUSING IMPORT — parse does not reconcile.\n"
            f"  amount stated {rec['amount']['stated']} parsed {rec['amount']['parsed']}\n"
            f"  fees   stated {rec['fees']['stated']} parsed {rec['fees']['parsed']}\n"
            "Fix the parser before loading numbers that will be read as fact.")

    txs = s["parsed"]["transactions"]
    rt = s["round_trips"]

    # Cash truth: with no open positions, realized P&L is recoverable from
    # cash alone, independent of lot matching. Keep both and state the gap.
    cash_pnl = round(rec["amount"]["parsed"] - s["net_deposits"]
                     - s["total_income"] - s["total_costs"], 2)
    residual = round(s["realized_pnl"] - cash_pnl, 2)

    trades = []
    for i, t in enumerate(rt["trades"]):
        trades.append({**t, "account": account,
                       "trade_id": f"{account}|{t['closed_on']}|{t['contract']}|{i}"})
    cash = []
    for i, c in enumerate(s["cash_flows"]):
        cash.append({**c, "account": account, "cash_id": f"{account}|{c['date']}|{i}"})

    summary = {
        "account": account,
        "source_file": s["source_file"],
        "start": s["actual_from"], "end": s["actual_to"],
        "requested_from": s["requested_from"], "requested_to": s["requested_to"],
        "rows": s["n_transactions"],
        "trades": len(trades),
        "open_positions": len(rt["open_positions"]),
        "unmatched": s["unmatched"],
        "split_symbols": s["split_symbols"],
        "realized_pnl_lots": s["realized_pnl"],
        "realized_pnl_cash": cash_pnl,
        "fee_allocation_residual": residual,
        "net_deposits": s["net_deposits"],
        "income": s["total_income"],
        "costs": s["total_costs"],
        "net_cash": rec["amount"]["parsed"],
        "total_fees": rec["fees"]["parsed"],
        "reconciled": True,
        "imported_at": store.now_iso(),
    }

    if dry:
        return {"dry_run": True, **summary}

    for t in txs:
        t["account"] = account
    n_tx = _commit("transactions", txs, "txn_id")
    n_tr = _commit("trades", trades, "trade_id")
    n_ca = _commit("cash", cash, "cash_id")
    pos = [{**p, "account": account,
            "position_id": f"{account}|{p['contract']}"} for p in rt["open_positions"]]
    n_po = _commit("positions", pos, "position_id") if pos else 0

    store.db().collection("import_runs").document(
        f"{account}|{s['source_file']}").set(summary)

    # ⛔ The read cache in core.portfolio must be dropped the instant the data
    # moves. A cached P&L that survives an import is a wrong number on a money
    # screen, which is worse than a slow one.
    from core import portfolio
    portfolio.invalidate()

    return {"transactions": n_tx, "trades": n_tr, "cash": n_ca,
            "positions": n_po, **summary}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    account = "corporate"
    if "--account" in sys.argv:
        account = sys.argv[sys.argv.index("--account") + 1]
    res = run(args[0], account, dry="--dry-run" in sys.argv)
    for k, v in res.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
