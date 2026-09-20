"""The read cache and the charts must not change any NUMBER.

Two failure modes are guarded here, and both are of the same family: they make
a money screen *look* right while being wrong.

1. THE CACHE. `trades_between` used to be a Firestore range query and is now an
   in-memory filter. If the two disagree by even one row, every figure on
   Performance shifts. So the test runs the ORIGINAL Firestore query and
   asserts the cached path returns the identical id set — not a similar count,
   the same ids.

2. THE CATEGORY CHART. It used to draw `rows[:8]` off a list sorted by signed
   P&L, which kept the winners and dropped the losers: on the real book that
   hid -$575,488 while the page beside it said -$519,390. The test asserts the
   drawn bars reconcile to the total, so a truncation can never again be
   silent.

Mutation proofs are at the bottom — a guard that cannot fail is decoration.

Run: python -m tests.test_portfolio_cache
"""

from __future__ import annotations

import re
import sys

from core import perf_charts, portfolio

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)
    return cond


# --- 1. the cache returns exactly what Firestore returned -------------------

def _firestore_trades(start: str, end: str) -> set[str]:
    """The ORIGINAL query, kept here as the oracle."""
    from core.store import db
    return {doc.id for doc in (db().collection("trades")
                               .where("closed_on", ">=", start)
                               .where("closed_on", "<=", end).stream())}


def test_cache_matches_firestore() -> None:
    print("\n1. cached filter == Firestore range query")
    windows = [
        ("2023-01-01", "2030-12-31"),   # everything
        ("2024-01-01", "2024-12-31"),   # a full year
        ("2026-09-01", "2026-09-30"),   # a month
        ("2026-09-18", "2026-09-18"),   # a single day, both bounds inclusive
        ("2019-01-01", "2019-12-31"),   # empty window
    ]
    for start, end in windows:
        want = _firestore_trades(start, end)
        got = {t["id"] for t in portfolio.trades_between(start, end)}
        check(f"{start} → {end}", want == got,
              f"{len(want)} rows"
              + ("" if want == got else f" · missing {len(want - got)}, extra {len(got - want)}"))


def test_invalidate_rereads() -> None:
    print("\n2. invalidate() actually drops the cache")
    portfolio.invalidate()
    check("empty after invalidate", portfolio.cache_state() == {})
    portfolio.trades_between("2023-01-01", "2030-12-31")
    st = portfolio.cache_state()
    check("warm after a read", "trades" in st, f"{st.get('trades', {}).get('rows')} rows cached")


def test_cache_is_not_mutable_by_callers() -> None:
    print("\n3. a caller cannot corrupt the cache")
    a = portfolio.trades_between("2023-01-01", "2030-12-31")
    if not a:
        return check("no trades to test with", True, "skipped")
    a[0]["pnl"] = 999_999_999
    b = portfolio.trades_between("2023-01-01", "2030-12-31")
    check("mutation did not persist", b[0].get("pnl") != 999_999_999)


# --- 4. the chart cannot hide money -----------------------------------------

def _bar_values(svg: str) -> list[float]:
    """Every dollar figure the chart actually draws, including the footer.

    ⚠ The labels are ABBREVIATED ($38k, -$299k), so reading them back is lossy
    by design. That is why the reconciliation below uses a tolerance derived
    from the rounding, not an exact match — see `_tolerance`.
    """
    out = []
    for m in re.findall(r'>(-?\$[\d,.]+[kM]?)</text>', svg):
        neg = m.startswith("-")
        body = m.lstrip("-").lstrip("$").replace(",", "")
        mult = 1.0
        if body.endswith("k"):
            mult, body = 1_000.0, body[:-1]
        elif body.endswith("M"):
            mult, body = 1_000_000.0, body[:-1]
        v = float(body) * mult
        out.append(-v if neg else v)
    return out


def _tolerance(svg: str) -> float:
    """Worst-case error from label abbreviation: half a unit per label."""
    tol = 0.0
    for m in re.findall(r'>(-?\$[\d,.]+[kM]?)</text>', svg):
        tol += 500.0 if m.endswith("k") else 500_000.0 if m.endswith("M") else 0.5
    return max(tol, 1.0)


