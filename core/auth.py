"""Staff authentication for Argent Ridge.

This replaces IAP when the app is served on a custom domain, so it is the ONLY
thing between the public internet and Pedram's account data. It is written
accordingly.

Design:
  * passwords hashed with scrypt (stdlib — no new dependency, memory-hard)
  * sessions are opaque random tokens stored server-side, so they are
    revocable; the cookie holds the token, never user data
  * only the token's SHA-256 is stored, so a Firestore leak does not hand
    over live sessions
  * failed logins are rate-limited and lock out per identity
  * ⛔ THERE IS NO SIGN-UP PATH. Users exist only because an owner created
    them. `create_user` is reachable from the admin page alone.
  * new users set their own password through a single-use token, so no
    password is ever transmitted to, chosen by, or logged by the operator.

⛔ THE IAP HEADER IS NOT TRUSTED BY DEFAULT. `x-goog-authenticated-user-email`
is only believable when IAP actually fronts the app — if the service is public
and we trusted it, anyone could set that header and walk in. It is honoured
only when VANTAGE_IAP_MODE=true.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from .store import db, now_iso

SESSION_COOKIE = "ar_session"
SESSION_DAYS = 14
SETUP_TOKEN_HOURS = 48

# Brute-force policy
MAX_FAILS = 8
LOCKOUT_MINUTES = 15

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
DK_LEN = 32

ROLES = ("owner", "staff")


def iap_mode() -> bool:
    return os.environ.get("VANTAGE_IAP_MODE", "").lower() in ("1", "true", "yes")


# --- password hashing -------------------------------------------------------

def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R,
                        p=SCRYPT_P, dklen=DK_LEN)
    return base64.b64encode(dk).decode(), base64.b64encode(salt).decode()


def verify_password(password: str, pw_hash: str, salt: str) -> bool:
    try:
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                            n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=DK_LEN)
    except Exception:  # noqa: BLE001
        return False
    # constant time — never a plain ==
    return hmac.compare_digest(base64.b64encode(dk).decode(), pw_hash)


def password_problem(password: str) -> str | None:
    """Return a reason the password is unacceptable, or None."""
    if len(password) < 12:
        return "Use at least 12 characters."
    if password.lower() in {"password", "argentridge", "changeme"}:
        return "That password is guessable."
    if password.strip() != password:
        return "Password cannot start or end with a space."
    return None


# --- users ------------------------------------------------------------------

def _users():
    return db().collection("users")


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def get_user(email: str) -> dict | None:
    email = normalize_email(email)
    if not email:
        return None
    doc = _users().document(email).get()
    if not doc.exists:
        return None
    u = doc.to_dict()
    u["email"] = email
    return u


def list_users() -> list[dict]:
    out = []
    for doc in _users().stream():
        u = doc.to_dict()
        u["email"] = doc.id
        u.pop("pw_hash", None)
        u.pop("pw_salt", None)
        u.pop("setup_token_hash", None)
        out.append(u)
    return sorted(out, key=lambda r: r.get("created_at", ""))


def user_count() -> int:
    return sum(1 for _ in _users().stream())


def create_user(email: str, name: str = "", role: str = "staff") -> dict:
    """Create a user WITHOUT a password and return a single-use setup token.

    The operator never sees or sets the password — the new user does.
    """
    email = normalize_email(email)
    if not email or "@" not in email:
        raise ValueError("A valid email address is required.")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if get_user(email):
        raise ValueError(f"{email} already exists.")
    token = secrets.token_urlsafe(32)
    _users().document(email).set({
        "name": name.strip() or email.split("@")[0],
        "role": role,
        "disabled": False,
        "pw_hash": None,
        "pw_salt": None,
        "setup_token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "setup_expires": (datetime.now(timezone.utc)
                          + timedelta(hours=SETUP_TOKEN_HOURS)).isoformat(),
        "created_at": now_iso(),
        "last_login": None,
    })
    return {"email": email, "setup_token": token}


def set_disabled(email: str, disabled: bool) -> None:
    _users().document(normalize_email(email)).set({"disabled": disabled}, merge=True)
    if disabled:
        revoke_all_sessions(normalize_email(email))


def delete_user(email: str) -> None:
    email = normalize_email(email)
    revoke_all_sessions(email)
    _users().document(email).delete()


def new_setup_token(email: str, revoke_password: bool = True) -> str:
    """Issue a single-use setup/reset token. Invalidates any previous one.

    `revoke_password=False` keeps the current password working until the new
    one is set — correct for a self-service reset, where someone who merely
    *requests* a reset (or an attacker who triggers one) must not be able to
    lock the real owner out.
    """
    email = normalize_email(email)
    if not get_user(email):
        raise ValueError("No such user.")
    token = secrets.token_urlsafe(32)
    patch = {
        "setup_token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "setup_expires": (datetime.now(timezone.utc)
                          + timedelta(hours=SETUP_TOKEN_HOURS)).isoformat(),
    }
    if revoke_password:
        patch.update({"pw_hash": None, "pw_salt": None})
    _users().document(email).set(patch, merge=True)
    if revoke_password:
        revoke_all_sessions(email)
    return token


def request_reset(email: str) -> bool:
    """Self-service reset. Returns whether an email was actually sent.

    ⛔ The CALLER must show the same message either way. Revealing that an
    address has no account turns this form into a user-enumeration oracle.
    """
    email = normalize_email(email)
    u = get_user(email)
    if not u or u.get("disabled"):
        return False
    if lockout_remaining(email) > 0:
        return False
    token = new_setup_token(email, revoke_password=False)
    base = os.environ.get("VANTAGE_BASE_URL", "https://argentridge.com").rstrip("/")
    try:
        from . import mail_templates
        from .mailer import send_html
        subject, html = mail_templates.reset(
            u.get("name") or email.split("@")[0],
            f"{base}/setup/{token}", SETUP_TOKEN_HOURS)
        res = send_html(email, subject, html, kind="password_reset")
        return bool(res.get("ok"))
    except Exception:  # noqa: BLE001
        return False


def send_invite(email: str, token: str) -> bool:
    email = normalize_email(email)
    u = get_user(email) or {}
    base = os.environ.get("VANTAGE_BASE_URL", "https://argentridge.com").rstrip("/")
    try:
        from . import mail_templates
        from .mailer import send_html
        subject, html = mail_templates.invite(
            u.get("name") or email.split("@")[0],
            f"{base}/setup/{token}", SETUP_TOKEN_HOURS)
        return bool(send_html(email, subject, html, kind="invite").get("ok"))
    except Exception:  # noqa: BLE001
        return False


def find_setup(token: str) -> dict | None:
    """Resolve a setup token to its user, if unexpired."""
    if not token:
        return None
    h = hashlib.sha256(token.encode()).hexdigest()
    for doc in _users().where("setup_token_hash", "==", h).stream():
        u = doc.to_dict()
        u["email"] = doc.id
        exp = u.get("setup_expires")
        if exp and exp < datetime.now(timezone.utc).isoformat():
            return None
        return u
    return None


def complete_setup(token: str, password: str) -> dict:
    u = find_setup(token)
    if not u:
        raise ValueError("This setup link is invalid or has expired.")
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)
    pw_hash, salt = hash_password(password)
    _users().document(u["email"]).set({
        "pw_hash": pw_hash, "pw_salt": salt,
        "setup_token_hash": None, "setup_expires": None,
    }, merge=True)
    return u


def change_password(email: str, current: str, new: str) -> None:
    """Change your OWN password. Raises ValueError with a safe message.

    ⛔ THE CURRENT PASSWORD IS REQUIRED even though the caller is already
    signed in. A live session is not proof of identity at the keyboard — an
    unlocked laptop or a stolen cookie is exactly the case this defends, and
    without it either one becomes permanent account takeover.

    ⛔ Every OTHER session is revoked on success and the current one is
    re-minted by the caller. Changing a password must evict whoever else was
    signed in, which is the main reason a person changes one.
    """
    email = normalize_email(email)
    u = get_user(email)
    if not u or not u.get("pw_hash"):
        raise ValueError("This account cannot change its password here.")
    if not verify_password(current, u["pw_hash"], u["pw_salt"]):
        record_fail(email)
        raise ValueError("That is not your current password.")
    if current == new:
        raise ValueError("The new password must be different.")
    problem = password_problem(new)
    if problem:
        raise ValueError(problem)
    pw_hash, salt = hash_password(new)
    _users().document(email).set({
        "pw_hash": pw_hash, "pw_salt": salt,
        "setup_token_hash": None, "setup_expires": None,
        "password_changed_at": now_iso(),
    }, merge=True)
    clear_fails(email)
    revoke_all_sessions(email)


def update_profile(email: str, name: str) -> None:
    """The only self-editable profile field. ⛔ Role and email are NOT — a user
    who could edit their own role could promote themselves to owner."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Name cannot be empty.")
    if len(name) > 80:
        raise ValueError("Name is too long.")
    _users().document(normalize_email(email)).set({"name": name}, merge=True)


