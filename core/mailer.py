"""Outgoing email via AWS SES v2 — the same verified sender Connect uses
(notify.patexia.com). Credentials come from Secret Manager at deploy time as
env vars; nothing is stored in the repo.

Fail-soft: if SES is not configured the send is a logged no-op so the daily
run still archives.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import os
import urllib.parse

from curl_cffi import requests as curl_requests

REGION = os.environ.get("SES_AWS_REGION", "us-east-1")
FROM = os.environ.get("SES_FROM_EMAIL", "Vantage <vantage@notify.patexia.com>")
REPLY_TO = os.environ.get("SES_REPLY_TO", "")
SERVICE = "ses"


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _sigv4_headers(access_key: str, secret_key: str, host: str, path: str,
                   payload: str) -> dict:
    now = _dt.datetime.now(_dt.timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    canonical_headers = f"content-type:application/json\nhost:{host}\nx-amz-date:{amzdate}\n"
    signed_headers = "content-type;host;x-amz-date"
    payload_hash = hashlib.sha256(payload.encode()).hexdigest()
    canonical_request = (f"POST\n{urllib.parse.quote(path)}\n\n{canonical_headers}\n"
                         f"{signed_headers}\n{payload_hash}")
    scope = f"{datestamp}/{REGION}/{SERVICE}/aws4_request"
    string_to_sign = (f"AWS4-HMAC-SHA256\n{amzdate}\n{scope}\n"
                      f"{hashlib.sha256(canonical_request.encode()).hexdigest()}")
    k = _sign(f"AWS4{secret_key}".encode(), datestamp)
    k = _sign(k, REGION)
    k = _sign(k, SERVICE)
    k = _sign(k, "aws4_request")
    signature = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Amz-Date": amzdate,
        "Authorization": (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                          f"SignedHeaders={signed_headers}, Signature={signature}"),
    }


def _archive(to: str, subject: str, html: str, kind: str, result: dict) -> None:
    """Record every outbound email so Admin -> Sent email shows what went out.

    ⛔ SETUP/RESET TOKENS ARE REDACTED. The archive answers "what was sent",
    and an admin browsing it must not be able to lift a live password-reset
    link out of someone else's email and use it.
    """
    try:
        import re as _re
        from .store import db, now_iso
        safe = _re.sub(r"(/setup/)[A-Za-z0-9_\-]{16,}", r"\1[redacted]", html)
        db().collection("emails").document().set({
            "to": to, "subject": subject, "kind": kind,
            "html": safe,
            "redacted": safe != html,
            "ok": bool(result.get("ok")),
            "skipped": bool(result.get("skipped")),
            "reason": result.get("reason") or result.get("error"),
            "message_id": result.get("message_id"),
            "sent_at": now_iso(),
            "from": FROM,
        })
    except Exception:  # noqa: BLE001
        pass  # archiving must never break a send


def send_html(to: str, subject: str, html: str, kind: str = "other") -> dict:
    res = _send_html(to, subject, html)
    _archive(to, subject, html, kind, res)
    return res


def _send_html(to: str, subject: str, html: str) -> dict:
    ak = os.environ.get("AWS_ACCESS_KEY_ID")
    sk = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not (ak and sk):
        return {"ok": False, "skipped": True, "reason": "SES credentials not configured"}

    host = f"email.{REGION}.amazonaws.com"
    path = "/v2/email/outbound-emails"
    body = {
        "FromEmailAddress": FROM,
        "Destination": {"ToAddresses": [to]},
        "Content": {"Simple": {
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Html": {"Data": html, "Charset": "UTF-8"}},
        }},
    }
    if REPLY_TO:
        body["ReplyToAddresses"] = [REPLY_TO]
    payload = json.dumps(body)
    headers = _sigv4_headers(ak, sk, host, path, payload)
    try:
        r = curl_requests.post(f"https://{host}{path}", data=payload.encode(),
                               headers=headers, timeout=30)
        if r.status_code >= 300:
            return {"ok": False, "status": r.status_code, "error": r.text[:300]}
        return {"ok": True, "message_id": r.json().get("MessageId")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}
