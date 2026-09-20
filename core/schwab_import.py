"""Parse a Schwab "Transactions" JSON export into Vantage's model.

Format (verified against a real 1,153-row corporate export, 2023-10 → 2026-09):

    {"FromDate": "09/20/2022", "ToDate": "09/20/2026",
     "TotalTransactionsAmount": "$94,651.65",
     "TotalFeesAndCommAmount": "$32,996.57",
     "BrokerageTransactions": [
       {"Date","Action","Symbol","Description","Quantity","Price",
        "Fees & Comm","Amount","AcctgRuleCd"}, ...]}

⭐ **The file states its own totals, so the parse is provable.** `reconcile()`
sums our parsed rows and compares against Schwab's stated totals to the cent.
A parser that cannot reproduce them is wrong, and we refuse the import rather
than load numbers that will later be treated as fact.

⛔ **A zero-row file is a suspected truncation, never "no trades".** Schwab
returns an empty export when a range exceeds its row cap, with no error.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime

OPTION_RE = re.compile(r"^([A-Z.]+)\s+(\d{2}/\d{2}/\d{4})\s+([\d.]+)\s+([CP])$")
EQUITY_RE = re.compile(r"^[A-Z.]{1,6}$")

# Actions that open / close a position.
OPEN_ACTIONS = {"Buy to Open", "Buy", "Reinvest Shares", "Sell to Open"}
CLOSE_ACTIONS = {"Sell to Close", "Sell", "Expired", "Buy to Close"}
SHORT_OPEN = {"Sell to Open"}

# Money in / out of the account itself — never profit.
CASH_ACTIONS = {"MoneyLink Transfer", "Wire Received", "Funds Received",
                "Journal", "Wire Sent", "MoneyLink Deposit"}
INCOME_ACTIONS = {"Bank Interest", "Cash Dividend", "Non-Qualified Div",
                  "Reinvest Dividend", "Short Term Cap Gain Reinvest",
                  "Special Dividend", "Qualified Dividend"}
COST_ACTIONS = {"Margin Interest", "Service Fee", "Foreign Tax Paid"}
CORPORATE_ACTIONS = {"Reverse Split", "Stock Split", "Name Change", "Merger"}


class ImportError_(ValueError):
    pass


# --- primitives -------------------------------------------------------------

def parse_money(s) -> float | None:
    """'$1,234.56' / '-$2,866.55' / '' -> float | None."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.lstrip("-(").rstrip(")").replace("$", "").replace(",", "").strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def parse_qty(s) -> float | None:
    if s is None or str(s).strip() == "":
        return None
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


def parse_date(s) -> tuple[date | None, date | None]:
    """'09/16/2026 as of 09/15/2026' -> (posted, trade).

    Schwab posts some rows a day after the trade. The 'as of' date is the one
    that belongs on a trade; ignoring it puts trades in the wrong period, which
    silently corrupts every daily/weekly P&L figure.
    """
    if not s:
        return None, None
    raw = str(s).strip()
    posted_s, asof_s = raw, None
    if " as of " in raw:
        posted_s, asof_s = [x.strip() for x in raw.split(" as of ", 1)]

    def one(x):
        try:
            return datetime.strptime(x, "%m/%d/%Y").date()
        except (ValueError, TypeError):
            return None
    return one(posted_s), one(asof_s)


def parse_symbol(sym: str, description: str = "") -> dict:
    """Classify a Schwab symbol. 'SPY 09/22/2026 763.00 C' -> option."""
    s = (sym or "").strip()
    if not s:
        return {"kind": "none", "underlying": None, "symbol": s}
    m = OPTION_RE.match(s)
    if m:
        und, exp, strike, right = m.groups()
        try:
            expiry = datetime.strptime(exp, "%m/%d/%Y").date()
        except ValueError:
            expiry = None
        return {"kind": "option", "underlying": und, "symbol": s,
                "expiry": expiry.isoformat() if expiry else None,
                "strike": float(strike), "right": right,
                "multiplier": 100.0}
    if EQUITY_RE.match(s):
        return {"kind": "equity", "underlying": s, "symbol": s, "multiplier": 1.0}
    # CUSIPs and anything else — carried, never silently dropped.
    return {"kind": "other", "underlying": s, "symbol": s, "multiplier": 1.0}


