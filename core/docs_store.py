"""Admin → Documentation.

Method from Lateral Compass (`api/src/routes.docs.ts`): documentation is the
markdown that lives in the repo, served read-only. It cannot drift from the
code because it ships in the same image.

Two kinds:
  * DOCUMENTS — the repo's own .md files (CLAUDE.md, docs/*.md)
  * CHANGE LOG — every commit, generated from git at build time by
    scripts/gen_build_info.py, so "every commit is logged" is automatic.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC_DIRS = [ROOT, ROOT / "docs"]
BUILD_INFO = ROOT / "app" / "build_info.json"


def build_info() -> dict:
    try:
        with open(BUILD_INFO) as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {"available": False, "commits": [], "short": None,
                "error": "build_info.json missing — image built without git"}


def _title_of(path: Path, text: str) -> str:
    for line in text.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem.replace("-", " ").replace("_", " ").title()


def list_docs() -> list[dict]:
    seen, out = set(), []
    for d in DOC_DIRS:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            # Finder/iCloud keeps regenerating "Name 2.md" copies of tracked
            # files. They are byte-identical duplicates and must never appear
            # as separate documents.
            if re.search(r" \d+\.md$", p.name) or p.name in seen:
                continue
            seen.add(p.name)
            try:
                text = p.read_text(errors="replace")
            except Exception:  # noqa: BLE001
                continue
            body = re.sub(r"^#.*$", "", text, count=1, flags=re.M).strip()
            excerpt = re.sub(r"[#*`>\[\]()|-]", " ", body[:400])
            excerpt = re.sub(r"\s+", " ", excerpt).strip()[:180]
            out.append({
                "slug": p.stem.lower(),
                "name": p.name,
                "title": _title_of(p, text),
                "excerpt": excerpt,
                "bytes": p.stat().st_size,
                "where": str(p.relative_to(ROOT)),
            })
    return sorted(out, key=lambda r: r["title"])


def get_doc(slug: str) -> dict | None:
    slug = (slug or "").lower()
    # ⛔ resolve and confine to the repo — never let a slug walk the filesystem
    for d in DOC_DIRS:
        if not d.is_dir():
            continue
        for p in d.glob("*.md"):
            if p.stem.lower() != slug:
                continue
            rp = p.resolve()
            if ROOT not in rp.parents and rp.parent != ROOT:
                return None
            text = rp.read_text(errors="replace")
            return {"slug": slug, "name": p.name, "title": _title_of(p, text),
                    "markdown": text, "where": str(rp.relative_to(ROOT))}
    return None


def render_markdown(md: str) -> str:
    """Small, safe markdown -> HTML. Escapes first, so repo text cannot inject."""
    import html as _html
    out, in_code, in_list, in_table = [], False, False, False
    for raw in md.split("\n"):
        line = _html.escape(raw)
        if raw.strip().startswith("```"):
            if in_code:
                out.append("</pre>"); in_code = False
            else:
                if in_list: out.append("</ul>"); in_list = False
                out.append("<pre>"); in_code = True
            continue
        if in_code:
            out.append(line); continue
        if raw.strip().startswith("|") and raw.strip().endswith("|"):
            cells = [c.strip() for c in raw.strip().strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_table:
                out.append("<table>"); in_table = True
            tag = "td"
            out.append("<tr>" + "".join(
                f"<{tag}>{_inline(_html.escape(c))}</{tag}>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>"); in_table = False
        m = re.match(r"^(#{1,6})\s+(.*)$", raw)
        if m:
            if in_list: out.append("</ul>"); in_list = False
            lvl = min(len(m.group(1)) + 1, 6)
            out.append(f"<h{lvl}>{_inline(_html.escape(m.group(2)))}</h{lvl}>")
            continue
        if re.match(r"^\s*[-*]\s+", raw):
            if not in_list: out.append("<ul>"); in_list = True
            out.append("<li>" + _inline(re.sub(r"^\s*[-*]\s+", "", line)) + "</li>")
            continue
        if in_list:
            out.append("</ul>"); in_list = False
        if not raw.strip():
            out.append("")
        elif re.match(r"^\s*---+\s*$", raw):
            out.append("<hr>")
        else:
            out.append(f"<p>{_inline(line)}</p>")
    if in_list: out.append("</ul>")
    if in_table: out.append("</table>")
    if in_code: out.append("</pre>")
    return "\n".join(out)


def _inline(s: str) -> str:
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)",
               r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    return s
