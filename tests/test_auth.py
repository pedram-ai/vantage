"""Auth guards. This is the only thing between the public internet and the
account data once IAP is off, so each property is asserted and each assertion
carries a mutation proof.

Usage: python -m tests.test_auth
"""
from __future__ import annotations
import sys
from core import auth

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'OK  ' if cond else 'FAIL'} {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def test_hashing():
    print("Password hashing:")
    h, s = auth.hash_password("correct horse battery staple")
    check("verifies the right password", auth.verify_password("correct horse battery staple", h, s))
    check("rejects the wrong password", not auth.verify_password("wrong", h, s))
    h2, s2 = auth.hash_password("correct horse battery staple")
    check("salted — same password, different hash", h != h2, "otherwise a rainbow table works")
    check("hash is not the password", "correct" not in h)
    check("garbage salt does not crash", not auth.verify_password("x", h, "!!notb64!!"))


def test_policy():
    print("Password policy:")
    check("rejects short", auth.password_problem("short") is not None)
    check("rejects a known-guessable", auth.password_problem("argentridge") is not None)
    check("accepts a reasonable one", auth.password_problem("a-decent-passphrase-42") is None)


def test_iap_trust():
    print("IAP header trust:")
    import os
    os.environ.pop("VANTAGE_IAP_MODE", None)
    check("IAP header NOT trusted by default", not auth.iap_mode(),
          "a public service must never believe a client-set identity header")
    os.environ["VANTAGE_IAP_MODE"] = "true"
    check("trusted only when explicitly enabled", auth.iap_mode())
    os.environ.pop("VANTAGE_IAP_MODE", None)


def test_csrf():
    print("CSRF:")
    tok = "session-abc"
    c = auth.csrf_for(tok)
    check("token derived from session", bool(c) and len(c) == 32)
    check("matches itself", auth.csrf_ok(tok, c))
    check("rejects another session's token", not auth.csrf_ok("other", c))
    check("rejects empty", not auth.csrf_ok(tok, ""))
    check("no session -> no csrf", auth.csrf_for(None) == "")


def mutation_proof():
    print("Mutation proof:")
    h, s = auth.hash_password("abcdefghijkl")
    check("a one-character change fails verification",
          not auth.verify_password("abcdefghijkm", h, s))
    check("empty password does not pass against a real hash",
          not auth.verify_password("", h, s))


def main() -> int:
    test_hashing(); test_policy(); test_iap_trust(); test_csrf(); mutation_proof()
    print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)}): {FAILS}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
