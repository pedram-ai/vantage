"""Argent Ridge web app.

Auth is enforced upstream by Identity-Aware Proxy; the app additionally checks
the IAP-asserted email against the allowlist.

Navigation: Today · Markets · Positions · Performance · Settings.
"""

from __future__ import annotations

import os
from urllib.parse import quote_plus
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


@app.on_event("startup")
def _warm_cache() -> None:
    """Prime the portfolio read cache off the request path.

    ⚠ With min-instances=1 the container outlives requests, so the FIRST page
    load would otherwise pay the whole Firestore read (~3.5 s measured on 771
    trades) while every later one is ~2 ms. Warming here moves that cost to
    deploy time, where nobody is waiting.

    Fail-soft on purpose: if Firestore is unreachable at boot the app must
    still start and render its "not connected" state, not crash-loop.
    """
    import threading

    def go():
        # Each in its own try: a failing warm-up must never stop the others,
        # and none of them may stop the app from starting.
        for warm in (
            lambda: __import__("core.portfolio", fromlist=["x"]).performance("all"),
            lambda: __import__("core.store", fromlist=["x"]).get_settings(),
            lambda: __import__("core.watchlists", fromlist=["x"]).all_lists(),
            # ⚠ System health probes Yahoo, Schwab and Firestore — 3.6 s on a
            # cold read. Priming it here means the first admin page load is
            # warm rather than the one that pays for it.
            lambda: __import__("core.sysheath", fromlist=["x"]).snapshot(),
        ):
            try:
                warm()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=go, daemon=True).start()



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


def _pt_date(iso):
    """UTC ISO -> Pacific, for template use. Never raises.

    ⛔ Every clock time on screen is Pacific; UTC is storage only."""
    from core import research
    return research.pt_date(iso)


templates.env.filters["money"] = _money
templates.env.filters["pt_date"] = _pt_date


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
    from core import brand, version
    kw.setdefault("me", current_user(request))
    # Inlined, because IAP 302s every asset path — see core/brand.py.
    kw.setdefault("icons", brand.data_uris())
    # Read once at import inside core.version; this is a dict lookup, not I/O.
    kw.setdefault("build_footer", version.footer())
    return kw


_TAPE_CACHE: tuple[float, list] | None = None
_TAPE_TTL = 45.0


def _tape() -> list[dict]:
    """The three-symbol strip in the header.

    ⚠ MEASURED 2026-09-20 at 156 ms, ON EVERY PAGE — it is a live Yahoo fetch
    and it renders in the header of every template, so it was charged to every
    click including ones that have nothing to do with quotes. Yahoo already
    rate-limits this app (see core/quotes_batch.py), so hitting it once per
    navigation was also the wrong thing to do to the source.

    45 s is short enough that the strip is never visibly stale and long enough
    that a burst of navigation costs one fetch.
    """
    global _TAPE_CACHE
    import time as _t
    now = _t.monotonic()
    if _TAPE_CACHE and now - _TAPE_CACHE[0] < _TAPE_TTL:
        return _TAPE_CACHE[1]
    try:
        syms = watchlists.all_tracked_symbols()[:3]
        b = quotes_for(syms)
        out = [{"symbol": s, "price": b["quotes"].get(s, {}).get("price")} for s in syms]
    except Exception:  # noqa: BLE001
        out = []
    _TAPE_CACHE = (now, out)
    return out


# --- brand assets -----------------------------------------------------------
# Generated from one geometry table in core/brand.py, so the favicon and the
# header logo cannot drift apart. Cached hard: the mark changes with a deploy.

_BRAND_CACHE: dict = {}


def _brand_asset(kind: str):
    from fastapi.responses import Response
    from core import brand
    if kind not in _BRAND_CACHE:
        if kind == "svg":
            _BRAND_CACHE[kind] = (brand.svg(64).encode(), "image/svg+xml")
        elif kind == "ico":
            _BRAND_CACHE[kind] = (brand.ico(32), "image/x-icon")
        elif kind == "apple":
            _BRAND_CACHE[kind] = (brand.png(180), "image/png")
        elif kind == "png32":
            _BRAND_CACHE[kind] = (brand.png(32), "image/png")
    data, mime = _BRAND_CACHE[kind]
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/favicon.svg")
def favicon_svg():
    return _brand_asset("svg")


