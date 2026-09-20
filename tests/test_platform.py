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
    test_system_docs_are_complete_and_linked()
    test_docs_renderer()
    test_search_ranks_by_hits()
    test_every_page_carries_the_build_stamp()
    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        return 1
    print("All checks passed.")
    return 0




# --- 9. system documentation ------------------------------------------------

def test_system_docs_are_complete_and_linked() -> None:
    print("\n9. system documentation")
    from core import docs_store as D

    docs = D.list_docs()
    system = [d for d in docs if d["group"] == "System documentation"]
    check("the numbered spec exists", len(system) >= 13, f"{len(system)} documents")

    # every one carries the build it was reviewed against
    missing = [d["name"] for d in system if not d.get("applies_to")]
    check("every system doc stamps its build", not missing, ", ".join(missing) or "all stamped")

    # ⛔ A cross-reference that 404s is worse than no link. Every in-repo .md
    # link must resolve to a document this page can actually open.
    slugs = {d["slug"] for d in docs}
    dead = []
    for d in docs:
        for s in re.findall(r"/admin/docs\?doc=([\w-]+)", D.render_markdown(d.get("text", ""))):
            if s not in slugs:
                dead.append(f"{d['slug']} -> {s}")
    check("no dead cross-links", not dead, "; ".join(dead) or "all resolve")

    # the index groups in a deliberate order, spec first
    groups = [g for g, _ in D.grouped_docs(docs)]
    check("System documentation is the first group",
          groups and groups[0] == "System documentation", str(groups))


def test_docs_renderer() -> None:
    print("\n10. the renderer escapes first, then formats")
    from core import docs_store as D

    evil = D.render_markdown('<script>alert(1)</script>\n\n[x](javascript:alert(1))')
    check("script tags cannot survive", "<script" not in evil)

    md = "> **Invariant:** a rule.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nSee [x](./01-overview.md)."
    h = D.render_markdown(md)
    check("blockquotes render as blockquotes", "<blockquote>" in h)
    check("the first table row is a header", "<thead>" in h and "<th>A</th>" in h)
    check("in-repo links become in-app links", '/admin/docs?doc=01-overview' in h)

    # ⛔ MUTATION PROOF. The bug is `tag = "td"` unconditionally, which makes
    # every row a body row. The property that separates correct from broken is
    # that EXACTLY ONE row is a header — not that any <th> exists at all, which
    # was this check's first, useless form: it asserted a single-row table has
    # no header, and a single row IS the header, so it failed against correct
    # code. A proof that fails on working code proves nothing about the bug.
    three = D.render_markdown("| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |")
    check("(proof) exactly one header row, two body rows",
          three.count("<th>") == 2 and three.count("<td>") == 4,
          f"{three.count('<th>')} th, {three.count('<td>')} td")
    check("(proof) the header is the FIRST row",
          three.index("<th>A</th>") < three.index("<td>1</td>"))


def test_search_ranks_by_hits() -> None:
    print("\n11. doc search ranks, it does not merely filter")
    from core import docs_store as D
    docs = D.list_docs()
    res = D.search(docs, "invariant")
    check("finds documents", len(res) > 1, f"{len(res)} match")
    check("results carry a hit count", all(r.get("hits") for r in res))
    check("ranked descending",
          all(res[i]["hits"] >= res[i + 1]["hits"] for i in range(len(res) - 1)),
          " > ".join(str(r["hits"]) for r in res[:4]))
    check("a term nobody uses returns nothing",
          D.search(docs, "zzzznotaword") == [])
    check("empty search returns everything", len(D.search(docs, "")) == len(docs))


def test_every_page_carries_the_build_stamp() -> None:
    print("\n12. the build stamp reaches signed-out pages too")
    main = open(os.path.join(ROOT, "app/main.py")).read()
    # ⛔ A route that skips _ctx has no build_footer and renders "dev build" on
    # a real deploy -- a wrong version, which is what the stamp exists to stop.
    bare = re.findall(r'TemplateResponse\(\s*request,\s*"[a-z_]+\.html",\s*\{', main, re.S)
    check("no route builds its context by hand", not bare, f"{len(bare)} found")
    shell = open(os.path.join(ROOT, "app/templates/shell.html")).read()
    check("the signed-out shell has a footer", "shellfoot" in shell)
    base = open(os.path.join(ROOT, "app/templates/base.html")).read()
    check("the signed-in shell has a footer", "sitefoot" in base)

if __name__ == "__main__":
    sys.exit(main())