def active_sessions(email: str) -> list[dict]:
    """Where this account is currently signed in. No tokens, ever."""
    email = normalize_email(email)
    out = []
    for doc in _sessions().where("email", "==", email).stream():
        d = doc.to_dict()
        out.append({"created_at": d.get("created_at"), "expires_at": d.get("expires_at"),
                    "ip": d.get("ip"), "ua": (d.get("ua") or "")[:80]})
    return sorted(out, key=lambda r: r.get("created_at") or "", reverse=True)


# --- rate limiting ----------------------------------------------------------

def _fails():
    return db().collection("login_fails")


def _fail_key(email: str) -> str:
    return hashlib.sha256(normalize_email(email).encode()).hexdigest()[:40]


def lockout_remaining(email: str) -> int:
    doc = _fails().document(_fail_key(email)).get()
    if not doc.exists:
        return 0
    d = doc.to_dict()
    if (d.get("count", 0) or 0) < MAX_FAILS:
        return 0
    last = d.get("last")
    if not last:
        return 0
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return 0
    unlock = when + timedelta(minutes=LOCKOUT_MINUTES)
    left = (unlock - datetime.now(timezone.utc)).total_seconds()
    return max(int(left), 0)


def record_fail(email: str) -> None:
    ref = _fails().document(_fail_key(email))
    doc = ref.get()
    count = (doc.to_dict().get("count", 0) if doc.exists else 0) + 1
    ref.set({"count": count, "last": datetime.now(timezone.utc).isoformat()})