@app.get("/favicon.ico")
def favicon_ico():
    return _brand_asset("ico")


@app.get("/icon-32.png")
def icon_32():
    return _brand_asset("png32")


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
def apple_icon():
    return _brand_asset("apple")


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


@app.get("/api/symbols")
def api_symbols(request: Request, q: str = ""):
    """Autocomplete for the Add-symbol box. Signed-in only — it is a proxy to
    an external service and must not be open to the internet."""
    check_user(request)
    from core import symbol_search
    return {"q": q, "results": symbol_search.search(q)}


# --- Research ---------------------------------------------------------------

@app.get("/research", response_class=HTMLResponse)
def research_page(request: Request, tab: str = "articles", source: str = "",
                  page: int = 1, err: str = "", pasted: str = "",
                  was: int = 0, now: int = 0, days: int = 0):
    check_user(request)
    from core import convert, llm, read_viz, research
    research.seed_sources()
    feed = (research.list_articles(source=source, preview=True, page=page, days=days)
            if tab == "articles" else {"rows": [], "pages": 1, "page": 1, "total": 0})
    live = convert.ratios()
    spy_now = live.get("spy")
    # ⛔ All arithmetic happens here, never in the template. Each article is
    # converted with ITS OWN publication-date ratio, so two cards on one page
    # legitimately use different numbers.
    for a in feed["rows"]:
        r = a.get("read") or {}
        if r.get("spy_ref") is None:
            continue
        unit = r.get("quoted_in") or "SPY"
        ar = convert.ratio_on((a.get("published_at") or "")[:10])
        a["viz"] = {
            "h24": read_viz.strip(r.get("h24"), r["spy_ref"], spy_now,
                                  "next 24 hours", unit, ar),
            "week": read_viz.strip(r.get("week"), r["spy_ref"], spy_now,
                                   "this week", unit, ar),
            "bias": read_viz.bias_bar(r.get("bias"), r.get("conviction")),
            "unit": unit,
            "ratio": round(ar.get("es_spy", 0), 4),
            "asof": ar.get("asof"),
            "levels": [
                {"label": l.get("label", ""),
                 "px": read_viz._px(l["spy"], unit, ar)}
                for l in (r.get("levels") or [])[:8]],
            "trig": {
                k: {"px": (read_viz._px(leg[f"spy_{k2}"], unit, ar)
                           if leg.get(f"spy_{k2}") is not None else None)
                    for k2 in ("activation",)}
                for k, leg in (("h24", r.get("h24") or {}), ("week", r.get("week") or {}))},
        }
    return templates.TemplateResponse(request, "research.html", _ctx(request, **{
        "page": "research", "tape": _tape(), "tab": tab,
        "articles": feed["rows"], "feed": feed, "sources": research.all_sources(),
        "source": source, "counts": research.counts(),
        "paywalled": ([a for a in research._list_all() if a.get("paywalled")]
                      if tab == "paste" else []),
        "job": research.job_status(), "err": err, "pasted": pasted,
        "was": was, "now": now, "days": days, "spy_now": spy_now,
        "timeline": research.timeline(source=source, days=days) if tab == "articles" else [],
        "outlook": (research.outlook(14, spy_now) if tab == "articles" else None),
        "ladder": (read_viz.ladder(research.outlook(14, spy_now).get("levels_all", []),
                                   spy_now, "SPY", live)
                   if tab == "articles" else ""),
        "live_ratio": live,
        "llm": llm.status(),
    }))


@app.get("/research/{aid}", response_class=HTMLResponse)
def research_article(request: Request, aid: str):
    """One article in full, with its read. The list carries a preview only."""
    check_user(request)
    from core import research
    a = research.get_article(aid)
    if not a:
        raise HTTPException(status_code=404, detail="No such article")
    return templates.TemplateResponse(request, "article.html", _ctx(request, **{
        "a": a, "page": "research", "tape": _tape(),
    }))


