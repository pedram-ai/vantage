"""Argent Ridge web app.

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

from core import auth, portfolio, store, watchlists
from core.charts import TIMEFRAMES
from core.glossary import GLOSSARY
from core.portfolio import PERIODS
from core.quotes_batch import quotes_for
from core.symbol_view import build_symbol, build_today

ALLOWED = {e.strip().lower() for e in
           os.environ.get("VANTAGE_ALLOWED_EMAILS", "pedram@patexia.com").split(",")}

app = FastAPI(title="Argent Ridge")



templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["GLOSSARY"] = GLOSSARY
templates.env.globals["TIMEFRAMES"] = TIMEFRAMES
templates.env.globals["PERIODS"] = PERIODS



def _money(value, dp: int = 0, signed: bool = False, symbol: str = "$") -> str:
    """Thousands-separated money WITH its currency symbol.

    Jinja's `format` filter uses %-formatting, which has no comma flag —
    `'%,.0f'|format(x)` raises. Use `x|money`.

    The sign goes OUTSIDE the symbol (-$519,390, not $-519,390) so a loss
    reads at a glance.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    body = f"{symbol}{abs(v):,.{dp}f}"
    if v < 0:
        return f"-{body}"
    return f"+{body}" if signed else body


templates.env.filters["money"] = _money


class NeedsLogin(Exception):
    """Raised instead of returning 403, so the handler can redirect."""


def current_user(request: Request) -> dict | None:
    """Who is making this request, or None.

    ⛔ The IAP header is only believed when IAP actually fronts the app. If the
    service is public and we trusted `x-goog-authenticated-user-email`, anyone
    could set it and walk straight in.
    """
    if auth.iap_mode():
        email = request.headers.get("x-goog-authenticated-user-email", "")
        email = email.split(":")[-1].lower()
        if email:
            if email not in ALLOWED:
                raise HTTPException(403, f"{email} is not allowed")
            u = auth.get_user(email)
            return u or {"email": email, "role": "owner", "name": email}
    return auth.session_user(request.cookies.get(auth.SESSION_COOKIE))


def check_user(request: Request) -> dict:
    u = current_user(request)
    if not u:
        raise NeedsLogin()
    return u


@app.exception_handler(NeedsLogin)
async def _needs_login(request: Request, exc: NeedsLogin):
    nxt = request.url.path
    return RedirectResponse(f"/login?next={nxt}" if nxt != "/" else "/login",
                            status_code=303)


def require_owner(request: Request) -> dict:
    u = check_user(request)
    if u.get("role") != "owner":
        raise HTTPException(403, "Owner access only.")
    return u


def _ctx(request: Request, **kw) -> dict:
    """Common template context — the viewer drives nav visibility."""
    kw.setdefault("me", current_user(request))
    return kw


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
    return templates.TemplateResponse(request, "today.html", _ctx(request, **{
        "brief": brief, "page": "today", "tape": _tape(),
        "list_id": current.get("id") if current else None,
        "list_name": current.get("name") if current else "no list",
        "degraded": batch.get("degraded"), "cap": batch.get("cap"),
    }))


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
    return templates.TemplateResponse(request, "markets.html", _ctx(request, **{
        "lists": lists, "autolists": autolists, "current": current,
        "batch": batch, "page": "markets", "tape": _tape(),
    }))


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
    return templates.TemplateResponse(request, "symbol.html", _ctx(request, **{
        "v": v, "tf": tf, "page": "markets", "tape": _tape(), "list_id": list,
    }))


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
    return templates.TemplateResponse(request, "positions.html", _ctx(request, **{
        "conn": conn, "positions": positions, "totals": totals,
        "page": "positions", "tape": _tape(),
    }))


@app.get("/cash", response_class=HTMLResponse)
def cash_page(request: Request):
    check_user(request)
    return templates.TemplateResponse(request, "cash.html", _ctx(request, **{
        "state": portfolio.account_state(), "flows": portfolio.cash_flows(),
        "page": "performance", "tape": _tape(),
    }))


@app.get("/performance", response_class=HTMLResponse)
def performance_page(request: Request, period: str = "month"):
    check_user(request)
    perf = portfolio.performance(period)
    return templates.TemplateResponse(request, "performance.html", _ctx(request, **{
        "perf": perf, "page": "performance", "tape": _tape(),
    }))


# --- Levels (ES author levels) ----------------------------------------------

@app.get("/levels", response_class=HTMLResponse)
def levels_page(request: Request):
    check_user(request)
    return templates.TemplateResponse(request, "levels.html", _ctx(request, **{
        "rows": store.recent_levels(), "page": "settings", "tape": _tape(),
    }))


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
    return templates.TemplateResponse(request, "settings.html", _ctx(request, **{
        "s": store.get_settings(), "page": "settings", "tape": _tape(),
        "schwab": schwab.status(), "conn": portfolio.is_connected(),
        "redirect_uri": _redirect_uri(request),
    }))


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
    return templates.TemplateResponse(request, "history.html", _ctx(request, **{
        "runs": store.list_runs(), "page": "today", "tape": _tape(),
    }))


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