# --- normalisation ----------------------------------------------------------

def normalize(rec: dict, source: str = "") -> dict:
    posted, asof = parse_date(rec.get("Date"))
    eff = asof or posted
    sym = parse_symbol(rec.get("Symbol", ""), rec.get("Description", ""))
    action = (rec.get("Action") or "").strip()
    return {
        "action": action,
        "posted_on": posted.isoformat() if posted else None,
        "trade_on": eff.isoformat() if eff else None,
        "symbol": sym["symbol"],
        "underlying": sym["underlying"],
        "instrument": sym["kind"],
        "expiry": sym.get("expiry"),
        "strike": sym.get("strike"),
        "right": sym.get("right"),
        "multiplier": sym.get("multiplier", 1.0),
        "description": (rec.get("Description") or "").strip(),
        "qty": parse_qty(rec.get("Quantity")),
        "price": parse_money(rec.get("Price")),
        "fees": parse_money(rec.get("Fees & Comm")) or 0.0,
        "amount": parse_money(rec.get("Amount")),
        "source_file": source,
        "txn_id": None,   # filled below
    }


def make_txn_id(t: dict, seq: int) -> str:
    """Stable id so re-importing the same file is a no-op and the 60-day API
    overlap dedupes cleanly. Schwab gives no id, so it is content-derived."""
    parts = [t.get("posted_on") or "", t.get("action") or "", t.get("symbol") or "",
             f"{t.get('qty')}", f"{t.get('amount')}", str(seq)]
    return "|".join(parts)


def load_export(path: str) -> dict:
    with open(path) as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict) or "BrokerageTransactions" not in raw:
        raise ImportError_("not a Schwab transactions export "
                           "(no BrokerageTransactions key)")
    rows = raw["BrokerageTransactions"]
    if not rows:
        raise ImportError_(
            "export contains ZERO transactions. Schwab returns an empty file "
            "when a range exceeds its row cap — treat this as a suspected "
            "truncation and re-export a narrower range, NOT as 'no trades'.")

    src = path.rsplit("/", 1)[-1]
    txs = []
    for i, r in enumerate(rows):
        t = normalize(r, src)
        t["txn_id"] = make_txn_id(t, i)
        txs.append(t)
    return {
        "transactions": txs,
        "from_date": raw.get("FromDate"),
        "to_date": raw.get("ToDate"),
        "stated_total_amount": parse_money(raw.get("TotalTransactionsAmount")),
        "stated_total_fees": parse_money(raw.get("TotalFeesAndCommAmount")),
        "source_file": src,
    }


def reconcile(parsed: dict) -> dict:
    """Prove the parse against Schwab's own stated totals."""
    txs = parsed["transactions"]
    got_amt = round(sum(t["amount"] or 0.0 for t in txs), 2)
    got_fees = round(sum(t["fees"] or 0.0 for t in txs), 2)
    exp_amt = parsed.get("stated_total_amount")
    exp_fees = parsed.get("stated_total_fees")
    amt_ok = exp_amt is None or abs(got_amt - exp_amt) < 0.01
    fee_ok = exp_fees is None or abs(got_fees - exp_fees) < 0.01
    return {
        "ok": amt_ok and fee_ok,
        "amount": {"stated": exp_amt, "parsed": got_amt,
                   "diff": None if exp_amt is None else round(got_amt - exp_amt, 2),
                   "ok": amt_ok},
        "fees": {"stated": exp_fees, "parsed": got_fees,
                 "diff": None if exp_fees is None else round(got_fees - exp_fees, 2),
                 "ok": fee_ok},
        "n": len(txs),
    }


# --- round trips ------------------------------------------------------------

@dataclass
class Lot:
    qty: float
    price: float
    fees: float
    opened_on: str
    multiplier: float



