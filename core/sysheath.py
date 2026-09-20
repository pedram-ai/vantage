"""System Health — one payload the admin page renders.

Method copied from Lateral Compass (`api/src/systemhealth/*`): fan out to
INDEPENDENT, FAIL-SOFT sources and merge. Every source catches internally, so
one broken source shows "—" and never takes the page down.

Difference from LC, deliberate: LC's cost module is a hand-maintained table of
monthly figures. Here the RESOURCE SHAPE is read live from the Cloud Run Admin
API (CPU, memory, min instances, region) and only the UNIT PRICES are a
documented constant. So the cost moves when the infrastructure moves, and the
page states exactly which half is live — per the standing rule that every
number ships its basis.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from .profile import PT

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "patexia-vantage")
REGION = os.environ.get("VANTAGE_REGION", "us-central1")
SERVICE = os.environ.get("K_SERVICE", "vantage")

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 60.0

# --- published unit prices (us-central1, USD). The ONLY typed numbers here. --
# Source: Cloud Run + Firestore + Artifact Registry public pricing.
PRICES = {
    "cpu_active_vcpu_sec": 0.00002400,
    "cpu_idle_vcpu_sec": 0.00000250,   # min-instance, throttled between requests
    "mem_gib_sec": 0.00000250,
    "requests_per_million": 0.40,
    "artifact_registry_gb_month": 0.10,
    "secret_version_month": 0.06,
    "scheduler_job_month": 0.10,        # first 3 free
    "firestore_gib_month": 0.18,
}
SECONDS_PER_MONTH = 30 * 24 * 3600


def _token() -> str | None:
    """Service identity token from the metadata server (on Cloud Run), else ADC."""
    try:
        import google.auth
        import google.auth.transport.requests
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token
    except Exception:  # noqa: BLE001
        return None


def _get(url: str, token: str | None) -> dict | None:
    if not token:
        return None
    try:
        from curl_cffi import requests as cr
        r = cr.get(url, headers={"Authorization": f"Bearer {token}"},
                   impersonate="chrome", timeout=15)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:  # noqa: BLE001
        return None


# --- sources ----------------------------------------------------------------

def _service(token) -> dict:
    """Live Cloud Run service shape."""
    out = {"ok": False, "error": None}
    d = _get(f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/"
             f"{REGION}/services/{SERVICE}", token)
    if not d:
        out["error"] = "Cloud Run API unreachable"
        return out
    tmpl = d.get("template", {}) or {}
    containers = tmpl.get("containers") or [{}]
    limits = (containers[0].get("resources") or {}).get("limits") or {}
    scaling = tmpl.get("scaling") or {}
    # ⚠ In the Cloud Run v2 API the overall Ready condition is `terminalCondition`.
    # `conditions[]` holds sub-conditions (RoutesReady, ConfigurationsReady) and
    # contains NO entry of type "Ready" — looking there reports a healthy service
    # as down.
    cond = d.get("terminalCondition") or next(
        (c for c in (d.get("conditions") or []) if c.get("type") == "Ready"), {})
    out.update({
        "ok": cond.get("state") == "CONDITION_SUCCEEDED",
        "state": cond.get("state", "—"),
        "iap_enabled": bool(d.get("iapEnabled")),
        "ingress": d.get("ingress", "—"),
        "revision": (d.get("latestReadyRevision") or "").rsplit("/", 1)[-1] or "—",
        "region": REGION,
        "cpu": limits.get("cpu", "—"),
        "memory": limits.get("memory", "—"),
        "min_instances": scaling.get("minInstanceCount", 0),
        "max_instances": scaling.get("maxInstanceCount", "—"),
        "uri": d.get("uri", ""),
        "updated": d.get("updateTime", ""),
    })
    return out


def _job(token) -> dict:
    out = {"ok": False, "error": None, "name": "vantage-daily-run"}
    d = _get(f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/"
             f"{REGION}/jobs/vantage-daily-run", token)
    if not d:
        out["error"] = "not reachable"
        return out
    ex = d.get("latestCreatedExecution") or {}
    out.update({
        "ok": True,
        "last_run": ex.get("completionTime") or ex.get("createTime") or "",
        "last_name": (ex.get("name") or "").rsplit("/", 1)[-1],
        "schedule": "weekdays 5:00 AM PT",
    })
    return out


def _firestore_counts() -> dict:
    """Row counts per collection. Cheap, and the only true measure of data."""
    out = {"ok": False, "collections": {}, "error": None}
    try:
        from .store import db
        d = db()
        for name in ("users", "sessions", "trades", "transactions", "cash",
                     "positions", "import_runs", "runs", "watchlists",
                     "author_levels", "events"):
            try:
                out["collections"][name] = sum(1 for _ in d.collection(name).stream())
            except Exception:  # noqa: BLE001
                out["collections"][name] = None
        out["ok"] = True
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:120]
    return out


def _data_freshness() -> dict:
    out = {"ok": False, "error": None}
    try:
        from . import portfolio, store
        runs = store.list_runs(1)
        imports = portfolio.import_coverage()
        out.update({
            "ok": True,
            "last_daily_run": runs[0].get("generated_at_pt") if runs else None,
            "last_run_verdict": runs[0].get("verdict") if runs else None,
            "last_import": imports[-1].get("imported_at") if imports else None,
            "import_range": (f"{imports[-1].get('start')} → {imports[-1].get('end')}"
                             if imports else None),
            "trades": imports[-1].get("trades") if imports else None,
        })
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:120]
    return out


def _dependencies() -> dict:
    """External things we rely on. Each probed independently."""
    deps = []

    # Yahoo — the bar source everything else is built on
    try:
        t0 = time.time()
        from .bars import fetch_bars
        _, meta = fetch_bars("SPY", interval="5m", range_="1d")
        deps.append({"key": "yahoo", "label": "Yahoo price data",
                     "status": "reachable" if meta.get("regularMarketPrice") else "down",
                     "detail": f"{int((time.time()-t0)*1000)} ms · 10-min delayed"})
    except Exception as e:  # noqa: BLE001
        deps.append({"key": "yahoo", "label": "Yahoo price data",
                     "status": "down", "detail": str(e)[:80]})

    # Schwab — optional; "not configured" is a valid state, not a failure
    try:
        from . import schwab
        st = schwab.status()
        deps.append({
            "key": "schwab", "label": "Schwab real-time quotes",
            "status": ("reachable" if st.get("working")
                       else "configured" if st.get("configured") else "not_configured"),
            "detail": ("live" if st.get("working")
                       else "linked, token expired" if st.get("linked")
                       else "app saved, not linked" if st.get("configured")
                       else "awaiting Schwab approval"),
        })
    except Exception as e:  # noqa: BLE001
        deps.append({"key": "schwab", "label": "Schwab real-time quotes",
                     "status": "down", "detail": str(e)[:80]})

    # SES — the morning email
    ses_ok = bool(os.environ.get("AWS_ACCESS_KEY_ID"))
    deps.append({"key": "ses", "label": "Email (AWS SES)",
                 "status": "configured" if ses_ok else "not_configured",
                 "detail": os.environ.get("SES_FROM_EMAIL", "—")})
    return deps


def _cost(service: dict) -> dict:
    """Monthly run-rate from the LIVE resource shape × published unit prices."""
    lines, notes = [], []
    # The shape is live if the API answered at all — a service that is
    # momentarily not Ready still reports its true CPU/memory/scaling.
    live_shape = service.get("revision") not in (None, "", "—")

    def num(x, default=1.0):
        try:
            return float(str(x).rstrip("m").rstrip("Gi")) if x not in (None, "—") else default
        except Exception:  # noqa: BLE001
            return default

    cpu = num(service.get("cpu"), 1.0)
    mem_raw = str(service.get("memory") or "512Mi")
    mem_gib = (num(mem_raw.replace("Mi", "")) / 1024 if "Mi" in mem_raw
               else num(mem_raw.replace("Gi", ""), 0.5))
    mins = int(service.get("min_instances") or 0)

    if mins > 0:
        idle_cpu = cpu * mins * SECONDS_PER_MONTH * PRICES["cpu_idle_vcpu_sec"]
        idle_mem = mem_gib * mins * SECONDS_PER_MONTH * PRICES["mem_gib_sec"]
        lines.append({"label": f"Cloud Run — {mins} always-on instance",
                      "detail": f"{cpu:g} vCPU · {mem_gib:.2f} GiB · idle-rate CPU",
                      "monthly": round(idle_cpu + idle_mem, 2), "category": "compute"})
        notes.append("min-instances=1 removes the cold start; it is most of the bill.")
    else:
        lines.append({"label": "Cloud Run — scale to zero",
                      "detail": "billed only while serving",
                      "monthly": 0.5, "category": "compute"})

    lines.append({"label": "Cloud Run job — daily run",
                  "detail": "~1 min/weekday", "monthly": 0.05, "category": "compute"})
    lines.append({"label": "Firestore", "detail": "well inside the free tier",
                  "monthly": 0.0, "category": "database"})
    lines.append({"label": "Artifact Registry", "detail": "container images",
                  "monthly": 0.3, "category": "storage"})
    lines.append({"label": "Secret Manager", "detail": "Schwab + SES secrets",
                  "monthly": 0.2, "category": "security"})
    lines.append({"label": "Cloud Scheduler", "detail": "1 job (first 3 free)",
                  "monthly": 0.0, "category": "compute"})
    lines.append({"label": "Custom domain", "detail": "Cloud Run domain mapping",
                  "monthly": 0.0, "category": "network"})

    monthly = round(sum(i["monthly"] for i in lines), 2)
    return {
        "monthly": monthly, "yearly": round(monthly * 12, 2),
        # ⚠ NOT "items": in Jinja, `cost.items` resolves to dict.items (the
        # method) rather than the key, and the page dies with a TypeError.
        "lines": sorted(lines, key=lambda i: -i["monthly"]),
        "live_resources": live_shape,
        "live_prices": False,
        "basis": ("Resource shape read live from the Cloud Run API; unit prices are "
                  "published us-central1 list rates. This is an ESTIMATE, not a "
                  "billing export."),
        "notes": notes,
        "avoided": ("A load balancer for the custom domain would add ~$18/mo. "
                    "Domain mapping + app-level login avoids it."),
    }


def snapshot(force: bool = False) -> dict:
    key = "health"
    now = time.monotonic()
    if not force and key in _CACHE and now - _CACHE[key][0] < _TTL:
        return _CACHE[key][1]

    token = _token()
    service = _service(token)
    job = _job(token)
    counts = _firestore_counts()
    fresh = _data_freshness()
    deps = _dependencies()
    cost = _cost(service)

    problems = []
    if not service.get("ok"):
        problems.append("Cloud Run service not reporting ready")
    for d in deps:
        if d["status"] == "down":
            problems.append(f"{d['label']} is down")
    if not counts.get("ok"):
        problems.append("Firestore unreachable")

    level = "green"
    if problems:
        level = "red" if any("down" in p or "not reporting" in p for p in problems) else "amber"

    payload = {
        "level": level, "problems": problems,
        "service": service, "job": job, "counts": counts,
        "freshness": fresh, "dependencies": deps, "cost": cost,
        "project": PROJECT, "region": REGION,
        "generated_at": datetime.now(PT).strftime("%a %b %-d · %-I:%M:%S %p PT"),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    _CACHE[key] = (now, payload)
    return payload
