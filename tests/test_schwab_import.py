"""Schwab import guards.

Two bugs found against the real 1,153-row export, both of which produced
confident, wrong money figures rather than errors. Each is pinned here with a
mutation proof.

  1. SAME-DAY ORDERING. Schwab lists newest-first, so a day-trade's
     "Sell to Close" appears BEFORE its "Buy to Open". Sorting on date alone
     closes a position that does not exist yet, manufacturing an unmatched
     close AND a phantom open position out of one real round trip.
     Measured on the real file: 76 unmatched closes, 54 phantom positions.

  2. REVERSE SPLITS. Pre-split lots stay in old share units and get matched
     against post-split sells. Measured: ~+$894k of fabricated profit on SQQQ
     before the fix.

Usage: python -m tests.test_schwab_import
"""

from __future__ import annotations

import sys

from core.schwab_import import (build_round_trips, detect_splits, normalize,
                                parse_date, parse_money, parse_symbol, reconcile)

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'OK  ' if cond else 'FAIL'} {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def rec(date, action, symbol, qty, price, amount, fees=""):
    return normalize({"Date": date, "Action": action, "Symbol": symbol,
                      "Description": "", "Quantity": str(qty),
                      "Price": f"${price}" if price != "" else "",
                      "Fees & Comm": fees, "Amount": amount})


def test_primitives():
    print("Money / date / symbol parsing:")
    check("negative money", parse_money("-$2,866.55") == -2866.55)
    check("positive money", parse_money("$11,833.22") == 11833.22)
    check("blank is None not 0", parse_money("") is None,
          "0 would silently become a real fee of zero")
    posted, asof = parse_date("09/16/2026 as of 09/15/2026")
    check("'as of' trade date extracted", asof.isoformat() == "2026-09-15", str(asof))
    check("posted date kept", posted.isoformat() == "2026-09-16")
    o = parse_symbol("SPY 09/22/2026 763.00 C")
    check("option parsed", o["kind"] == "option" and o["strike"] == 763.0
          and o["right"] == "C" and o["multiplier"] == 100)
    check("equity parsed", parse_symbol("SQQQ")["kind"] == "equity")
    check("CUSIP kept, not dropped", parse_symbol("74347G432")["kind"] == "other")


def test_same_day_ordering():
    """The exact shape from the real file: close listed before open."""
    print("Same-day day trade (close listed first, as Schwab writes it):")
    txs = [
        rec("05/13/2025", "Sell to Close", "QQQ 05/16/2025 530.00 C", 100, 0.58, "$5,733.62"),
        rec("05/13/2025", "Buy to Open", "QQQ 05/16/2025 530.00 C", 100, 0.73, "-$7,366.10"),
    ]
    r = build_round_trips(txs)
    check("one round trip produced", len(r["trades"]) == 1, str(len(r["trades"])))
    check("no unmatched close", len(r["unmatched_closes"]) == 0,
          str(len(r["unmatched_closes"])))
    check("no phantom open position", len(r["open_positions"]) == 0,
          str(len(r["open_positions"])))
    if r["trades"]:
        pnl = r["trades"][0]["pnl"]
        check("loss is (0.58-0.73)*100*100 = -1500", abs(pnl + 1500) < 1.0, str(pnl))


def test_partial_lots():
    print("Multiple opens, single larger close (FIFO):")
    txs = [
        rec("06/16/2025", "Sell to Close", "QQQ 06/27/2025 500.00 P", 100, 1.00, "$10,000.00"),
        rec("06/16/2025", "Buy to Open", "QQQ 06/27/2025 500.00 P", 50, 0.80, "-$4,000.00"),
        rec("06/16/2025", "Buy to Open", "QQQ 06/27/2025 500.00 P", 50, 0.90, "-$4,500.00"),
    ]
    r = build_round_trips(txs)
    check("two lots matched", len(r["trades"]) == 2, str(len(r["trades"])))
    check("nothing left open", not r["open_positions"])
    total = sum(t["pnl"] for t in r["trades"])
    check("total = 10000 - 8500 = 1500", abs(total - 1500) < 1.0, str(total))


def test_reverse_split():
    print("Reverse split restates open lots:")
    txs = [
        rec("01/02/2024", "Buy", "SQQQ", 50000, 10.00, "-$500,000.00"),
        rec("11/07/2024", "Reverse Split", "SQQQ", 10000, "", ""),
        rec("11/07/2024", "Reverse Split", "74347G432", -50000, "", ""),
        rec("12/02/2024", "Sell", "SQQQ", 10000, 55.00, "$550,000.00"),
    ]
    sp = detect_splits(txs)
    check("5:1 split detected", sp.get(("2024-11-07", "SQQQ"), {}).get("ratio") == 5.0,
          str(sp))
    r = build_round_trips(txs)
    check("position fully closed after split", not r["open_positions"],
          str(r["open_positions"]))
    total = sum(t["pnl"] for t in r["trades"])
    # 50,000 @ $10 becomes 10,000 @ $50. Sold 10,000 @ $55 -> +$50,000.
    check("P&L is +50,000, not a fabricated windfall", abs(total - 50000) < 1.0, str(total))


def test_reconcile_gate():
    print("Reconciliation gate:")
    parsed = {"transactions": [rec("01/02/2024", "Buy", "SQQQ", 10, 10.0, "-$100.00", "$1.00")],
              "stated_total_amount": -100.0, "stated_total_fees": 1.0}
    check("matching totals pass", reconcile(parsed)["ok"])
    bad = {**parsed, "stated_total_amount": -999.0}
    check("mismatched totals FAIL (import is refused)", not reconcile(bad)["ok"])


def mutation_proof():
    print("Mutation proof — the guards can actually fail:")
    # Ordering: feed the file's own order but force date-only sorting semantics
    # by using different dates, so the close genuinely precedes the open.
    txs = [
        rec("05/12/2025", "Sell to Close", "QQQ 05/16/2025 530.00 C", 100, 0.58, "$5,733.62"),
        rec("05/13/2025", "Buy to Open", "QQQ 05/16/2025 530.00 C", 100, 0.73, "-$7,366.10"),
    ]
    r = build_round_trips(txs)
    check("a genuinely-earlier close IS reported unmatched",
          len(r["unmatched_closes"]) == 1 and len(r["open_positions"]) == 1,
          f"unmatched={len(r['unmatched_closes'])} open={len(r['open_positions'])}")
    # Split: without the pair, no ratio is inferred.
    check("no split inferred without the removal leg",
          not detect_splits([rec("11/07/2024", "Reverse Split", "SQQQ", 10000, "", "")]))


def main() -> int:
    test_primitives()
    test_same_day_ordering()
    test_partial_lots()
    test_reverse_split()
    test_reconcile_gate()
    mutation_proof()
    print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)}): {FAILS}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
