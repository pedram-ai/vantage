"""Version stamp, profile/password, and the IA move.

Each block guards something that fails QUIETLY:

* A version that repeats makes "did my fix ship?" unanswerable, and float
  arithmetic on 0.001 steps produces 0.029000000000000005 without erroring.
* A password change that does not require the current password turns an
  unlocked laptop into permanent account takeover, and nothing on screen
  looks different.
* A self-editable role is privilege escalation wearing a form field.
* A moved page that 404s instead of redirecting loses every bookmark, and
  only the person who had the bookmark ever finds out.
* An Anthropic import left behind is a 3.5-second page and a bill.

Run: python -m tests.test_platform
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)
    return cond


# --- 1. versioning ----------------------------------------------------------

def test_version_arithmetic() -> None:
    print("\n1. version steps by exactly 0.001, forever")
    import bump_version as bv

    # ⛔ The mutation this catches: `float(cur) + 0.001`. Run it 1,000 times and
    # a float accumulates visible error; the integer path cannot.
    seq = [bv.fmt(n) for n in range(1, 1001)]
    check("V0.001 is the first", seq[0] == "0.001", seq[0])
    check("steps are exact at 29", seq[28] == "0.029", seq[28])
    check("rolls to 1.000 at the thousandth", seq[999] == "1.000", seq[999])
    check("always three decimals", all(re.fullmatch(r"\d+\.\d{3}", v) for v in seq))

    float_seq, x = [], 0.0
    for _ in range(29):
        x += 0.001
        float_seq.append(f"{x}")
    check("(proof) the float version WOULD drift",
          any(len(v.split(".")[1]) > 3 for v in float_seq),
          f"float reaches {float_seq[-1]}")


def test_version_persists_and_only_rises() -> None:
    print("\n2. VERSION is committed, so it can only go up")
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "VERSION"],
                             cwd=ROOT, capture_output=True, text=True)
    check("VERSION is tracked by git", tracked.returncode == 0,
          "a gitignored version resets to 0.001 on a clean checkout")

    import bump_version as bv
    real = bv.VERSION_FILE
    with tempfile.TemporaryDirectory() as d:
        bv.VERSION_FILE = os.path.join(d, "VERSION")
        open(bv.VERSION_FILE, "w").write("0.017\n")
        bv.main()
        after = open(bv.VERSION_FILE).read().strip()
    bv.VERSION_FILE = real
    check("bump writes the next value", after == "0.018", after)


def test_footer_never_invents_a_version() -> None:
    print("\n3. the footer says 'dev build' rather than guessing")
    from core import version
    real = version._INFO
    try:
        version._INFO = {}
        f = version.footer()
        check("no build info -> dev", f["dev"] and f["version"] is None, str(f))
        version._INFO = {"version": "0.042",
                         "deployed_at": "2026-09-20T22:10:30+00:00", "short": "abc12345"}
        f = version.footer()
        check("version renders with a V", f["version"] == "V0.042", f["version"])
        # ⛔ Pacific, not UTC. 22:10 UTC is 3:10 PM the same day in LA.
        check("timestamp is Pacific", "3:10 PM PT" in (f["deployed"] or ""), f["deployed"])
    finally:
        version._INFO = real


# --- 4. profile + password --------------------------------------------------

def test_password_change_rules() -> None:
    print("\n4. changing a password")
    from core import auth
    email = "cachetest-profile@example.invalid"
    try:
        auth.delete_user(email)
    except Exception:  # noqa: BLE001
        pass
    tok = auth.create_user(email, "Test", "staff")["setup_token"]
    auth.complete_setup(tok, "correct-horse-battery")

    def fails_with(cur, new):
        try:
            auth.change_password(email, cur, new)
            return None
        except ValueError as e:
            return str(e)

    check("wrong current password is refused",
          "not your current password" in (fails_with("nope-nope-nope", "brand-new-passphrase") or ""))
    check("same password is refused",
          "must be different" in (fails_with("correct-horse-battery", "correct-horse-battery") or ""))
    check("short password is refused",
          "12 characters" in (fails_with("correct-horse-battery", "short") or ""))

    # a live session must not survive the change
    sess = auth.login(email, "correct-horse-battery")
    check("session is valid before", auth.session_user(sess) is not None)
    auth.change_password(email, "correct-horse-battery", "brand-new-passphrase")
    check("every session is revoked after", auth.session_user(sess) is None)
    check("the new password works", auth.login(email, "brand-new-passphrase") != "")

    # role escalation
    try:
        auth.update_profile(email, "Renamed")
        u = auth.get_user(email)
        check("name is editable", u["name"] == "Renamed")
        check("role did NOT change", u["role"] == "staff", u["role"])
    finally:
        auth.delete_user(email)


def test_profile_cannot_edit_role_or_email() -> None:
    print("\n5. the profile form offers no role or email field")
    html = open(os.path.join(ROOT, "app/templates/profile.html")).read()
    form = html.split('action="/profile"')[1].split("</form>")[0]
    check("no role input", 'name="role"' not in form)
    check("no email input", 'name="email"' not in form)
    check("csrf token present", 'name="csrf"' in form)
    pwform = html.split('action="/profile/password"')[1].split("</form>")[0]
    check("password form asks for the current one", 'name="current"' in pwform)
    check("password form has csrf", 'name="csrf"' in pwform)


# --- 6. the IA move ---------------------------------------------------------

def test_settings_moved_not_deleted() -> None:
    print("\n6. Settings -> Admin / Data sources")
    main = open(os.path.join(ROOT, "app/main.py")).read()
    check("/settings still answers, as a 301",
          '@app.get("/settings")' in main and "status_code=301" in main,
          "a moved page that 404s silently breaks every bookmark")
    check("/admin/sources exists", '@app.get("/admin/sources"' in main)
    check("it is owner-gated", re.search(
        r'def sources_page\(request: Request\):\s*\n\s*require_owner', main) is not None,
        "it holds broker credentials")

    base = open(os.path.join(ROOT, "app/templates/base.html")).read()
    nav = base.split("<nav>")[1].split("</nav>")[0]
    check("Settings is off the top nav", "Settings" not in nav)
    check("Profile is in the avatar menu", "/profile" in base)

    # every template link must point somewhere that exists
    dead = []
    for f in os.listdir(os.path.join(ROOT, "app/templates")):
        t = open(os.path.join(ROOT, "app/templates", f)).read()
        for href in re.findall(r'(?:href|action)="(/settings[^"]*)"', t):
            dead.append(f"{f} -> {href}")
    check("no template still links to /settings", not dead, "; ".join(dead) or "clean")


def test_no_anthropic_anywhere() -> None:
    print("\n7. the AI panel is gone, not merely hidden")
    hits = []
    for d in ("app", "core", "scripts"):
        for root, _, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if not f.endswith((".py", ".html", ".txt")):
                    continue
                p = os.path.join(root, f)
                body = open(p, errors="ignore").read()
                if re.search(r"anthropic|ai_insights", body, re.I):
                    hits.append(os.path.relpath(p, ROOT))
    check("no Anthropic import, key or panel", not hits, ", ".join(hits) or "clean")
    reqs = open(os.path.join(ROOT, "requirements.txt")).read()
    check("dependency removed", "anthropic" not in reqs)
    check("module deleted", not os.path.exists(os.path.join(ROOT, "core/ai_insights.py")))


# --- 8. the caches that made it fast ---------------------------------------

def test_caches_are_invalidated_by_writers() -> None:
    print("\n8. every cache has a writer that clears it")
    imp = open(os.path.join(ROOT, "jobs/import_schwab.py")).read()
    check("the importer calls portfolio.invalidate()",
          "portfolio.invalidate()" in imp,
          "a cached P&L that outlives an import is a wrong number on a money screen")
    main = open(os.path.join(ROOT, "app/main.py")).read()
    check("the header tape is cached", "_TAPE_CACHE" in main and "_TAPE_TTL" in main,
          "it was a live Yahoo fetch on every page")
    check("the cache is primed off the request path",
          "on_event(\"startup\")" in main and "threading.Thread" in main)


def main() -> int:
    print("Argent Ridge — platform guards")
    test_version_arithmetic()
    test_version_persists_and_only_rises()
    test_footer_never_invents_a_version()
    test_password_change_rules()
    test_profile_cannot_edit_role_or_email()
    test_settings_moved_not_deleted()
    test_no_anthropic_anywhere()
    test_caches_are_invalidated_by_writers()
    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