# --- authentication ---------------------------------------------------------

def _cookie_kwargs(request: Request) -> dict:
    """Secure only over https, so local http dev still works."""
    return {
        "httponly": True,
        "secure": request.url.scheme == "https",
        "samesite": "lax",
        "max_age": auth.SESSION_DAYS * 24 * 3600,
        "path": "/",
    }


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    if current_user(request):
        return RedirectResponse(next or "/", status_code=303)
    return templates.TemplateResponse(request, "login.html", _ctx(request, **{"error": None}))


@app.post("/login")
def do_login(request: Request, email: str = Form(...), password: str = Form(...),
             next: str = Form("/")):
    try:
        token = auth.login(email, password,
                           ip=(request.client.host if request.client else ""),
                           ua=request.headers.get("user-agent", ""))
    except ValueError as e:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": str(e), "email": email}, status_code=401)
    # Only ever redirect somewhere on this site.
    dest = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = RedirectResponse(dest, status_code=303)
    resp.set_cookie(auth.SESSION_COOKIE, token, **_cookie_kwargs(request))
    return resp


@app.post("/logout")
@app.get("/logout")
def do_logout(request: Request):
    auth.logout(request.cookies.get(auth.SESSION_COOKIE))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


@app.get("/setup/{token}", response_class=HTMLResponse)
def setup_form(request: Request, token: str):
    u = auth.find_setup(token)
    return templates.TemplateResponse(request, "setup.html",
                                      {"user": u, "token": token, "error": None})


@app.post("/setup/{token}", response_class=HTMLResponse)
def do_setup(request: Request, token: str,
             password: str = Form(...), confirm: str = Form(...)):
    u = auth.find_setup(token)
    if password != confirm:
        return templates.TemplateResponse(
            request, "setup.html",
            {"user": u, "token": token, "error": "The two passwords do not match."},
            status_code=400)
    try:
        auth.complete_setup(token, password)
    except ValueError as e:
        return templates.TemplateResponse(
            request, "setup.html",
            {"user": u, "token": token, "error": str(e)}, status_code=400)
    return RedirectResponse("/login", status_code=303)


# --- user administration (owner only; there is NO sign-up route) ------------

@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request, created: str = "", link: str = ""):
    me = require_owner(request)
    return templates.TemplateResponse(request, "users.html", _ctx(request, **{
        "me": me, "users": auth.list_users(), "page": "settings",
        "tape": _tape(), "created": created, "link": link,
        "csrf": auth.csrf_for(request.cookies.get(auth.SESSION_COOKIE)),
    }))


@app.post("/users")
def add_user(request: Request, email: str = Form(...), name: str = Form(""),
             role: str = Form("staff"), csrf: str = Form("")):
    require_owner(request)
    if not auth.csrf_ok(request.cookies.get(auth.SESSION_COOKIE), csrf):
        raise HTTPException(400, "Bad CSRF token")
    try:
        res = auth.create_user(email, name, role)
    except ValueError as e:
        return RedirectResponse(f"/users?created={e}", status_code=303)
    base = os.environ.get("VANTAGE_BASE_URL") or str(request.base_url).rstrip("/")
    return RedirectResponse(
        f"/users?created={res['email']}&link={base}/setup/{res['setup_token']}",
        status_code=303)


@app.post("/users/{email}/reset")
def reset_user(request: Request, email: str, csrf: str = Form("")):
    require_owner(request)
    if not auth.csrf_ok(request.cookies.get(auth.SESSION_COOKIE), csrf):
        raise HTTPException(400, "Bad CSRF token")
    token = auth.new_setup_token(email)
    base = os.environ.get("VANTAGE_BASE_URL") or str(request.base_url).rstrip("/")
    return RedirectResponse(f"/users?created={email}&link={base}/setup/{token}",
                            status_code=303)


@app.post("/users/{email}/disable")
def disable_user(request: Request, email: str, csrf: str = Form("")):
    me = require_owner(request)
    if not auth.csrf_ok(request.cookies.get(auth.SESSION_COOKIE), csrf):
        raise HTTPException(400, "Bad CSRF token")
    if auth.normalize_email(email) == me["email"]:
        raise HTTPException(400, "You cannot disable your own account.")
    auth.set_disabled(email, True)
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{email}/enable")
def enable_user(request: Request, email: str, csrf: str = Form("")):
    require_owner(request)
    if not auth.csrf_ok(request.cookies.get(auth.SESSION_COOKIE), csrf):
        raise HTTPException(400, "Bad CSRF token")
    auth.set_disabled(email, False)
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{email}/delete")
def remove_user(request: Request, email: str, csrf: str = Form("")):
    me = require_owner(request)
    if not auth.csrf_ok(request.cookies.get(auth.SESSION_COOKIE), csrf):
        raise HTTPException(400, "Bad CSRF token")
    if auth.normalize_email(email) == me["email"]:
        raise HTTPException(400, "You cannot delete your own account.")
    auth.delete_user(email)
    return RedirectResponse("/users", status_code=303)
