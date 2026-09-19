"""Instruments as data, not code.

Vantage began as an ES-only tool: the contract, the 5-point profile bins and
the CME session clock were hardcoded in three different modules. Supporting
SPY, QQQ, NVDA and anything else means all of that becomes a record.

Adding an instrument is a row here (or a symbol typed into a watchlist), not
a release.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time

from .contracts import active_es_contract

# --- session specs ----------------------------------------------------------
# All times ET. `overnight` means "the session that leads into this RTH open".


@dataclass(frozen=True)
class SessionSpec:
    key: str
    rth_start: time
    rth_end: time
    # Overnight/extended window that precedes the RTH open.
    pre_start: time
    pre_starts_prev_day: bool
    label_overnight: str

    def has_overnight(self) -> bool:
        return self.pre_starts_prev_day or self.pre_start < self.rth_start


CME_FUTURES = SessionSpec(
    key="cme_futures",
    rth_start=time(9, 30), rth_end=time(16, 0),
    pre_start=time(18, 0), pre_starts_prev_day=True,
    label_overnight="Overnight",
)

US_EQUITY = SessionSpec(
    key="us_equity",
    rth_start=time(9, 30), rth_end=time(16, 0),
    # US equities have no true overnight; pre-market opens 04:00 ET.
    pre_start=time(4, 0), pre_starts_prev_day=False,
    label_overnight="Pre-market",
)

SESSIONS = {s.key: s for s in (CME_FUTURES, US_EQUITY)}


# --- instruments ------------------------------------------------------------

@dataclass
class Instrument:
    symbol: str                 # what Pedram types: ES, SPY, QQQ, NVDA
    kind: str                   # future | etf | stock
    name: str = ""
    session_key: str = "us_equity"
    bin_pts: float | None = None       # profile bin width; None = derive
    ladder_bin: float | None = None
    has_author_levels: bool = False
    _yahoo: str | None = None
    _schwab: str | None = None

    @property
    def session(self) -> SessionSpec:
        return SESSIONS.get(self.session_key, US_EQUITY)

    @property
    def is_future(self) -> bool:
        return self.kind == "future"

    def yahoo_symbol(self, today: date | None = None) -> str:
        if self._yahoo:
            return self._yahoo
        if self.symbol == "ES":
            return f"{active_es_contract(today or date.today())}.CME"
        return self.symbol

    def schwab_symbol(self, today: date | None = None) -> str:
        if self._schwab:
            return self._schwab
        if self.is_future:
            return f"/{self.symbol}"
        return self.symbol

    def display_contract(self, today: date | None = None) -> str:
        if self.symbol == "ES":
            return active_es_contract(today or date.today())
        return self.symbol

    def to_dict(self, today: date | None = None) -> dict:
        return {
            "symbol": self.symbol, "kind": self.kind, "name": self.name,
            "contract": self.display_contract(today),
            "session": self.session_key,
            "has_author_levels": self.has_author_levels,
        }


# Instruments Vantage knows about out of the box. Anything else typed into a
# watchlist is resolved as a US equity/ETF, which is the correct default for a
# plain ticker.
BUILTIN: dict[str, Instrument] = {
    "ES": Instrument("ES", "future", "E-mini S&P 500", "cme_futures",
                     bin_pts=1, ladder_bin=5, has_author_levels=True),
    "NQ": Instrument("NQ", "future", "E-mini Nasdaq 100", "cme_futures",
                     bin_pts=1, ladder_bin=10),
    "SPY": Instrument("SPY", "etf", "SPDR S&P 500 ETF"),
    "QQQ": Instrument("QQQ", "etf", "Invesco QQQ Trust"),
    "IWM": Instrument("IWM", "etf", "iShares Russell 2000 ETF"),
    "DIA": Instrument("DIA", "etf", "SPDR Dow Jones ETF"),
}


def resolve(symbol: str) -> Instrument:
    """Never fails — an unknown ticker is a US equity until proven otherwise."""
    s = (symbol or "").strip().upper()
    if s in BUILTIN:
        return BUILTIN[s]
    return Instrument(s, "stock", s)


def derive_bin(price: float, kind: str) -> float:
    """Profile bin width when the instrument doesn't pin one.

    A fixed bin can't serve both a 7,700-point future and a $40 stock: 1-point
    bins on ES are right and on a $40 stock collapse the whole profile into a
    handful of buckets. Scale with price instead — roughly 0.05% of price,
    snapped to a human number.
    """
    if price <= 0:
        return 1.0
    target = price * 0.0005
    for step in (0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 25):
        if target <= step:
            return float(step)
    return 50.0


def derive_ladder_bin(price: float, kind: str) -> float:
    """Ladder rows are coarser than the histogram so the list stays readable —
    but not so coarse that a 3-day range collapses to six rows."""
    return derive_bin(price, kind) * 2
