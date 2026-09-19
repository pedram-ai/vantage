"""Vantage web app.

Auth is enforced upstream by Identity-Aware Proxy; the app additionally checks
the IAP-asserted email against the allowlist.

Navigation: Today · Markets · Positions · Performance · Settings.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from core import portfolio, store, watchlists
from core.charts import TIMEFRAMES
from core.glossary import GLOSSARY
from core.portfolio import PERIODS
from core.quotes_batch import quotes_for
from core.symbol_view import build_symbol, build_today

ALLOWED = {e.strip().lower() for e in
           os.environ.get("VANTAGE_ALLOWED_EMAILS", "pedram@patexia.com").split(",")}

app = FastAPI(title="Vantage")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["GLOSSARY"] = GLOSSARY
templates.env.globals["TIMEFRAMES"] = TIMEFRAMES
templates.env.globals["PERIODS"] = PERIODS


def check_user(request: Request) -> str:
    email = request.headers.get("x-goog-authenticated-user-email", "")
    email = email.split(":")[-1].lower()
    if email and email not in ALLOWED:
        raise HTTPException(403, f"{email} is not allowed")
    return email or "local"


def _tape() -> list[dict]:
    """The three-symbol strip in the header."""
    try:
        syms = watchlists.all_tracked_symbols()[:3]
        b = quotes_for(syms)
        return [{"symbol": s, "price": b["quotes"].get(s, {}).get("price")} for s in syms]
    except Exception:  # noqa: BLE001
        return []


@app.get("/healthz")
def healthz():
    return {"ok": True}


# --- Today ------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def today(request: Request, list: str = "", refresh: int = 0):
    check_user(request)
    watchlists.ensure_seed()
    lists = watchlists.all_lists()
    current = watchlists.get(list) if list else (lists[0] if lists else None)
    symbols = current.get("symbols", []) if current else []
    brief = build_today(symbols, fresh=bool(refresh))
    batch = quotes_for(symbols, fresh=bool(refresh))
    return templates.TemplateResponse(request, "today.html", {
        "brief": brief, "page": "today", "tape": _tape(),
        "list_id": current.get("id") if current else None,
        "list_name": current.get("name") if current else "no list",
        "degraded": batch.get("degraded"), "cap": batch.get("cap"),
    })


# --- Markets / watchlists ---------------------------------------------------

@app.get("/markets", response_class=HTMLResponse)
def markets(request: Request, list: str = "", refresh: int = 0):
    check_user(request)
    watchlists.ensure_seed()
    lists = watchlists.all_lists()
    autolists = watchlists.auto_lists()
    current = watchlists.get(list) if list else None
    if current is None:
        current = lists[0] if lists else {"id": "", "name": "—", "symbols": [], "auto": True}
    batch = quotes_for(current.get("symbols", []), fresh=bool(refresh))
    return templates.TemplateResponse(request, "markets.html", {
        "lists": lists, "autolists": autolists, "current": current,
        "batch": batch, "page": "markets", "tape": _tape(),
    })


@app.post("/watchlists")
def new_list(request: Request, name: str = Form(...)):
    check_user(request)
    lid = watchlists.create(name)
    return RedirectResponse(f"/markets?list={lid}", status_code=303)


@app.post("/watchlists/{list_id}/delete")
def del_list(request: Request, list_id: str):
    check_user(request)
    watchlists.delete(list_id)
    return RedirectResponse("/markets", status_code=303)


@app.post("/watchlists/{list_id}/symbols")
def add_symbol(request: Request, list_id: str, symbol: str = Form(...)):
    check_user(request)
    watchlists.add_symbol(list_id, symbol)
    return RedirectResponse(f"/markets?list={list_id}", status_code=303)


@app.post("/watchlists/{list_id}/symbols/{symbol}/delete")
def rm_symbol(request: Request, list_id: str, symbol: str):
    check_user(request)
    watchlists.remove_symbol(list_id, symbol)
    return RedirectResponse(f"/markets?list={list_id}", status_code=303)


@app.get("/symbol/{symbol}", response_class=HTMLResponse)
def symbol_page(request: Request, symbol: str, tf: str = "1d",
                refresh: int = 0, list: str = ""):
    check_user(request)
    tf = tf if tf in TIMEFRAMES else "1d"
    v = build_symbol(symbol, tf=tf, with_chart=True, fresh=bool(refresh))
    return templates.TemplateResponse(request, "symbol.html", {
        "v": v, "tf": tf, "page": "markets", "tape": _tape(), "list_id": list,
    })


@app.get("/api/quotes")
def api_quotes(request: Request, symbols: str = ""):
    check_user(request)
    syms = [s for s in symbols.split(",") if s.strip()]
    return JSONResponse(quotes_for(syms, fresh=True))


# --- Portfolio --------------------------------------------------------------

@app.get("/positions", response_class=HTMLResponse)
def positions_page(request: Request):
    check_user(request)
    conn = portfolio.is_connected()
    positions = portfolio.open_positions()
    totals = {
        "open_pnl": sum(float(p.get("open_pnl", 0) or 0) for p in positions),
        "cost": sum(float(p.get("cost", 0) or 0) * float(p.get("qty", 0) or 0)
                    for p in positions),
        "expiring": sum(1 for p in positions if p.get("expiring_soon")),
    }
    return templates.TemplateResponse(request, "positions.html", {
        "conn": conn, "positions": positions, "totals": totals,
        "page": "positions", "tape": _tape(),
    })


@app.get("/performance", response_class=HTMLResponse)
def performance_page(request: Request, period: str = "month"):
    check_user(request)
    perf = portfolio.performance(period)
    return templates.TemplateResponse(request, "performance.html", {
        "perf": perf, "page": "performance", "tape": _tape(),
    })


# --- Levels (ES author levels) ----------------------------------------------

@app.get("/levels", response_class=HTMLResponse)
def levels_page(request: Request):
    check_user(request)
    return templates.TemplateResponse(request, "levels.html", {
        "rows": store.recent_levels(), "page": "settings", "tape": _tape(),
    })


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


# --- Settings ---------------------------------------------------------------

def _redirect_uri(request: Request) -> str:
    base = os.environ.get("VANTAGE_BASE_URL") or str(request.base_url).rstrip("/")
    return f"{base}/schwab/callback"


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    check_user(request)
    from core import schwab
    return templates.TemplateResponse(request, "settings.html", {
        "s": store.get_settings(), "page": "settings", "tape": _tape(),
        "schwab": schwab.status(), "conn": portfolio.is_connected(),
        "redirect_uri": _redirect_uri(request),
    })


@app.post("/settings")
def save_settings(request: Request, contract_override: str = Form("")):
    check_user(request)
    store.save_settings({"contract_override": contract_override.strip().upper() or None})
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/schwab")
def save_schwab_creds(request: Request, app_key: str = Form(""), app_secret: str = Form("")):
    """Pedram's own developer.schwab.com credentials go straight into Secret
    Manager and are never echoed back to the page."""
    check_user(request)
    from core import schwab
    if app_key.strip():
        schwab.write_secret(schwab.SECRET_KEY, app_key.strip())
    if app_secret.strip():
        schwab.write_secret(schwab.SECRET_SECRET, app_secret.strip())
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/events")
def add_event(request: Request, date: str = Form(...), kind: str = Form(...), text: str = Form("")):
    check_user(request)
    from core.calendar_rules import RULES
    store.add_event({"date": date, "kind": kind, "text": text or RULES.get(kind, kind)})
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


# --- archive / email --------------------------------------------------------

@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    check_user(request)
    return templates.TemplateResponse(request, "history.html", {
        "runs": store.list_runs(), "page": "today", "tape": _tape(),
    })


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
    from core.run_builder import build_run
    _, html = render_email(build_run(persist=False))
    return HTMLResponse(html)