@app.post("/research/refresh")
def research_refresh(request: Request):
    check_user(request)
    from core import research
    research.refresh()
    return RedirectResponse("/research", status_code=303)


@app.post("/research/read")
def research_read(request: Request, limit: int = Form(8)):
    """Kick off the background pass. ⛔ Never blocks — a read is ~18 s."""
    check_user(request)
    from core import research
    research.start_summarise(int(limit))
    return RedirectResponse("/research?reading=1", status_code=303)


@app.get("/api/research/job")
def research_job(request: Request):
    check_user(request)
    from core import research
    return research.job_status()


@app.post("/research/paste")
def research_paste(request: Request, title: str = Form(""), body: str = Form(""),
                   url: str = Form(""), source_label: str = Form("")):
    """Save a pasted post. Substack's paid text cannot be fetched, so this is
    the route for it — and it upgrades a teaser we already hold in place."""
    check_user(request)
    from core import research
    try:
        res = research.add_manual(title, body, url, source_label)
    except ValueError as e:
        return RedirectResponse(f"/research?tab=paste&err={quote_plus(str(e))}",
                                status_code=303)
    # Read it straight away — one article is ~18 s, in the background.
    research.start_summarise(1)
    verb = "upgraded" if res["upgraded"] else "saved"
    return RedirectResponse(
        f"/research?pasted={verb}&was={res['was']}&now={res['now']}", status_code=303)


@app.post("/research/sources")
def research_add_source(request: Request, handle: str = Form(""),
                        label: str = Form(""), kind: str = Form("substack")):
    require_owner(request)
    from core import research
    try:
        research.add_source(kind, handle, label)
    except ValueError:
        pass
    return RedirectResponse("/research?tab=sources", status_code=303)


