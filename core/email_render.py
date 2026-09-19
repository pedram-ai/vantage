"""Gmail-safe HTML email (handoff §5.1).

Rules enforced here: tables only; bgcolor attributes plus background-color
(never the `background` shorthand); inline styles; entities for arrows
(&#9650; &#9660; &larr; &rarr; only - never &#9664;/&#9654;); no images,
SVG or scripts; max-width 720px. No SPX/strikes/expiries.
"""

from __future__ import annotations

AMBER, GREEN, RED, BLUE, GREY = "#f9a825", "#2e7d32", "#c62828", "#0d47a1", "#888888"


def _badge(verdict: str) -> str:
    color = AMBER
    if "CALL" in verdict:
        color = GREEN
    elif "PUT" in verdict:
        color = RED
    elif "STAND ASIDE" in verdict:
        color = "#757575"
    return (f'<span style="background-color:{color};color:#ffffff;font-weight:bold;'
            f'padding:3px 10px;border-radius:4px;font-size:14px;">{verdict}</span>')


def _row(label: str, s: dict) -> str:
    return (
        f'<tr><td style="padding:3px 8px;border-bottom:1px solid #f0f0f0;">{label}</td>'
        + "".join(
            f'<td align="right" style="padding:3px 8px;border-bottom:1px solid #f0f0f0;">{s[k]}</td>'
            for k in ("open", "high", "low", "close", "poc", "vah", "val", "vwap"))
        + "</tr>"
    )


def _zone_card(title: str, color: str, lines: list[str]) -> str:
    body = "<br>".join(l for l in lines if l)
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:6px 0;"><tr>'
        f'<td bgcolor="{color}" style="background-color:{color};color:#ffffff;'
        f'border-radius:10px;padding:10px 14px;font-size:13px;">'
        f'<b style="font-size:14px;">{title}</b><br>{body}</td></tr></table>'
    )