def detect_splits(txs: list[dict]) -> dict:
    """Find reverse/forward splits and their ratio.

    Schwab books a split as a PAIR on one date: the old shares leave (negative
    quantity, often under the CUSIP) and the new shares arrive (positive, under
    the ticker). SQQQ did exactly this on 2024-11-07: -59,800 old, +11,960 new
    = a 5:1 reverse split.

    ⛔ Ignoring this is not a rounding error. Pre-split lots stay in OLD share
    units and get FIFO-matched against post-split sells, which fabricates
    enormous phantom profit — measured at roughly +$894k of nonsense on this
    very file before the fix.
    """
    by_date: dict[str, list[dict]] = defaultdict(list)
    for t in txs:
        if t["action"] in CORPORATE_ACTIONS:
            by_date[t.get("trade_on") or ""].append(t)

    out: dict[tuple, dict] = {}
    for d, rows in by_date.items():
        adds = [r for r in rows if (r.get("qty") or 0) > 0]
        rems = [r for r in rows if (r.get("qty") or 0) < 0]
        if not adds or not rems:
            continue
        old_q = sum(abs(r["qty"]) for r in rems)
        new_q = sum(r["qty"] for r in adds)
        if new_q <= 0 or old_q <= 0:
            continue
        ratio = old_q / new_q          # >1 reverse split, <1 forward split
        for r in adds:
            out[(d, r.get("underlying"))] = {
                "ratio": ratio, "old_qty": old_q, "new_qty": new_q, "date": d}
    return out


def _apply_split(by_key: dict, underlying: str, ratio: float) -> None:
    """Restate open lots into post-split units: fewer shares, higher basis."""
    for key, lots in by_key.items():
        if not lots:
            continue
        # equities only: the key IS the ticker
        if key != underlying:
            continue
        for lot in lots:
            lot.qty = lot.qty / ratio
            lot.price = lot.price * ratio

def build_round_trips(txs: list[dict]) -> dict:
    """FIFO-match opens against closes, per exact instrument.

    Options match on the full contract (symbol carries expiry+strike+right);
    equities on the ticker.

    ⚠ Corporate actions (reverse splits) change the share count without a
    trade. SQQQ/TQQQ do this regularly. Affected symbols are FLAGGED rather
    than silently mismatched — a split turns a FIFO share match into nonsense.
    """
    by_key: dict[str, deque[Lot]] = defaultdict(deque)
    trades: list[dict] = []
    unmatched_closes: list[dict] = []
    split_symbols: set[str] = set()
    splits = detect_splits(txs)

    # ⚠ ORDER MATTERS AND THE FILE'S ORDER IS WRONG FOR US.
    # Schwab lists newest-first, so a same-day day-trade has its "Sell to
    # Close" BEFORE its "Buy to Open". Sorting on date alone then tries to
    # close a position that does not exist yet, which manufactures BOTH an
    # unmatched close and a phantom open position from one real round trip.
    # Within a date, opens must always be processed before closes.
    def _order(r):
        return (r.get("trade_on") or "",
                0 if r.get("action") in OPEN_ACTIONS else 1)

    for t in sorted(txs, key=_order):
        action = t["action"]
        if action in CORPORATE_ACTIONS:
            if t.get("underlying"):
                split_symbols.add(t["underlying"])
            ev = splits.get((t.get("trade_on"), t.get("underlying")))
            if ev and ev["ratio"] and ev["ratio"] != 1:
                _apply_split(by_key, t["underlying"], ev["ratio"])
            continue
        if action not in OPEN_ACTIONS and action not in CLOSE_ACTIONS:
            continue
        key = t["symbol"]
        if not key:
            continue
        qty = abs(t["qty"] or 0.0)
        if qty <= 0:
            continue
        mult = t.get("multiplier") or 1.0

        if action in OPEN_ACTIONS:
            price = t["price"]
            if price is None and t["amount"] is not None:
                price = abs(t["amount"]) / (qty * mult)
            by_key[key].append(Lot(qty, price or 0.0, t["fees"] or 0.0,
                                   t["trade_on"], mult))
            continue

        # close
        price = t["price"]
        if action == "Expired":
            price = 0.0
        if price is None and t["amount"] is not None:
            price = abs(t["amount"]) / (qty * mult)
        price = price or 0.0
        remaining = qty
        close_fees = t["fees"] or 0.0
        lots = by_key[key]
        while remaining > 1e-9 and lots:
            lot = lots[0]
            take = min(remaining, lot.qty)
            share = take / qty if qty else 0
            open_fee_share = lot.fees * (take / lot.qty) if lot.qty else 0.0
            proceeds = take * price * mult
            cost = take * lot.price * mult
            fees = open_fee_share + close_fees * share
            trades.append({
                "symbol": t["underlying"] or key,
                "contract": key,
                "instrument": t["instrument"],
                "description": t["description"],
                "qty": take,
                "opened_on": lot.opened_on,
                "closed_on": t["trade_on"],
                "open_price": round(lot.price, 4),
                "close_price": round(price, 4),
                "fees": round(fees, 2),
                "pnl": round(proceeds - cost - fees, 2),
                "expired": action == "Expired",
                "split_affected": (t["underlying"] in split_symbols),
                "zone": None,   # filled later by the map scorecard
            })
            lot.qty -= take
            remaining -= take
            if lot.qty <= 1e-9:
                lots.popleft()
        if remaining > 1e-9:
            unmatched_closes.append({**t, "unmatched_qty": remaining})

    open_positions = []
    expired_worthless = []
    for key, lots in by_key.items():
        tot = sum(l.qty for l in lots)
        if tot <= 1e-9:
            continue
        cost = sum(l.qty * l.price for l in lots) / tot
        first = lots[0]
        sample = next((x for x in txs if x["symbol"] == key), {})
        open_positions.append({
            "symbol": sample.get("underlying") or key,
            "contract": key,
            "instrument": sample.get("instrument"),
            "description": sample.get("description"),
            "qty": round(tot, 4),
            "cost": round(cost, 4),
            "multiplier": first.multiplier,
            "opened_at": first.opened_on,
            "expiry": sample.get("expiry"),
            "strike": sample.get("strike"),
            "right": sample.get("right"),
        })

    return {
        "trades": trades,
        "open_positions": open_positions,
        "unmatched_closes": unmatched_closes,
        "split_symbols": sorted(split_symbols),
    }


