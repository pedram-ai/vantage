"""Admin console guards: access control, doc-path confinement, honest health.

Usage: python -m tests.test_admin
"""
from __future__ import annotations
import sys
from core import docs_store, sysheath

FAILS = []
def check(n, c, d=""):
    print(f"  {'OK  ' if c else 'FAIL'} {n}{(' — '+d) if d else ''}")
    if not c: FAILS.append(n)


def test_docs():
    print("Documentation:")
    docs = docs_store.list_docs()
    check("repo docs are listed", len(docs) >= 2, str([d['name'] for d in docs]))
    check("Finder ' 2.md' duplicates are filtered",
          not any(" 2." in d["name"] for d in docs))
    slug = docs[0]["slug"]
    check("a doc loads by slug", (docs_store.get_doc(slug) or {}).get("markdown"))
    print("  path confinement:")
    for bad in ("../../etc/passwd", "../CLAUDE", "/etc/passwd", "..%2f..%2fetc"):
        check(f"rejects {bad!r}", docs_store.get_doc(bad) is None)


def test_render():
    print("Markdown rendering:")
    html = docs_store.render_markdown("# T\n## Sub\n- item\n**bold** `code`\n")
    check("headings render", "<h2>" in html and "<h3>" in html)
    check("lists render", "<li>" in html)
    check("inline formatting", "<b>bold</b>" in html and "<code>code</code>" in html)
    evil = docs_store.render_markdown('<script>alert(1)</script>\n<img src=x onerror=y>')
    check("HTML in the source is escaped, not executed",
          "<script>" not in evil and "&lt;script&gt;" in evil, "repo text must not inject")


def test_build_info():
    print("Change log:")
    b = docs_store.build_info()
    check("build info present", isinstance(b, dict))
    if b.get("available"):
        check("commits recorded", len(b["commits"]) > 0, f"{len(b['commits'])} commits")
        c = b["commits"][0]
        check("each entry has date/subject/sha", all(c.get(k) for k in ("date","subject","short")))
    else:
        check("unavailable state is explicit, not silently empty", bool(b.get("error")),
              "an empty log must never look like 'no changes'")


def test_health_failsoft():
    print("System health fail-soft:")
    import core.sysheath as sh
    orig = sh._get
    sh._get = lambda *a, **k: None          # simulate total GCP outage
    try:
        h = sh.snapshot(force=True)
        check("still returns a payload with GCP down", isinstance(h, dict) and "cost" in h)
        check("problem is reported, not hidden", len(h["problems"]) > 0, str(h["problems"])[:70])
        check("level reflects the outage", h["level"] in ("amber", "red"), h["level"])
        check("cost marks the shape as NOT live", h["cost"]["live_resources"] is False,
              "otherwise a fallback estimate reads as measured")
    finally:
        sh._get = orig
        sh._CACHE.clear()


def mutation_proof():
    print("Mutation proof:")
    check("render escapes a crafted payload",
          "&lt;b&gt;" in docs_store.render_markdown("\\<b\\>") or
          "&lt;" in docs_store.render_markdown("<b>x</b>"))
    check("slug lookup is exact, not prefix",
          docs_store.get_doc("claud") is None, "'claud' must not match 'claude'")


def main() -> int:
    test_docs(); test_render(); test_build_info(); test_health_failsoft(); mutation_proof()
    print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)}): {FAILS}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
