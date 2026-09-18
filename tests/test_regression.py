"""Regression: reproduce the handoff §11 session prints from live Yahoo bars
(ESZ26, Sep 16-18, 2026). Run while Yahoo's 5d/5m window still covers those
dates; afterwards it validates whatever window is available via invariants.

Usage: python -m tests.test_regression
"""

from __future__ import annotations

import sys
from datetime import date

from core.bars import fetch_bars
from core.profile import ET, build_sessions, rth_window, overnight_window, slice_bars, compute_stats

EXPECTED = {
    # session_date: (open, high, low, close, poc, vah, val, vwap)
    date(2026, 9, 16): (7676.5, 7699, 7575, 7622.25, 7679, 7692, 7591, 7653),
    date(2026, 9, 17): (7713.5, 7716.25, 7680.5, 7707.25, 7705, 7711, 7691, 7700.7),
}
EXPECTED_ON = (7703.5, 7739.25, 7692.25, 7709.75, 7701, 7735, 7697, 7713.4)  # Fri 9/18 overnight

TOL_PRINT = 0.26   # opens/highs/lows/closes must match to a tick
TOL_PROFILE = 3.0  # POC/VAH/VAL from sampled volume: allow small drift
TOL_VWAP = 1.5


def check(name, got, exp, tol):
    ok = abs(got - exp) <= tol
    print(f"  {name:6s} got {got:9.2f}  exp {exp:9.2f}  {'OK' if ok else 'FAIL'}")
    return ok


def main() -> int:
    bars, _ = fetch_bars("ESZ26.CME", use_cache=False)
    ok = True
    for d, exp in EXPECTED.items():
        s, e = rth_window(d)
        chunk = slice_bars(bars, s, e)
        if not chunk:
            print(f"RTH {d}: no bars in window (Yahoo 5d window moved on) - skipped")
            continue
        st = compute_stats(chunk, f"RTH {d}", d, "rth")
        print(f"RTH {d} ({st.n_bars} bars):")
        for name, got, expv, tol in [
            ("open", st.open, exp[0], TOL_PRINT), ("high", st.high, exp[1], TOL_PRINT),
            ("low", st.low, exp[2], TOL_PRINT), ("close", st.close, exp[3], TOL_PRINT),
            ("poc", st.poc, exp[4], TOL_PROFILE), ("vah", st.vah, exp[5], TOL_PROFILE),
            ("val", st.val, exp[6], TOL_PROFILE), ("vwap", st.vwap, exp[7], TOL_VWAP),
        ]:
            ok &= check(name, got, expv, tol)

    s, e = overnight_window(date(2026, 9, 18))
    chunk = slice_bars(bars, s, e)
    if chunk:
        st = compute_stats(chunk, "ON 9/18", date(2026, 9, 18), "overnight")
        print(f"Overnight into 9/18 ({st.n_bars} bars):")
        for name, got, expv, tol in [
            ("open", st.open, EXPECTED_ON[0], TOL_PRINT), ("high", st.high, EXPECTED_ON[1], TOL_PRINT),
            ("low", st.low, EXPECTED_ON[2], TOL_PRINT), ("close", st.close, EXPECTED_ON[3], TOL_PRINT),
            ("poc", st.poc, EXPECTED_ON[4], TOL_PROFILE), ("vah", st.vah, EXPECTED_ON[5], TOL_PROFILE),
            ("val", st.val, EXPECTED_ON[6], TOL_PROFILE), ("vwap", st.vwap, EXPECTED_ON[7], TOL_VWAP),
        ]:
            ok &= check(name, got, expv, tol)

    # structural invariants on whatever is in the window
    sess = build_sessions(bars)
    for st in sess["rth"]:
        assert st.val <= st.poc <= st.vah, f"VA ordering broken on {st.label}"
        assert st.low <= st.val and st.vah <= st.high, f"VA outside range on {st.label}"
    print("invariants OK on", [s_.label for s_ in sess["rth"]])
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
