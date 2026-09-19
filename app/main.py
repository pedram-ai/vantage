"""Vantage web app. Auth is enforced upstream by Identity-Aware Proxy;
the app additionally checks the IAP-asserted email against the allowlist."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from core import store
from core.charts import TIMEFRAMES
from core.glossary import GLOSSARY
from core.run_builder import build_run

ALLOWED = {e.strip().lower() for e in
           os.environ.get("VANTAGE_ALLOWED_EMAILS", "pedram@patexia.com").split(",")}

app = FastAPI(title="Vantage")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["GLOSSARY"] = GLOSSARY
templates.env.globals["TIMEFRAMES"] = TIMEFRAMES


def check_user(request: Request) -> str:
    email = request.headers.get("x-goog-authenticated-user-email", "")
    email = email.split(":")[-1].lower()
    if email and email not in ALLOWED:
        raise HTTPException(403, f"{email} is not allowed")
    return email or "local"


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def realtime(request: Request, tf: str = "15m"):
    check_user(request)
    tf = tf if tf in TIMEFRAMES else "15m"
    run = build_run(persist=False, tf=tf, with_charts=True, fresh=True)
    return templates.TemplateResponse(
        request, "today.html", {"run": run, "page": "today", "tf": tf})


@app.get("/api/price")
def api_price(request: Request):
    check_user(request)
    from core.bars import last_price
    from core.contracts import active_es_contract, yahoo_symbol
    contract = store.get_settings().get("contract_override") or active_es_contract(date.today())
    es = last_price(yahoo_symbol(contract))
    spy = last_price("SPY")
    return JSONResponse({
        "es": {**es, "price": round(es["price"], 2) if es.get("price") else None},
        "spy": {**spy, "price": round(spy["price"], 2) if spy.get("price") else None},
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


@app.get("/history/{run_id}/email", response_class=HTMLResponse)
def history_email(request: Request, run_id: str):
    check_user(request)
    run = store.get_run(run_id)
    if not run or not run.get("email_html"):
        raise HTTPException(404, "no email archived for this run")
    return HTMLResponse(run["email_html"])


@app.get("/email-preview", response_class=HTMLResponse)
def email_preview(request: Request):
    check_user(request)
    from core.email_render import render_email
    run = build_run(persist=False)
    _, html = render_email(run)
    return HTMLResponse(html)


def _redirect_uri(request: Request) -> str:
    """Schwab callback. Must match the app's registered callback exactly."""
    base = os.environ.get("VANTAGE_BASE_URL") or str(request.base_url).rstrip("/")
    return f"{base}/schwab/callback"


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    check_user(request)
    from core import schwab
    s = store.get_settings()
    return templates.TemplateResponse(request, "settings.html", {
        "s": s, "page": "settings",
        "schwab": schwab.status(),
        "redirect_uri": _redirect_uri(request),
    })


@app.post("/settings/schwab")
def save_schwab_creds(request: Request, app_key: str = Form(""), app_secret: str = Form("")):
    """Pedram pastes his own developer.schwab.com app credentials; they go
    straight into Secret Manager and are never echoed back to the page."""
    check_user(request)
    from core import schwab
    if app_key.strip():
        schwab.write_secret(schwab.SECRET_KEY, app_key.strip())
    if app_secret.strip():
        schwab.write_secret(schwab.SECRET_SECRET, app_secret.strip())
    return RedirectResponse("/settings", status_code=303)


@app.get("/schwab/connect")
def schwab_connect(request: Request):
    check_user(request)
    from core import schwab
    url = schwab.authorize_url(_redirect_uri(request))
    if not url:
        raise HTTPException(400, "Save your Schwab app key and secret first")
    return RedirectResponse(url, status_code=303)


@app.get("/schwab/callback", response_class=HTMLResponse)
def schwab_callback(request: Request, code: str = "", error: str = ""):
    check_user(request)
    from core import schwab
    if error or not code:
        return HTMLResponse(
            f"<p>Schwab returned: {error or 'no code'}</p><p><a href='/settings'>back</a></p>",
            status_code=400)
    res = schwab.exchange_code(code, _redirect_uri(request))
    if not res.get("ok"):
        return HTMLResponse(
            f"<p>Could not complete the Schwab link: {res.get('error')}</p>"
            f"<p><a href='/settings'>back</a></p>", status_code=400)
    return RedirectResponse("/settings", status_code=303)


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