@app.post("/research/sources/{sid}/delete")
def research_del_source(request: Request, sid: str):
    require_owner(request)
    from core import research
    research.delete_source(sid)
    return RedirectResponse("/research?tab=sources", status_code=303)


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
def performance_page(request: Request, period: str = "month", page: int = 1):
    check_user(request)
    from core import perf_charts
    perf = portfolio.performance(period, page=page)
    # ⚠ Charts must use the FULL period, never the current page — paginating
    # the table must not silently reshape the charts beside it.
    trades = perf.get("all_trades") or []
    charts = {
        "equity": perf_charts.equity_curve(trades),
        "monthly": perf_charts.monthly_bars(trades),
        "by_symbol": perf_charts.category_bars(perf.get("by_symbol") or []),
        "by_zone": perf_charts.category_bars(perf.get("by_zone") or []),
        "fees": perf_charts.gross_vs_fees(perf.get("realized", 0), perf.get("fees", 0)),
    }
    return templates.TemplateResponse(request, "performance.html", _ctx(request, **{
        "perf": perf, "charts": charts, "page": "performance", "tape": _tape(),
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


@app.get("/settings")
def settings_moved():
    """Settings became Admin -> Data sources. A 301 keeps every old bookmark,
    link and browser autocomplete working instead of turning them into 404s."""
    return RedirectResponse("/admin/sources", status_code=301)


@app.get("/admin/sources", response_class=HTMLResponse)
def sources_page(request: Request):
    require_owner(request)
    from core import schwab
    return templates.TemplateResponse(request, "sources.html", _ctx(request, **{
        "s": store.get_settings(), "page": "admin", "admin_page": "/admin/sources",
        "tape": _tape(), "schwab": schwab.status(), "conn": portfolio.is_connected(),
        "redirect_uri": _redirect_uri(request),
    }))


@app.post("/admin/sources")
def save_settings(request: Request, contract_override: str = Form("")):
    require_owner(request)
    store.save_settings({"contract_override": contract_override.strip().upper() or None})
    return RedirectResponse("/admin/sources", status_code=303)


@app.post("/admin/sources/schwab")
def save_schwab_creds(request: Request, app_key: str = Form(""), app_secret: str = Form("")):
    """Pedram's own developer.schwab.com credentials go straight into Secret
    Manager and are never echoed back to the page."""
    check_user(request)
    from core import schwab
    if app_key.strip():
        schwab.write_secret(schwab.SECRET_KEY, app_key.strip())
    if app_secret.strip():
        schwab.write_secret(schwab.SECRET_SECRET, app_secret.strip())
    return RedirectResponse("/admin/sources", status_code=303)


@app.post("/admin/sources/events")
def add_event(request: Request, date: str = Form(...), kind: str = Form(...), text: str = Form("")):
    check_user(request)
    from core.calendar_rules import RULES
    store.add_event({"date": date, "kind": kind, "text": text or RULES.get(kind, kind)})
    return RedirectResponse("/admin/sources", status_code=303)


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
            f"<p>Schwab returned: {error or 'no code'}</p><p><a href='/admin/sources'>back</a></p>",
            status_code=400)
    res = schwab.exchange_code(code, _redirect_uri(request))
    if not res.get("ok"):
        return HTMLResponse(
            f"<p>Could not complete the Schwab link: {res.get('error')}</p>"
            f"<p><a href='/admin/sources'>back</a></p>", status_code=400)
    return RedirectResponse("/admin/sources", status_code=303)


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
            _ctx(request, error=str(e), email=email), status_code=401)
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


@app.get("/forgot", response_class=HTMLResponse)
def forgot_form(request: Request):
    return templates.TemplateResponse(request, "forgot.html", _ctx(request, sent=False, error=None))


@app.post("/forgot", response_class=HTMLResponse)
def do_forgot(request: Request, email: str = Form(...)):
    """⛔ Always renders the same confirmation. Telling the visitor whether an
    address has an account turns this into a user-enumeration oracle."""
    try:
        auth.request_reset(email)
    except Exception:  # noqa: BLE001
        pass
    return templates.TemplateResponse(request, "forgot.html", _ctx(request, sent=True, error=None))


@app.get("/setup/{token}", response_class=HTMLResponse)
def setup_form(request: Request, token: str):
    u = auth.find_setup(token)
    return templates.TemplateResponse(request, "setup.html", _ctx(request, **{"user": u, "token": token, "error": None}))


@app.post("/setup/{token}", response_class=HTMLResponse)
def do_setup(request: Request, token: str,
             password: str = Form(...), confirm: str = Form(...)):
    u = auth.find_setup(token)
    if password != confirm:
        return templates.TemplateResponse(
            request, "setup.html",
            _ctx(request, user=u, token=token, error="The two passwords do not match."),
            status_code=400)
    try:
        auth.complete_setup(token, password)
    except ValueError as e:
        return templates.TemplateResponse(
            request, "setup.html",
            _ctx(request, user=u, token=token, error=str(e)), status_code=400)
    return RedirectResponse("/login", status_code=303)


# --- admin console (owner only) --------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    require_owner(request)
    from core import auth as _a, docs_store, sysheath
    h = sysheath.snapshot()
    b = docs_store.build_info()
    newest = b["commits"][0]["date"][:10] if b.get("commits") else None
    return templates.TemplateResponse(request, "admin.html", _ctx(request, **{
        "page": "admin", "tape": _tape(), "build": b,
        "commits": len(b.get("commits", [])), "level": h["level"],
        "cost": "$%.2f" % h["cost"]["monthly"],
        "docs": len(docs_store.list_docs()), "users": _a.user_count(),
        "latest_change": newest, "admin_page": "overview",
    }))


@app.get("/admin/health", response_class=HTMLResponse)
def admin_health(request: Request, force: int = 0):
    require_owner(request)
    from core import sysheath
    return templates.TemplateResponse(request, "health.html", _ctx(request, **{
        "h": sysheath.snapshot(force=bool(force)), "page": "admin", "tape": _tape(),
        "admin_page": "/admin/health",
    }))


@app.get("/admin/docs", response_class=HTMLResponse)
def admin_docs(request: Request, doc: str = "", q: str = ""):
    require_owner(request)
    from core import docs_store
    all_docs = docs_store.list_docs()
    # ⚠ Search filters the INDEX, never the document on screen. Narrowing the
    # index while still showing the open document is what lets you search for a
    # term, see which pages carry it, and keep reading the one you were on.
    shown = docs_store.search(all_docs, q) if q else all_docs
    d = docs_store.get_doc(doc) if doc else None
    return templates.TemplateResponse(request, "docs.html", _ctx(request, **{
        "groups": docs_store.grouped_docs(shown), "doc": d, "q": q,
        "total_docs": len(all_docs),
        "rendered": docs_store.render_markdown(d["markdown"]) if d else "",
        "build": docs_store.build_info(), "page": "admin", "tape": _tape(),
        "admin_page": "/admin/docs",
    }))


# --- user administration (owner only; there is NO sign-up route) ------------

@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, saved: str = "", pw: str = "", err: str = ""):
    me = check_user(request)
    return templates.TemplateResponse(request, "profile.html", _ctx(request, **{
        "u": me, "page": "profile", "tape": _tape(),
        "sessions": auth.active_sessions(me["email"]),
        "saved": saved == "1", "pw": pw == "1", "err": err,
        "csrf": auth.csrf_for(request.cookies.get(auth.SESSION_COOKIE)),
    }))