def test_chart_reconciles_to_total() -> None:
    print("\n4. by-instrument chart reconciles to the period total")
    perf = portfolio.performance("all")
    rows = perf["by_symbol"]
    if not rows:
        return check("no data", True, "skipped")
    svg = perf_charts.category_bars(rows)
    total = sum(float(r["pnl"]) for r in rows)
    drawn = sum(_bar_values(svg))
    tol = _tolerance(svg)
    check("drawn sum == sum of all categories",
          abs(drawn - total) <= tol,
          f"drawn ${drawn:,.0f} vs total ${total:,.0f} (±${tol:,.0f} label rounding)")

    # and nothing is missing without being named: every category is either a
    # drawn label or inside the folded remainder
    labels = set(re.findall(r'>([A-Z][A-Z0-9.\- ]*)</text>', svg))
    missing = [r["key"] for r in rows if r["key"] not in labels]
    check("every category drawn or explicitly folded",
          not missing or "smaller" in svg,
          f"{len(missing)} folded" if missing else "all drawn")

    # the biggest loser must be visible, by name
    worst = min(rows, key=lambda r: float(r["pnl"]))
    check(f"biggest loser {worst['key']} is drawn", f">{worst['key']}<" in svg,
          f"${float(worst['pnl']):,.0f}")

    # and the sign of the chart must agree with the sign of the period
    check("chart agrees with the page on win/lose",
          (drawn >= 0) == (total >= 0),
          f"chart {'+' if drawn >= 0 else '-'}, page {'+' if total >= 0 else '-'}")


def test_chart_labels_what_it_drops() -> None:
    print("\n5. truncation is stated on the chart, never silent")
    many = [{"key": f"SYM{i}", "pnl": (100 - i) * (1 if i % 2 else -1), "n": 1,
             "win_rate": 50.0} for i in range(40)]
    svg = perf_charts.category_bars(many, cap=5)
    check("says how many are folded", "smaller" in svg)
    drawn = sum(_bar_values(svg))
    total = sum(r["pnl"] for r in many)
    check("folded remainder is drawn too", abs(drawn - total) <= _tolerance(svg),
          f"drawn {drawn:,.0f} vs total {total:,.0f}")


# --- 6. pagination never loses or duplicates a trade -------------------------

def test_pagination_is_lossless() -> None:
    print("\n6. paging over Trades loses nothing and repeats nothing")
    first = portfolio.performance("all", page=1)
    seen: list[str] = []
    for p in range(1, first["pages"] + 1):
        seen += [t["id"] for t in portfolio.performance("all", page=p)["trades"]]
    check("every trade appears exactly once",
          len(seen) == len(set(seen)) == first["total_trades"],
          f"{len(seen)} paged · {len(set(seen))} unique · {first['total_trades']} total")
    check("charts still see the whole period",
          len(first["all_trades"]) == first["total_trades"],
          f"{len(first['all_trades'])} rows fed to charts")


# --- mutation proofs --------------------------------------------------------

def mutation_proofs() -> None:
    print("\n7. mutation proofs — reintroduce each bug, the guard must go red")

    # (a) the original truncation bug
    real = portfolio.performance("all")["by_symbol"]
    if len(real) > 8:
        broken = perf_charts.category_bars(sorted(real, key=lambda r: -float(r["pnl"]))[:8],
                                           cap=99)
        drawn = sum(_bar_values(broken))
        total = sum(float(r["pnl"]) for r in real)
        check("(a) top-8-by-pnl truncation IS caught",
              abs(drawn - total) > _tolerance(broken),
              f"hides ${total - drawn:,.0f}")

    # (b) an off-by-one on the inclusive end bound
    end = "2026-09-18"
    inclusive = {t["id"] for t in portfolio.trades_between("2023-01-01", end)}
    exclusive = {t["id"] for t in portfolio.trades_between("2023-01-01", "2026-09-17")}
    check("(b) the end bound is genuinely inclusive", len(inclusive) > len(exclusive),
          f"{len(inclusive)} vs {len(exclusive)} — {len(inclusive) - len(exclusive)} closed on {end}")

    # (c) a chart that silently drops rows without saying so
    many = [{"key": f"S{i}", "pnl": -1000.0, "n": 1, "win_rate": 0.0} for i in range(30)]
    svg = perf_charts.category_bars(many, cap=3)
    check("(c) a 30→3 fold still reconciles",
          abs(sum(_bar_values(svg)) - (-30000.0)) <= _tolerance(svg),
          f"drawn {sum(_bar_values(svg)):,.0f}")


def main() -> int:
    print("Portfolio cache + performance chart guards")
    test_cache_matches_firestore()
    test_invalidate_rereads()
    test_cache_is_not_mutable_by_callers()
    test_chart_reconciles_to_total()
    test_chart_labels_what_it_drops()
    test_pagination_is_lossless()
    mutation_proofs()
    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
