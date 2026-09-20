"""AI review of the trading record.

⛔ THE HARD RULE: the model NEVER sees raw trades and NEVER invents a figure.
It receives a compact set of already-computed aggregates and must cite, in a
`figure` field, the exact value it reasoned from. The page renders that field
next to the observation, so a fabricated number is visible rather than
plausible.

Everything here is fail-soft: no key, no network, a malformed reply — the panel
disappears, and the rest of Performance is unaffected. Results are cached per
(period, data fingerprint) so a page refresh costs nothing and the analysis only
re-runs when the underlying numbers actually move.
"""

from __future__ import annotations

import hashlib
import json
import os

MODEL = "claude-opus-5"
CACHE_COLL = "ai_insights"

SYSTEM = """You review one trader's own realized results and tell them what the \
numbers show. You are talking to the person who placed these trades.

Hard rules:
- Use ONLY the figures in the payload. Never estimate, extrapolate or invent a \
number, and never infer one that is not present.
- Every observation must carry the exact figure it rests on, copied verbatim \
into the `figure` field.
- Say what the data shows, including when it is unflattering. Do not soften a \
loss into a lesson, and do not congratulate.
- If a pattern could have an innocent explanation the data cannot rule out, say \
so in the observation rather than asserting cause.
- No investment advice, no predictions, no suggestions to buy or sell anything. \
Describe what happened, not what to do next.

Write plainly, second person, one or two sentences per observation."""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {
            "type": "string",
            "description": "One sentence: the single most important thing the numbers show.",
        },
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Three to six words."},
                    "detail": {"type": "string", "description": "One or two sentences."},
                    "figure": {
                        "type": "string",
                        "description": "The exact figure from the payload this rests on.",
                    },
                    "tone": {"type": "string", "enum": ["good", "bad", "neutral"]},
                },
                "required": ["title", "detail", "figure", "tone"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "observations"],
    "additionalProperties": False,
}


SECRET_NAME = "anthropic-api-key"


def _api_key() -> str | None:
    """Env first, then Vantage's own secret, then the shared lexdana one.

    Vantage's own is checked BEFORE lexdana's so a key pasted in Settings
    overrides a shared key that has been rotated elsewhere.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        from google.cloud import secretmanager
        c = secretmanager.SecretManagerServiceClient()
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "patexia-vantage")
        for path in (f"projects/{project}/secrets/{SECRET_NAME}/versions/latest",
                     f"projects/lexdana/secrets/{SECRET_NAME}/versions/latest"):
            try:
                v = c.access_secret_version(name=path).payload.data.decode().strip()
                if v:
                    return v
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return None


def status() -> dict:
    """For the Settings page — never raises, never returns the key."""
    return {"configured": bool(_api_key())}


def build_payload(perf: dict) -> dict:
    """The ONLY thing the model sees. Aggregates, never raw trades."""
    acct = perf.get("account") or {}
    by_month: dict[str, float] = {}
    for t in perf.get("all_trades") or perf.get("trades") or []:
        d = t.get("closed_on")
        if d:
            by_month[d[:7]] = round(by_month.get(d[:7], 0.0) + float(t.get("pnl", 0) or 0), 2)

    def money(v):
        return None if v is None else round(float(v), 2)

    return {
        "period": perf.get("label"),
        "closed_trades": perf.get("n_trades"),
        "realized_pnl_net_of_fees": money(perf.get("realized")),
        "fees_paid": money(perf.get("fees")),
        "gross_pnl_before_fees": money((perf.get("realized") or 0) + (perf.get("fees") or 0)),
        "win_rate_pct": perf.get("win_rate"),
        "avg_win": money(perf.get("avg_win")),
        "avg_loss": money(perf.get("avg_loss")),
        "by_month": dict(sorted(by_month.items())),
        "by_instrument": [
            {"symbol": r["key"], "trades": r["n"], "win_rate_pct": r["win_rate"],
             "net_pnl": money(r["pnl"])}
            for r in (perf.get("by_symbol") or [])
        ],
        "by_setup": [
            {"zone": r["key"], "trades": r["n"], "win_rate_pct": r["win_rate"],
             "net_pnl": money(r["pnl"])}
            for r in (perf.get("by_zone") or [])
        ],
        "account_lifetime": {
            "deposited": money(acct.get("net_deposits")),
            "cash_now": money(acct.get("net_cash")),
            "realized_pnl": money(acct.get("realized_pnl")),
            "total_fees": money(acct.get("total_fees")),
            "return_pct_of_deposits": (round(acct["return_pct"], 1)
                                       if acct.get("return_pct") is not None else None),
        } if acct else None,
    }


def _fingerprint(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _cached(key: str) -> dict | None:
    try:
        from .store import db
        doc = db().collection(CACHE_COLL).document(key).get()
        return doc.to_dict() if doc.exists else None
    except Exception:  # noqa: BLE001
        return None


def _cache(key: str, value: dict) -> None:
    try:
        from .store import db, now_iso
        db().collection(CACHE_COLL).document(key).set({**value, "cached_at": now_iso()})
    except Exception:  # noqa: BLE001
        pass


def review(perf: dict, force: bool = False) -> dict:
    """Return {'ok': bool, ...}. Never raises."""
    if perf.get("empty"):
        return {"ok": False, "reason": "no trades to review"}

    payload = build_payload(perf)
    key = f"{perf.get('period')}-{_fingerprint(payload)}"
    if not force:
        hit = _cached(key)
        if hit:
            return {**hit, "ok": True, "cached": True}

    api_key = _api_key()
    if not api_key:
        return {"ok": False, "reason": "Anthropic API key not available"}

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{
                "role": "user",
                "content": ("Review these results and give me the headline plus 3 to 5 "
                            "observations.\n\n" + json.dumps(payload, indent=1)),
            }],
        )
        if resp.stop_reason == "refusal":
            return {"ok": False, "reason": "the model declined to answer"}
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = json.loads(text)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": str(e)[:160]}

    out = {
        "headline": data.get("headline", ""),
        "observations": (data.get("observations") or [])[:6],
        "model": MODEL,
        "period": perf.get("period"),
    }
    _cache(key, out)
    return {**out, "ok": True, "cached": False}
