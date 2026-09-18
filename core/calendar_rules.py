"""Event calendar rules (handoff §2.3). Opex is computed; FOMC/CPI/jobs
days come from the `events` collection (hand-maintained in Settings)."""

from __future__ import annotations

from datetime import date, timedelta

from .contracts import third_friday

RULES = {
    "fomc": ("FOMC day: quiet until 2:00 PM ET, then volatility; experienced "
             "traders are flat into the statement."),
    "quarterly_opex": ("Quarterly opex (quad witching): highest-volume session of "
                       "the quarter; first-hour push, pinned midday around large "
                       "open-interest strikes, second burst in the last 30 minutes. "
                       "Take first targets fast; breakouts/breakdowns are low odds."),
    "monthly_opex": "Monthly opex: pinning around large open-interest strikes into the close.",
    "post_opex_week": ("Week after quarterly opex: negative lean (~70% of years "
                       "down since the 1990s, worst in September)."),
    "cpi": "CPI morning: expect an 8:30 AM ET repricing; levels reset after the print.",
    "jobs": "Jobs Friday: expect an 8:30 AM ET repricing; levels reset after the print.",
    "quarter_end": "Quarter-end rebalancing flows can distort the tape late in the day.",
    "half_day": "Holiday-shortened session: thin tape, moves exaggerate; size down.",
}


def builtin_events(d: date) -> list[dict]:
    out = []
    tf = third_friday(d.year, d.month)
    if d == tf and d.month in (3, 6, 9, 12):
        out.append({"kind": "quarterly_opex", "text": RULES["quarterly_opex"]})
    elif d == tf:
        out.append({"kind": "monthly_opex", "text": RULES["monthly_opex"]})
    # week after quarterly opex
    if d.month in (3, 6, 9, 12) and tf < d <= tf + timedelta(days=7):
        out.append({"kind": "post_opex_week", "text": RULES["post_opex_week"]})
    prev_q = {1: 12, 4: 3, 7: 6, 10: 9}.get(d.month)
    if prev_q and d.day <= 5:
        ptf = third_friday(d.year if prev_q != 12 else d.year - 1, prev_q)
        if (d - ptf).days <= 7:
            out.append({"kind": "post_opex_week", "text": RULES["post_opex_week"]})
    return out
