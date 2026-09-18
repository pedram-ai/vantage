"""Active ES front-month contract handling (handoff §2.4).

Quarterly cycle Mar(H)/Jun(M)/Sep(U)/Dec(Z); roll ~8 days before the 3rd
Friday of the expiry month. No back-adjusting across rolls.
"""

from __future__ import annotations

from datetime import date, timedelta

MONTH_CODES = {3: "H", 6: "M", 9: "U", 12: "Z"}


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def active_es_contract(today: date) -> str:
    """Return e.g. 'ESZ26' for the active front month on `today`."""
    for k in range(0, 5):
        month = ((today.month - 1) // 3 + 1 + k) * 3
        year = today.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        if month not in MONTH_CODES:
            continue
        roll = third_friday(year, month) - timedelta(days=8)
        if today < roll:
            return f"ES{MONTH_CODES[month]}{year % 100:02d}"
    # fallback: next quarter
    return f"ESH{(today.year + 1) % 100:02d}"


def yahoo_symbol(contract: str) -> str:
    return f"{contract}.CME"
