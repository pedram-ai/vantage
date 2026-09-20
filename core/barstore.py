"""The local bar archive.

Two jobs, and the first is the one that matters:

⭐ **SOME OF THIS DATA CANNOT BE RE-FETCHED.** Yahoo keeps only **7 days** of
1-minute bars — measured 2026-09-20: `interval=1m&range=7d` returns 6,201 bars
and `range=30d` is a hard **422**. Every minute we do not store is gone. The
archive is therefore not a cache; it is the only copy of most of what it holds.

⚠ Second job: stop re-fetching what we already have. The in-process bar cache
is 60 s and dies with the instance, so a cold container, a cache expiry, or a
second Cloud Run instance all went back to Yahoo for bars that had not changed
since the session closed.

⛔⛔ THE INTERVAL GUARD IS NOT OPTIONAL. `range=max` **silently coerces the
interval to monthly**: measured, `interval=1d&range=max` returns 405 bars with
a 31-day median gap, as does `1wk&range=max`. Nothing errors. Storing those as
daily bars would poison every moving average and every backtest with data that
looks completely ordinary. Every fetch is checked against the interval it
claimed to be, and a mismatch is REFUSED rather than written.

Layout: `gs://argentridge-bars/<symbol>/<interval>/<YYYY-MM>.ndjson.gz`
— one object per month, newline-delimited JSON, gzipped. A month is small
(1-minute SPY for a month is ~8k bars, well under 100 KB compressed) and
whole-month rewrite avoids the read-modify-write races that per-append files
would create.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from .profile import Bar

BUCKET = os.environ.get("VANTAGE_BARS_BUCKET", "argentridge-bars")

# What we keep, and the widest window Yahoo will actually serve for each.
# ⛔ NEVER "max" — see the module docstring.
# ⚠ `part` is the PARTITION GRAIN, and it is not cosmetic. Partitioning 10
# years of daily bars by month produced 121 objects holding 2,514 rows between
# them — a read became 121 GCS round trips and took 16 s. The grain is chosen
# so one object holds roughly a few thousand bars: months for minute data,
# years for hourly, a single file for daily and weekly.
INTERVALS: dict[str, dict] = {
    "1m":  {"secs": 60,     "range": "7d",   "label": "1 min",  "part": "%Y-%m"},
    "5m":  {"secs": 300,    "range": "60d",  "label": "5 min",  "part": "%Y-%m"},
    "15m": {"secs": 900,    "range": "60d",  "label": "15 min", "part": "%Y"},
    "30m": {"secs": 1800,   "range": "60d",  "label": "30 min", "part": "%Y"},
    "1h":  {"secs": 3600,   "range": "730d", "label": "1 hour", "part": "%Y"},
    "1d":  {"secs": 86400,  "range": "10y",  "label": "Daily",  "part": "all"},
    "1wk": {"secs": 604800, "range": "10y",  "label": "Weekly", "part": "all"},
}

# Derived by resampling, never fetched — Yahoo has no 4-hour interval.
DERIVED = {"4h": ("1h", 4)}

_LOCK = threading.Lock()
_MEM: dict[str, tuple[float, list[Bar]]] = {}
_MEM_TTL = 300.0

# ⛔ A SYNC ATTEMPT IS REMEMBERED, NOT JUST A SUCCESS. Over a weekend the last
# daily bar is Friday's and is therefore ~52 h "stale" — but no newer bar can
# exist, so the staleness test fired on every single request and re-fetched
# 2,514 bars to learn nothing. Measured: 8.3 s on every chart load.
#
# The cooldown is capped at an hour so a live session still updates promptly,
# and floored at a minute so a 1-minute chart is not throttled.
_TRIED: dict[str, float] = {}


def _cooldown(interval: str) -> float:
    secs = INTERVALS.get(interval, {}).get("secs", 3600)
    return max(60.0, min(float(secs), 3600.0))
_client = None


def _gcs():
    global _client
    if _client is None:
        from google.cloud import storage
        _client = storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT",
                                                        "patexia-vantage"))
    return _client


def _blob(symbol: str, interval: str, month: str):
    return _gcs().bucket(BUCKET).blob(f"{symbol}/{interval}/{month}.ndjson.gz")


# --- serialisation ----------------------------------------------------------

def _dump(bars: list[Bar]) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
        for b in bars:
            gz.write((json.dumps({
                "t": int(b.ts.timestamp()), "o": b.open, "h": b.high,
                "l": b.low, "c": b.close, "v": b.volume,
            }) + "\n").encode())
    return buf.getvalue()


def _load(data: bytes) -> list[Bar]:
    out = []
    with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as gz:
        for line in gz:
            if not line.strip():
                continue
            d = json.loads(line)
            out.append(Bar(ts=datetime.fromtimestamp(d["t"], tz=timezone.utc),
                           open=d["o"], high=d["h"], low=d["l"],
                           close=d["c"], volume=d.get("v") or 0))
    return out


def _parts(bars: list[Bar], interval: str) -> dict[str, list[Bar]]:
    fmt = INTERVALS.get(interval, {}).get("part", "%Y-%m")
    out: dict[str, list[Bar]] = {}
    for b in bars:
        out.setdefault("all" if fmt == "all" else b.ts.strftime(fmt), []).append(b)
    return out


# --- the interval guard -----------------------------------------------------

def interval_ok(bars: list[Bar], interval: str, tol: float = 0.45) -> tuple[bool, str]:
    """Do these bars actually have the spacing the interval claims?

    ⛔ THIS IS THE GUARD THAT CATCHES `range=max`. Uses the MEDIAN gap, because
    real data is full of legitimate holes — overnight, weekends, holidays — and
    a mean would be dragged by every one of them. The median of a daily series
    is 1 day even across a long weekend; the median of a monthly series
    masquerading as daily is 31.
    """
    if len(bars) < 3:
        return True, "too few bars to check"
    want = INTERVALS.get(interval, {}).get("secs")
    if not want:
        return True, "unknown interval, not checked"
    gaps = sorted((bars[i + 1].ts - bars[i].ts).total_seconds()
                  for i in range(len(bars) - 1))
    med = gaps[len(gaps) // 2]
    if med <= 0:
        return False, "duplicate timestamps"
    ratio = med / want
    if ratio > 1 + tol or ratio < 1 - tol:
        return False, (f"spacing is {med/60:.0f} min, expected "
                       f"{want/60:.0f} min ({ratio:.1f}x)")
    return True, f"median gap {med/60:.0f} min"


# --- reading ----------------------------------------------------------------

def read_store(symbol: str, interval: str, months: int = 0) -> list[Bar]:
    """Everything held for (symbol, interval). Never raises."""
    key = f"{symbol}:{interval}"
    now = time.monotonic()
    with _LOCK:
        hit = _MEM.get(key)
        if hit and now - hit[0] < _MEM_TTL:
            return list(hit[1])
    bars: list[Bar] = []
    try:
        prefix = f"{symbol}/{interval}/"
        names = sorted(b.name for b in _gcs().list_blobs(BUCKET, prefix=prefix))  # noqa: E501
        if months:
            names = names[-months:]
        for name in names:
            try:
                bars.extend(_load(_gcs().bucket(BUCKET).blob(name).download_as_bytes()))
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        return []
    bars.sort(key=lambda b: b.ts)
    with _LOCK:
        _MEM[key] = (now, bars)
    return list(bars)


def coverage(symbol: str, interval: str) -> dict:
    """What the archive holds. For System Health and the Charts page."""
    bars = read_store(symbol, interval)
    if not bars:
        return {"symbol": symbol, "interval": interval, "bars": 0,
                "first": None, "last": None, "stale_hours": None}
    last = bars[-1].ts
    return {
        "symbol": symbol, "interval": interval, "bars": len(bars),
        "first": bars[0].ts.isoformat(), "last": last.isoformat(),
        "stale_hours": round((datetime.now(timezone.utc) - last).total_seconds() / 3600, 1),
    }


# --- writing ----------------------------------------------------------------

def _merge(old: list[Bar], new: list[Bar]) -> list[Bar]:
    """Newer wins on a tie — the last bar of a live session keeps updating."""
    by_ts = {b.ts: b for b in old}
    by_ts.update({b.ts: b for b in new})
    return [by_ts[t] for t in sorted(by_ts)]


def sync(symbol: str, interval: str, force: bool = False) -> dict:
    """Fetch only what is missing and store it. Never raises.

    ⭐ THE WHOLE POINT: if the archive is already current, this makes NO
    network call at all. "Current" means the newest stored bar is less than one
    interval old, or the market has not produced a new bar since.
    """
    from .bars import fetch_bars

    spec = INTERVALS.get(interval)
    if not spec:
        return {"ok": False, "reason": f"unknown interval {interval}"}

    key = f"{symbol}:{interval}"
    held = read_store(symbol, interval)
    if held and not force:
        age = (datetime.now(timezone.utc) - held[-1].ts).total_seconds()
        if age < spec["secs"]:
            return {"ok": True, "fetched": 0, "added": 0, "skipped": "already current",
                    "held": len(held)}
        with _LOCK:
            last = _TRIED.get(key)
        if last and time.monotonic() - last < _cooldown(interval):
            # We already asked recently and got nothing. The market has not
            # produced a bar since; asking again cannot change that.
            return {"ok": True, "fetched": 0, "added": 0,
                    "skipped": "asked recently, nothing new", "held": len(held)}
    with _LOCK:
        _TRIED[key] = time.monotonic()

    try:
        fresh, _meta = fetch_bars(symbol, interval=interval,
                                  range_=spec["range"], use_cache=False)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": str(e)[:140], "held": len(held)}

    ok, why = interval_ok(fresh, interval)
    if not ok:
        # ⛔ REFUSE. Writing these would corrupt the archive permanently, and
        # nothing downstream would look wrong.
        return {"ok": False, "reason": f"refused: {why}", "fetched": len(fresh),
                "held": len(held)}

    merged = _merge(held, fresh)
    added = len(merged) - len(held)
    if added <= 0 and not force:
        return {"ok": True, "fetched": len(fresh), "added": 0,
                "skipped": "nothing new", "held": len(held)}

    # Rewrite only the months that actually changed.
    all_parts = _parts(merged, interval)
    # Rewrite only the partitions the new bars land in.
    touched = (set(all_parts) if not held
               else set(_parts(fresh, interval)))
    # ⛔⛔ A FAILED WRITE IS A FAILED SYNC. The first version swallowed every
    # upload error and still returned ok:True with months_written:0 — so a
    # missing google-cloud-storage dependency reported a healthy archive that
    # contained nothing. A store that silently stores nothing and claims
    # success is worse than one that refuses.
    wrote, failed = 0, []
    for month in sorted(touched):
        try:
            _blob(symbol, interval, month).upload_from_string(
                _dump(all_parts[month]), content_type="application/gzip")
            wrote += 1
        except Exception as e:  # noqa: BLE001
            failed.append(f"{month}: {type(e).__name__} {str(e)[:80]}")
    with _LOCK:
        _MEM.pop(f"{symbol}:{interval}", None)
    if failed:
        return {"ok": False, "reason": f"{len(failed)} month(s) failed to write",
                "errors": failed[:3], "fetched": len(fresh),
                "months_written": wrote, "held": len(held)}
    return {"ok": True, "fetched": len(fresh), "added": added,
            "months_written": wrote, "held": len(merged)}


def sync_all(symbols: list[str], intervals: list[str] | None = None) -> list[dict]:
    out = []
    for s in symbols:
        for iv in (intervals or list(INTERVALS)):
            r = sync(s, iv)
            out.append({"symbol": s, "interval": iv, **r})
    return out


# --- the read path the charts use -------------------------------------------

def resample(bars: list[Bar], factor: int) -> list[Bar]:
    """Combine N bars into one. Used for 4h, which Yahoo does not serve.

    ⚠ Groups by INDEX, not by clock. Grouping 1-hour bars by wall clock would
    silently merge the last hour of one session with the first of the next
    across a weekend.
    """
    out = []
    for i in range(0, len(bars), factor):
        chunk = bars[i:i + factor]
        if not chunk:
            continue
        out.append(Bar(ts=chunk[0].ts, open=chunk[0].open,
                       high=max(b.high for b in chunk),
                       low=min(b.low for b in chunk),
                       close=chunk[-1].close,
                       volume=sum(b.volume or 0 for b in chunk)))
    return out


def bars_for(symbol: str, interval: str, limit: int = 400,
             auto_sync: bool = True) -> tuple[list[Bar], dict]:
    """Bars for a chart. Reads the archive; syncs only if it is behind."""
    if interval in DERIVED:
        base, factor = DERIVED[interval]
        raw, info = bars_for(symbol, base, limit * factor, auto_sync)
        return resample(raw, factor)[-limit:], {**info, "derived_from": base}

    info = {"synced": None}
    if auto_sync:
        info["synced"] = sync(symbol, interval)
    bars = read_store(symbol, interval)
    if not bars and auto_sync:
        # first ever run for this pair
        info["synced"] = sync(symbol, interval, force=True)
        bars = read_store(symbol, interval)
    return bars[-limit:], info


def sma(bars: list[Bar], n: int) -> list[float | None]:
    """Simple moving average, aligned to `bars`. None until there are n points."""
    out: list[float | None] = []
    run = 0.0
    for i, b in enumerate(bars):
        run += b.close
        if i >= n:
            run -= bars[i - n].close
        out.append(run / n if i >= n - 1 else None)
    return out
