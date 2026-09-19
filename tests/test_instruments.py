"""Multi-instrument guards.

The ES-only assumptions that used to be hardcoded are the things most likely
to silently misbehave on SPY/QQQ/a stock. Each assertion carries a mutation
proof — break it, watch it go red.

Usage: python -m tests.test_instruments
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time

from core.instruments import (CME_FUTURES, US_EQUITY, derive_bin,
                              derive_ladder_bin, resolve)
from core.profile import ET, Bar, compute_stats, overnight_window, rth_window

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def test_resolve():
    print("Instrument resolution:")
    check("ES is a future on the CME clock",
          resolve("ES").is_future and resolve("ES").session.key == "cme_futures")
    check("SPY is an equity-session ETF",
          resolve("SPY").kind == "etf" and resolve("SPY").session.key == "us_equity")
    check("an unknown ticker resolves, never raises",
          resolve("TSLA").kind == "stock" and resolve("TSLA").session.key == "us_equity")
    check("lowercase input normalises", resolve("  spy ").symbol == "SPY")
    check("only ES claims author levels",
          resolve("ES").has_author_levels and not resolve("SPY").has_author_levels)


def test_sessions():
    print("Session windows differ by instrument class:")
    d = date(2026, 9, 18)
    f_start, _ = overnight_window(d, CME_FUTURES)
    e_start, _ = overnight_window(d, US_EQUITY)
    check("futures overnight starts 18:00 the PREVIOUS day",
          f_start.date() == date(2026, 9, 17) and f_start.time() == time(18, 0),
          str(f_start))
    check("equity pre-market starts 04:00 the SAME day",
          e_start.date() == d and e_start.time() == time(4, 0), str(e_start))
    # The bug this prevents: applying the futures window to SPY would sweep in
    # the prior afternoon's post-market and label it "overnight".
    check("the two windows are genuinely different", f_start != e_start)
    r1, r2 = rth_window(d, US_EQUITY)
    check("RTH is 09:30-16:00 for equities",
          r1.time() == time(9, 30) and r2.time() == time(16, 0))


def test_bins():
    print("Profile bin width scales with price:")
    es, spy, cheap = derive_bin(7700, "future"), derive_bin(761, "etf"), derive_bin(40, "stock")
    check("ES-scale price gets a wide bin", es >= 1, str(es))
    check("SPY-scale price gets a sub-point bin", 0.1 <= spy <= 1, str(spy))
    check("a $40 stock gets a fine bin", cheap <= 0.1, str(cheap))
    check("bins are ordered by price", cheap < spy <= es)
    check("ladder bins are coarser than histogram bins",
          derive_ladder_bin(761, "etf") > derive_bin(761, "etf"))
    check("zero/negative price does not divide by zero", derive_bin(0, "stock") > 0)


def test_histogram_bins():
    """A fixed 1-point bin on a cheap instrument collapses the profile."""
    print("Histogram honours the bin width:")
    bars = [Bar(ts=datetime(2026, 9, 18, 10, i, tzinfo=ET), open=40 + i * 0.1,
                high=40.2 + i * 0.1, low=39.9 + i * 0.1, close=40.1 + i * 0.1,
                volume=1000) for i in range(20)]
    coarse = compute_stats(bars, "coarse", date(2026, 9, 18), "rth", bw=1.0)
    fine = compute_stats(bars, "fine", date(2026, 9, 18), "rth", bw=0.05)
    check("1-point bins collapse a $40 instrument", len(coarse.histogram) <= 4,
          f"{len(coarse.histogram)} bins")
    check("fine bins resolve it", len(fine.histogram) > 15, f"{len(fine.histogram)} bins")
    check("value area still ordered at fine resolution",
          fine.val <= fine.poc <= fine.vah)
    check("VA stays inside the session range",
          fine.low <= fine.val and fine.vah <= fine.high)


def mutation_proof():
    print("Mutation proof:")
    # If overnight_window ignored the spec, these would be equal.
    d = date(2026, 9, 18)
    differs = overnight_window(d, CME_FUTURES)[0] != overnight_window(d, US_EQUITY)[0]
    check("session spec actually changes the window", differs)
    # If derive_bin ignored price, these would be equal.
    check("bin width actually depends on price", derive_bin(40, "s") != derive_bin(7700, "f"))


def main() -> int:
    test_resolve()
    test_sessions()
    test_bins()
    test_histogram_bins()
    mutation_proof()
    print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)}): {FAILS}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