def cash_and_income(txs: list[dict]) -> dict:
    """Deposits/withdrawals are NEVER profit; income and costs are separate."""
    cash, income, costs = [], [], []
    for t in txs:
        a = t["action"]
        row = {"date": t["trade_on"], "action": a, "amount": t["amount"],
               "description": t["description"], "symbol": t["symbol"]}
        if a in CASH_ACTIONS:
            cash.append(row)
        elif a in INCOME_ACTIONS:
            income.append(row)
        elif a in COST_ACTIONS:
            costs.append(row)
    return {
        "cash_flows": cash,
        "income": income,
        "costs": costs,
        "net_deposits": round(sum(c["amount"] or 0 for c in cash), 2),
        "total_income": round(sum(c["amount"] or 0 for c in income), 2),
        "total_costs": round(sum(c["amount"] or 0 for c in costs), 2),
    }


def summarize(path: str) -> dict:
    parsed = load_export(path)
    rec = reconcile(parsed)
    rt = build_round_trips(parsed["transactions"])
    cash = cash_and_income(parsed["transactions"])
    dates = [t["trade_on"] for t in parsed["transactions"] if t["trade_on"]]
    return {
        "source_file": parsed["source_file"],
        "requested_from": parsed["from_date"], "requested_to": parsed["to_date"],
        "actual_from": min(dates) if dates else None,
        "actual_to": max(dates) if dates else None,
        "n_transactions": len(parsed["transactions"]),
        "reconcile": rec,
        "n_trades": len(rt["trades"]),
        "n_open": len(rt["open_positions"]),
        "unmatched": len(rt["unmatched_closes"]),
        "split_symbols": rt["split_symbols"],
        "realized_pnl": round(sum(t["pnl"] for t in rt["trades"]), 2),
        **cash,
        "parsed": parsed, "round_trips": rt,
    }
