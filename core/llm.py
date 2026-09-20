"""Gemini on Vertex AI.

⛔ ANTHROPIC IS NOT USED ANYWHERE IN THIS APP and must not be reintroduced
(Pedram, 2026-09-20: *"don't use anthropic at all. And even if you need LLM
model, use gemini from model garden"*). `tests/test_platform.py` greps for it.

Auth is the **Cloud Run service identity** via ADC — there is no API key to
store, rotate or leak, and nothing on the request path reads Secret Manager
(an uncached secret lookup once cost this app 2,870 ms per page render).

⚠ The model id is resolved ONCE per process against the live endpoint, from a
preference list, and the winner is remembered. A hardcoded model id is a
constant that goes stale silently: the call starts 404-ing on a day nobody
deployed anything, and a fail-soft caller turns that into "no summary yet".
`status()` reports which model actually answered, so the page can say so.
"""

from __future__ import annotations

import json
import os
import threading
import time

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "patexia-vantage")
LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")

# Best first. The first one that answers is used for the life of the process.
MODEL_PREFERENCE = [
    os.environ.get("VERTEX_MODEL") or "",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

TIMEOUT = 90.0

_LOCK = threading.Lock()
_MODEL: str | None = None
_LAST_ERROR: str | None = None
_TOKEN: tuple[float, str] | None = None


def _token() -> str | None:
    """Service identity token, cached until shortly before it expires."""
    global _TOKEN
    now = time.monotonic()
    if _TOKEN and now - _TOKEN[0] < 1800:
        return _TOKEN[1]
    try:
        import google.auth
        import google.auth.transport.requests
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        _TOKEN = (now, creds.token)
        return creds.token
    except Exception:  # noqa: BLE001
        return None


def _url(model: str) -> str:
    return (f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT}"
            f"/locations/{LOCATION}/publishers/google/models/{model}:generateContent")


def _call(model: str, system: str, prompt: str, schema: dict | None) -> tuple[dict | str | None, str | None]:
    """(result, error). Never raises."""
    tok = _token()
    if not tok:
        return None, "no Google credentials (ADC unavailable)"
    body: dict = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096},
    }
    if schema:
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["responseSchema"] = schema
    try:
        from curl_cffi import requests as cr
        r = cr.post(_url(model), json=body, timeout=TIMEOUT,
                    headers={"Authorization": f"Bearer {tok}",
                             "Content-Type": "application/json"})
        if r.status_code != 200:
            return None, f"{r.status_code}: {r.text[:160]}"
        d = r.json()
        cand = (d.get("candidates") or [{}])[0]
        # ⚠ A safety block returns a candidate with NO parts. Reading
        # parts[0] there raises, and a fail-soft caller would record it as a
        # generic failure rather than the refusal it is.
        parts = (cand.get("content") or {}).get("parts") or []
        if not parts:
            return None, f"no content ({cand.get('finishReason', 'unknown')})"
        text = parts[0].get("text", "")
        if not schema:
            return text, None
        try:
            return json.loads(text), None
        except json.JSONDecodeError:
            return None, "model returned non-JSON despite a response schema"
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:160]


def generate(system: str, prompt: str, schema: dict | None = None):
    """Returns (result, error). Resolves the model on first use."""
    global _MODEL, _LAST_ERROR
    with _LOCK:
        chosen = _MODEL
    if chosen:
        out, err = _call(chosen, system, prompt, schema)
        if out is not None:
            return out, None
        # A 404 means the pinned model went away; fall through and re-resolve.
        if not str(err).startswith("404"):
            _LAST_ERROR = err
            return None, err
        with _LOCK:
            _MODEL = None

    errors = []
    for m in [m for m in MODEL_PREFERENCE if m]:
        out, err = _call(m, system, prompt, schema)
        if out is not None:
            with _LOCK:
                _MODEL = m
            return out, None
        errors.append(f"{m}: {err}")
    _LAST_ERROR = " | ".join(errors)[:300]
    return None, _LAST_ERROR


def status() -> dict:
    """For System Health and the Research page. Never raises, never a secret."""
    return {
        "provider": "Gemini on Vertex AI",
        "project": PROJECT,
        "location": LOCATION,
        "model": _MODEL,
        "credentials": bool(_token()),
        "last_error": _LAST_ERROR,
    }
