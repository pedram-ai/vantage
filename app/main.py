"""Vantage web app. Auth is enforced upstream by Identity-Aware Proxy;
the app additionally checks the IAP-asserted email against the allowlist."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from core import store
from core.run_builder import build_run

ALLOWED = {e.strip().lower() for e in
           os.environ.get("VANTAGE_ALLOWED_EMAILS", "pedram@patexia.com").split(",")}

app = FastAPI(title="Vantage")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def check_user(request: Request) -> str:
    email = request.headers.get("x-goog-authenticated-user-email", "")
    email = email.split(":")[-1].lower()
    # Local dev / direct invoker calls (Cloud Run auth) have no IAP header.
    if email and email not in ALLOWED:
        raise HTTPException(403, f"{email} is not allowed")
    return email or "local"


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def today(request: Request):
    check_user(request)
    run = build_run(persist=False)
    return templates.TemplateResponse(request, "today.html", {"run": run, "page": "today"})


@app.get("/api/price")
def api_price(request: Request):
    check_user(request)
    from core.bars import last_price
    from core.contracts import active_es_contract, yahoo_symbol
    from datetime import date
    contract = store.get_settings().get("contract_override") or active_es_contract(date.today())
    return JSONResponse({
        "es": last_price(yahoo_symbol(contract)),
        "spy": last_price("SPY"),
    })


@app.get("/levels", response_class=HTMLResponse)
def levels_page(request: Request):
    check_user(request)
    rows = store.recent_levels()
    return templates.TemplateResponse(request, "levels.html", {"rows": rows, "page": "levels"})


@app.post("/levels")
def add_level(request: Request,
              author: str = Form(...), session_date: str = Form(...),
              kind: str = Form(...), price: float = Form(...),
              label: str = Form(""), direction: str = Form(""),
              source_url: str = Form(""), note: str = Form("")):
    check_user(request)
    store.add_level({
        "author": author, "session_date": session_date, "kind": kind,
        "price": price, "label": label, "direction": direction,
        "source_url": source_url, "note": note,
    })
    return RedirectResponse("/levels", status_code=303)


@app.post("/levels/{level_id}/delete")
def del_level(request: Request, level_id: str):
    check_user(request)
    store.delete_level(level_id)
    return RedirectResponse("/levels", status_code=303)


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    check_user(request)
    runs = store.list_runs()
    return templates.TemplateResponse(request, "history.html", {"runs": runs, "page": "history"})


@app.get("/history/{run_id}", response_class=HTMLResponse)
def history_run(request: Request, run_id: str):
    check_user(request)
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "run_detail.html", {"run": run, "page": "history"})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    check_user(request)
    s = store.get_settings()
    return templates.TemplateResponse(request, "settings.html", {"s": s, "page": "settings"})


@app.post("/settings")
def save_settings(request: Request, contract_override: str = Form("")):
    check_user(request)
    store.save_settings({"contract_override": contract_override.strip().upper() or None})
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/events")
def add_event(request: Request, date: str = Form(...), kind: str = Form(...), text: str = Form("")):
    check_user(request)
    from core.calendar_rules import RULES
    store.add_event({"date": date, "kind": kind, "text": text or RULES.get(kind, kind)})
    return RedirectResponse("/settings", status_code=303)


@app.post("/jobs/daily-run")
def daily_run_endpoint(request: Request):
    # Reached only by IAP-allowed users or authenticated invokers.
    check_user(request)
    run = build_run(persist=True)
    return {"run_id": run.get("run_id"), "verdict": run.get("verdict"), "errors": run["errors"]}