@app.post("/profile")
def save_profile(request: Request, name: str = Form(""), csrf: str = Form("")):
    me = check_user(request)
    tok = request.cookies.get(auth.SESSION_COOKIE)
    if not auth.csrf_ok(tok, csrf):
        return RedirectResponse("/profile?err=Session+expired.", status_code=303)
    try:
        auth.update_profile(me["email"], name)
    except ValueError as e:
        return RedirectResponse(f"/profile?err={quote_plus(str(e))}", status_code=303)
    return RedirectResponse("/profile?saved=1", status_code=303)


@app.post("/profile/password")
def change_password(request: Request, current: str = Form(""), new: str = Form(""),
                    confirm: str = Form(""), csrf: str = Form("")):
    """⛔ Every other session is revoked inside auth.change_password, INCLUDING
    this one — so a new cookie is minted here. Without it you change your
    password and are immediately signed out, which reads as a failure."""
    me = check_user(request)
    tok = request.cookies.get(auth.SESSION_COOKIE)
    if not auth.csrf_ok(tok, csrf):
        return RedirectResponse("/profile?err=Session+expired.", status_code=303)
    if new != confirm:
        return RedirectResponse("/profile?err=The+two+new+passwords+do+not+match.",
                                status_code=303)
    try:
        auth.change_password(me["email"], current, new)
    except ValueError as e:
        return RedirectResponse(f"/profile?err={quote_plus(str(e))}", status_code=303)
    fresh = auth.login(me["email"], new, ip=(request.client.host if request.client else ""),
                       ua=request.headers.get("user-agent", ""))
    resp = RedirectResponse("/profile?pw=1", status_code=303)
    resp.set_cookie(auth.SESSION_COOKIE, fresh, **_cookie_kwargs(request))
    return resp


@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request, created: str = "", link: str = "", mailed: str = ""):
    me = require_owner(request)
    return templates.TemplateResponse(request, "users.html", _ctx(request, **{
        "me": me, "users": auth.list_users(), "page": "admin", "admin_page": "/users",
        "tape": _tape(), "created": created, "link": link, "mailed": mailed == "1",
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
    mailed = auth.send_invite(res["email"], res["setup_token"])
    base = os.environ.get("VANTAGE_BASE_URL") or str(request.base_url).rstrip("/")
    # The link is still shown, because email can silently fail and an admin
    # who cannot see the link has no way to onboard the person.
    return RedirectResponse(
        f"/users?created={res['email']}&mailed={int(mailed)}"
        f"&link={base}/setup/{res['setup_token']}", status_code=303)


@app.post("/users/{email}/reset")
def reset_user(request: Request, email: str, csrf: str = Form("")):
    require_owner(request)
    if not auth.csrf_ok(request.cookies.get(auth.SESSION_COOKIE), csrf):
        raise HTTPException(400, "Bad CSRF token")
    token = auth.new_setup_token(email)
    mailed = auth.send_invite(email, token)
    base = os.environ.get("VANTAGE_BASE_URL") or str(request.base_url).rstrip("/")
    return RedirectResponse(
        f"/users?created={email}&mailed={int(mailed)}&link={base}/setup/{token}",
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
