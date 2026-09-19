"""Schwab Trader API — real-time quotes only.

Why this exists: Yahoo's CME feed is 10-minute delayed (Yahoo's own exchange
table), so the live price on the Real Time page and in the email can be stale
by enough to change a verdict. Schwab streams real-time futures under the
brokerage entitlement at no extra cost.

Scope, deliberately narrow:
  * real-time quote for the active ES contract  -> this module
  * historical bars                             -> NOT here; Schwab has none
    for futures (REST /pricehistory is equities/ETFs only). Yahoo stays the
    history source.

Credentials live in Secret Manager in patexia-vantage:
  schwab-app-key        the app key from developer.schwab.com
  schwab-app-secret     the app secret
  schwab-refresh-token  written by the OAuth callback, rotated on each login

⛔ The refresh token expires every 7 days with no programmatic renewal. This
module therefore NEVER raises into the caller's happy path: if anything is
missing or expired it returns None and `core.quote` falls back to Yahoo. A
broken Schwab link must never stop the map from being produced.
"""

from __future__ import annotations

import base64
import os
import time
from urllib.parse import urlencode

from curl_cffi import requests as curl_requests

from google.api_core import exceptions as gexc
from google.cloud import secretmanager

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "patexia-vantage")
AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
QUOTE_URL = "https://api.schwabapi.com/marketdata/v1/quotes"

SECRET_KEY = "schwab-app-key"
SECRET_SECRET = "schwab-app-secret"
SECRET_REFRESH = "schwab-refresh-token"

# Access tokens last 30 min; refresh a little early.
_access: dict = {"token": None, "expires_at": 0.0}

_sm: secretmanager.SecretManagerServiceClient | None = None


def _client() -> secretmanager.SecretManagerServiceClient:
    global _sm
    if _sm is None:
        _sm = secretmanager.SecretManagerServiceClient()
    return _sm


def read_secret(name: str) -> str | None:
    try:
        path = f"projects/{PROJECT}/secrets/{name}/versions/latest"
        return _client().access_secret_version(name=path).payload.data.decode().strip()
    except Exception:  # noqa: BLE001 - missing/denied/disabled all mean "not configured"
        return None


def write_secret(name: str, value: str) -> None:
    """Add a new version, creating the secret if needed."""
    parent = f"projects/{PROJECT}"
    try:
        _client().create_secret(
            parent=parent, secret_id=name,
            secret={"replication": {"automatic": {}}},
        )
    except gexc.AlreadyExists:
        pass
    _client().add_secret_version(
        parent=f"{parent}/secrets/{name}",
        payload={"data": value.encode()},
    )


def is_configured() -> bool:
    return bool(read_secret(SECRET_KEY) and read_secret(SECRET_SECRET))


def is_linked() -> bool:
    return bool(read_secret(SECRET_REFRESH))


def _basic_auth() -> str | None:
    key, secret = read_secret(SECRET_KEY), read_secret(SECRET_SECRET)
    if not key or not secret:
        return None
    return base64.b64encode(f"{key}:{secret}".encode()).decode()


def authorize_url(redirect_uri: str) -> str | None:
    key = read_secret(SECRET_KEY)
    if not key:
        return None
    return f"{AUTH_URL}?" + urlencode({
        "client_id": key, "redirect_uri": redirect_uri, "response_type": "code",
    })


def _post_token(data: dict) -> dict | None:
    auth = _basic_auth()
    if not auth:
        return None
    try:
        r = curl_requests.post(
            TOKEN_URL, data=data, impersonate="chrome", timeout=20,
            headers={"Authorization": f"Basic {auth}",
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
        return r.json()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Complete the OAuth login. Stores the refresh token. Returns a status."""
    tok = _post_token({"grant_type": "authorization_code", "code": code,
                       "redirect_uri": redirect_uri})
    if not tok:
        return {"ok": False, "error": "Schwab app key/secret not configured"}
    if tok.get("error") or "refresh_token" not in tok:
        return {"ok": False, "error": tok.get("error", "no refresh_token in response")}
    write_secret(SECRET_REFRESH, tok["refresh_token"])
    _access["token"] = tok.get("access_token")
    _access["expires_at"] = time.time() + float(tok.get("expires_in", 1800)) - 60
    return {"ok": True}


def access_token() -> str | None:
    if _access["token"] and time.time() < _access["expires_at"]:
        return _access["token"]
    refresh = read_secret(SECRET_REFRESH)
    if not refresh:
        return None
    tok = _post_token({"grant_type": "refresh_token", "refresh_token": refresh})
    if not tok or tok.get("error") or "access_token" not in tok:
        return None
    _access["token"] = tok["access_token"]
    _access["expires_at"] = time.time() + float(tok.get("expires_in", 1800)) - 60
    # Schwab rotates the refresh token on some refreshes; persist when it does.
    if tok.get("refresh_token") and tok["refresh_token"] != refresh:
        try:
            write_secret(SECRET_REFRESH, tok["refresh_token"])
        except Exception:  # noqa: BLE001
            pass
    return _access["token"]


def parse_quote(payload: dict, symbol: str) -> dict | None:
    """Pull price + timestamp out of a Schwab quotes response.

    Written tolerantly on purpose: the live response has not been observed
    (no credentials yet), and Schwab returns the resolved contract symbol
    (/ESZ26) rather than the key asked for (/ES).
    """
    if not isinstance(payload, dict) or not payload:
        return None
    node = payload.get(symbol)
    if node is None:
        # fall back to the first future-looking entry
        for v in payload.values():
            if isinstance(v, dict) and (v.get("assetMainType") == "FUTURE" or "quote" in v):
                node = v
                break
    if not isinstance(node, dict):
        return None
    q = node.get("quote") or node
    price = None
    for k in ("lastPrice", "mark", "lastPriceInDouble", "closePrice"):
        if isinstance(q.get(k), (int, float)):
            price = float(q[k])
            break
    if price is None:
        return None
    ts = None
    for k in ("quoteTime", "tradeTime", "quoteTimeInLong", "tradeTimeInLong"):
        v = q.get(k)
        if isinstance(v, (int, float)) and v > 0:
            ts = float(v) / 1000.0 if v > 1e12 else float(v)
            break
    return {"price": price, "as_of": ts,
            "symbol": node.get("symbol") or q.get("symbol") or symbol}


def quote(symbol: str = "/ES") -> dict | None:
    """Real-time quote, or None if Schwab is unavailable for any reason."""
    tok = access_token()
    if not tok:
        return None
    try:
        r = curl_requests.get(
            QUOTE_URL, params={"symbols": symbol}, impersonate="chrome", timeout=15,
            headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"},
        )
        if r.status_code != 200:
            return None
        return parse_quote(r.json(), symbol)
    except Exception:  # noqa: BLE001
        return None


def status() -> dict:
    """For the Settings page — never raises."""
    configured = is_configured()
    linked = is_linked()
    live = None
    if configured and linked:
        live = quote()
    return {
        "configured": configured,
        "linked": linked,
        "working": bool(live),
        "last_price": live.get("price") if live else None,
        "symbol": live.get("symbol") if live else None,
    }