def clear_fails(email: str) -> None:
    _fails().document(_fail_key(email)).delete()


# --- sessions ---------------------------------------------------------------

def _sessions():
    return db().collection("sessions")


def _tok_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(email: str, password: str, ip: str = "", ua: str = "") -> str:
    """Return a session token, or raise ValueError with a safe message."""
    email = normalize_email(email)
    left = lockout_remaining(email)
    if left:
        raise ValueError(f"Too many attempts. Try again in {left // 60 + 1} minute(s).")

    u = get_user(email)
    # Same message and similar work whether the user exists or not, so the
    # form cannot be used to enumerate who has an account.
    ok = False
    if u and not u.get("disabled") and u.get("pw_hash"):
        ok = verify_password(password, u["pw_hash"], u["pw_salt"])
    else:
        hash_password(password)  # burn comparable time

    if not ok:
        record_fail(email)
        raise ValueError("Incorrect email or password.")

    clear_fails(email)
    token = secrets.token_urlsafe(40)
    _sessions().document(_tok_hash(token)).set({
        "email": email,
        "created_at": now_iso(),
        "expires_at": (datetime.now(timezone.utc)
                       + timedelta(days=SESSION_DAYS)).isoformat(),
        "ip": ip[:64], "ua": ua[:200],
    })
    _users().document(email).set({"last_login": now_iso()}, merge=True)
    return token


def session_user(token: str | None) -> dict | None:
    if not token:
        return None
    doc = _sessions().document(_tok_hash(token)).get()
    if not doc.exists:
        return None
    s = doc.to_dict()
    if (s.get("expires_at") or "") < datetime.now(timezone.utc).isoformat():
        doc.reference.delete()
        return None
    u = get_user(s.get("email", ""))
    if not u or u.get("disabled"):
        return None
    return u


def logout(token: str | None) -> None:
    if token:
        _sessions().document(_tok_hash(token)).delete()


def revoke_all_sessions(email: str) -> None:
    email = normalize_email(email)
    for doc in _sessions().where("email", "==", email).stream():
        doc.reference.delete()


# --- CSRF -------------------------------------------------------------------

def csrf_for(token: str | None) -> str:
    """Derived from the session token, so it needs no extra storage."""
    if not token:
        return ""
    secret = os.environ.get("VANTAGE_CSRF_SECRET", "argent-ridge-csrf")
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()[:32]


def csrf_ok(token: str | None, submitted: str | None) -> bool:
    expected = csrf_for(token)
    if not expected or not submitted:
        return False
    return hmac.compare_digest(expected, submitted)