def render_email(run: dict) -> tuple[str, str]:
    """Returns (subject, html_body)."""
    es = run.get("es") or {}
    am = es.get("action_map") or {}
    contract = es.get("contract", "ES")
    from datetime import date
    d = date.fromisoformat(run["date"])
    subject = f"ES Action Map - {d.strftime('%a %b %d, %Y')} ({contract})"

    p: list[str] = []
    p.append('<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;'
             'max-width:720px;color:#1a1a1a;">')

    for err in run.get("errors", []):
        p.append(f'<p style="font-size:13px;color:{RED};margin:0 0 8px;">'
                 f'Source unavailable ({err}) - map built from what is available.</p>')

    if not am:
        p.append('<p style="font-size:14px;">No action map today - not enough session data.</p></div>')
        return subject, "".join(p)

    # 1. headline + verdict
    price = es.get("price")
    p.append(f'<div style="font-size:18px;font-weight:bold;margin-bottom:4px;">Now: ES {price} '
             f'<span style="font-size:12px;color:{GREY};font-weight:normal;">{contract} &middot; '
             f'{run.get("generated_at_pt","")}</span></div>')
    p.append(f'<div style="margin-bottom:8px;">{_badge(am["verdict"])}</div>')

    # Provenance of the price the whole map is measured from. A delayed quote
    # during live trading can shift every distance below, so it is stated, not
    # implied.
    q = es.get("quote") or {}
    if q.get("source_label"):
        live = q.get("source") == "schwab"
        p.append(
            f'<div style="font-size:12px;color:{GREEN if live else "#b26a00"};'
            f'margin-bottom:8px;">{"" if live else "&#9888; "}'
            f'{q["source_label"]} &middot; {q.get("age_text","")}</div>')

    # 2. distance line + open type
    dist = am.get("distances", {})
    bits = []
    if dist.get("put_zone_above"):
        r = dist["put_zone_above"]
        bits.append(f'<span style="color:{RED};font-weight:bold;">&#9660; PUT ZONE is '
                    f'{r["pts"]} pts above ({r["range"][0]}-{r["range"][1]})</span>')
    if dist.get("call_zone_below"):
        r = dist["call_zone_below"]
        bits.append(f'<span style="color:{GREEN};font-weight:bold;">&#9650; CALL ZONE is '
                    f'{r["pts"]} pts below ({r["range"][0]}-{r["range"][1]})</span>')
    if bits:
        p.append(f'<p style="font-size:14px;margin:4px 0;">{" &middot; ".join(bits)}</p>')
    ot = am.get("open_type", {})
    p.append(f'<p style="font-size:12px;color:{GREY};margin:4px 0 10px;">'
             f'Open type: {ot.get("code","")} &rarr; {ot.get("text","")}</p>')

    # 3. event box
    for e in es.get("events", []):
        p.append(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
                 f'<tr><td bgcolor="#fff3e0" style="background-color:#fff3e0;'
                 f'border-left:4px solid #ef6c00;padding:8px 12px;font-size:13px;">'
                 f'{e["text"]}</td></tr></table>')

    # 4. zone cards, top to bottom
    z = am.get("zones", {})
    if z.get("breakout"):
        b = z["breakout"]
        tg = ", then ".join(str(t) for t in b["targets"]) or "next reference above"
        cap = f' &middot; cap {b["cap"]}' if b.get("cap") else ""
        p.append(_zone_card(
            f'&#9650; CALL ZONE &middot; breakout{" (low odds today)" if b.get("low_odds") else ""}',
            GREEN,
            [f'Buy calls: {b["trigger"]} &middot; {b["internals"]}',
             f'Target {tg}{cap} &middot; Stop {b["stop"]}']))
    if z.get("fade"):
        f_ = z["fade"]
        tg = ", then ".join(str(t) for t in f_["targets"]) or "prior value"
        p.append(_zone_card(
            f'&#9660; PUT ZONE &middot; fade{" (preferred)" if f_.get("preferred") else ""}',
            RED,
            [f'Band {f_["band"]["lo"]}-{f_["band"]["hi"]} [{", ".join(f_["band"]["sources"])}]',
             f'Buy puts: {f_["trigger"]} &middot; {f_["internals"]}',
             f'Target {tg} &middot; Stop {f_["stop"]}']))
    p.append(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
             f'<tr><td bgcolor="#fff8e1" style="background-color:#fff8e1;padding:6px 14px;'
             f'font-size:12px;color:#7a6000;border-radius:6px;">NO TRADE ZONE between the bands'
             f'</td></tr></table>')
    if z.get("dip"):
        dp = z["dip"]
        tg = ", then ".join(str(t) for t in dp["targets"]) or "prior POC"
        p.append(_zone_card(
            f'&#9650; CALL ZONE &middot; buy the dip{" (preferred)" if dp.get("preferred") else ""}',
            GREEN,
            [f'Band {dp["band"]["lo"]}-{dp["band"]["hi"]} [{", ".join(dp["band"]["sources"])}]',
             f'Buy calls: {dp["trigger"]} &middot; {dp["internals"]}',
             f'Target {tg} &middot; Stop {dp["stop"]}']))
    if z.get("breakdown"):
        bd = z["breakdown"]
        tg = ", then ".join(str(t) for t in bd["targets"]) or "next reference below"
        cap = f' &middot; cap {bd["cap"]}' if bd.get("cap") else ""
        p.append(_zone_card(
            f'&#9660; PUT ZONE &middot; breakdown{" (low odds today)" if bd.get("low_odds") else ""}',
            RED,
            [f'Buy puts: {bd["trigger"]} &middot; {bd["internals"]}',
             f'Target {tg}{cap} &middot; Stop {bd["stop"]}']))

    for n in am.get("notes", []):
        p.append(f'<p style="font-size:12px;color:{GREY};margin:4px 0;">{n}</p>')

    # 5. session prints
    def prints_table(title, sessions):
        head = ("".join(f'<th align="right" style="padding:3px 8px;font-size:11px;color:#666;'
                        f'border-bottom:1px solid #e0e0e0;">{h}</th>'
                        for h in ("Open", "High", "Low", "Close", "POC", "VAH", "VAL", "VWAP")))
        rows = "".join(_row(s["label"], s) for s in reversed(sessions.get("rth", [])))
        if sessions.get("overnight"):
            rows += _row(sessions["overnight"]["label"], sessions["overnight"])
        return (f'<p style="font-size:14px;font-weight:bold;margin:14px 0 4px;">{title}</p>'
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                f'style="font-size:12px;border-collapse:collapse;">'
                f'<tr><th align="left" style="padding:3px 8px;font-size:11px;color:#666;'
                f'border-bottom:1px solid #e0e0e0;">Session</th>{head}</tr>{rows}</table>')

    # ES only. §5.1: no SPY/SPX numbers, strikes or expiries in the email -
    # SPY lives on the platform instead.
    if es.get("sessions"):
        p.append(prints_table("ES session prints", es["sessions"]))

    # 6. sources + disclaimer
    srcs = []
    if es.get("author_levels"):
        srcs.append(", ".join(sorted({l.get("author", "") for l in es["author_levels"]})))
    srcs.append("session volume profile")
    p.append(f'<p style="font-size:12px;color:{GREY};margin:12px 0 4px;">Sources: '
             f'{" &middot; ".join(s for s in srcs if s)}. Short-dated options decay fast - '
             f'take the first target.</p>')
    p.append(f'<p style="font-size:12px;color:{GREY};margin:4px 0;">Decision map built from '
             f'published author levels and public price data; informational, not personalized '
             f'investment advice.</p>')
    p.append("</div>")
    return subject, "".join(p)
