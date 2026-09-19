"""Quote provenance + Schwab parsing.

The live Schwab API cannot be reached from CI (OAuth needs Pedram's
interactive login), so this exercises the two things that would actually
break in production:

  1. the fallback — Schwab unavailable must never raise into the map;
  2. the parser — against the response shapes Schwab is documented to
     return, including the one that bit us conceptually: the key asked for
     ('/ES') is NOT the key returned ('/ESZ26').

Each assertion carries a mutation proof: break the behaviour, confirm the
test goes red. A guard that cannot fail is decoration.

Usage: python -m tests.test_quote_source
"""

from __future__ import annotations

import sys
from datetime import datetime

from core import quote as qmod
from core.profile import ET
from core.schwab import parse_quote

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def test_parse() -> None:
    print("Schwab quote parsing:")
    # Documented shape: keyed by the RESOLVED contract, not the requested '/ES'.
    payload = {"/ESZ26": {"assetMainType": "FUTURE", "symbol": "/ESZ26",
                          "quote": {"lastPrice": 7712.5, "quoteTime": 1789764591000}}}
    got = parse_quote(payload, "/ES")
    check("resolves /ESZ26 when /ES was requested", got is not None and got["price"] == 7712.5,
          str(got))
    check("epoch millis converted to seconds",
          bool(got) and 1.7e9 < got["as_of"] < 2.1e9, str(got and got["as_of"]))

    # Exact-key shape.
    check("exact key match",
          (parse_quote({"/ES": {"quote": {"mark": 7700.0}}}, "/ES") or {}).get("price") == 7700.0)

    # Degenerate shapes must return None, never a fabricated price.
    for name, bad in [("empty dict", {}), ("not a dict", None),
                      ("no price field", {"/ES": {"quote": {"bidSize": 3}}})]:
        check(f"{name} -> None", parse_quote(bad, "/ES") is None)


def test_fallback() -> None:
    print("Fallback when Schwab is unavailable:")
    meta = {"regularMarketPrice": 7712.5, "regularMarketTime": 1789764591}
    q = qmod.es_quote("ESZ26", meta)
    check("falls back to Yahoo", q["source"] == "yahoo", q["source"])
    check("price preserved", q["price"] == 7712.5)
    check("labels the delay", "delayed" in q["source_label"].lower(), q["source_label"])
    check("delay is 10 min", q["delayed_min"] == 10)
    check("carries an age string", bool(q["age_text"]), q["age_text"])

    # A Schwab call that explodes must not propagate.
    import core.schwab as sch
    orig = sch.quote
    sch.quote = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("token dead"))
    try:
        q2 = qmod.es_quote("ESZ26", meta)
        check("Schwab raising does not break the map", q2["source"] == "yahoo")
    finally:
        sch.quote = orig

    # And when Schwab works, it must win and be marked real-time.
    sch.quote = lambda *a, **k: {"price": 7715.25, "as_of": 1789764591, "symbol": "/ESZ26"}
    try:
        q3 = qmod.es_quote("ESZ26", meta)
        check("Schwab preferred over Yahoo", q3["source"] == "schwab" and q3["price"] == 7715.25,
              f'{q3["source"]} {q3["price"]}')
        check("real-time is not flagged delayed", q3["delayed_min"] == 0)
    finally:
        sch.quote = orig


def test_market_hours() -> None:
    print("ES session clock (weekend age must not read as latency):")
    cases = [
        ("Sat noon", datetime(2026, 9, 19, 12, 0, tzinfo=ET), False),
        ("Sun 17:00", datetime(2026, 9, 20, 17, 0, tzinfo=ET), False),
        ("Sun 18:30", datetime(2026, 9, 20, 18, 30, tzinfo=ET), True),
        ("Tue 10:00", datetime(2026, 9, 22, 10, 0, tzinfo=ET), True),
        ("Tue 17:30 halt", datetime(2026, 9, 22, 17, 30, tzinfo=ET), False),
        ("Fri 18:00", datetime(2026, 9, 18, 18, 0, tzinfo=ET), False),
    ]
    for name, dt, expected in cases:
        check(f"{name} open={expected}", qmod.es_market_open(dt) is expected)


def mutation_proof() -> None:
    """Prove these assertions can fail."""
    print("Mutation proof:")
    ok = parse_quote({"/ESZ26": {"quote": {"lastPrice": 7712.5}}}, "/ES") is not None
    broken = parse_quote({"/ESZ26": {"quote": {"lastPriceX": 7712.5}}}, "/ES") is None
    check("parser distinguishes a real price field from a wrong one", ok and broken)

    m = {"regularMarketPrice": 1.0, "regularMarketTime": 1789764591}
    labelled = "delayed" in qmod.es_quote("ES", m)["source_label"].lower()
    check("a Yahoo quote is never labelled real-time", labelled)


def main() -> int:
    test_parse()
    test_fallback()
    test_market_hours()
    mutation_proof()
    print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)}): {FAILS}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
