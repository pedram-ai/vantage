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
# ⭐ ORDER IS THE IA. `docs/system` is the numbered functional spec and comes
# first, as its own group; the rest are reference material that happens to live
# in the repo. A flat alphabetical list buried "01 — Overview" between
# "DATA-SOURCES" and "SCHWAB-SETUP", which is how a spec stops being read.
DOC_GROUPS = [
    ("System documentation", ROOT / "docs" / "system"),
    ("Reference", ROOT / "docs"),
    ("Repo", ROOT),
]
DOC_DIRS = [d for _, d in DOC_GROUPS]
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
    for group, d in DOC_GROUPS:
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
                "group": group,
                "title": _title_of(p, text),
                "excerpt": excerpt,
                "bytes": p.stat().st_size,
                "words": len(text.split()),
                "applies_to": _applies_to(text),
                "text": text,
                "where": str(p.relative_to(ROOT)),
            })
    # Within a group keep FILENAME order, so 01..12 read as written; the README
    # is pinned first because it is the index.
    rank = {g: i for i, (g, _) in enumerate(DOC_GROUPS)}
    return sorted(out, key=lambda r: (rank.get(r["group"], 9),
                                      0 if r["name"].lower() == "readme.md" else 1,
                                      r["name"].lower()))


def _applies_to(text: str) -> str | None:
    """The `**Applies to build:** `0.002`` line, if the document carries one."""
    m = re.search(r"\*\*Applies to build:\*\*\s*`([^`]+)`", text)
    return m.group(1) if m else None


def grouped_docs(docs: list[dict] | None = None) -> list[tuple[str, list[dict]]]:
    """[(group, [doc, ...]), ...] preserving DOC_GROUPS order, empties dropped."""
    docs = list_docs() if docs is None else docs
    out = []
    for group, _ in DOC_GROUPS:
        rows = [d for d in docs if d["group"] == group]
        if rows:
            out.append((group, rows))
    return out


def search(docs: list[dict], term: str) -> list[dict]:
    """Filter by title OR body, ranked by hit count.

    ⚠ The count is what makes this useful: searching "invariant" should surface
    the document that talks about it most, not the first one alphabetically.
    A plain substring filter returns everything and ranks nothing.
    """
    term = (term or "").strip()
    if not term:
        return docs
    low = term.lower()
    hit = []
    for d in docs:
        n = d.get("text", "").lower().count(low)
        if low in d["title"].lower():
            n += 5          # a title match outranks a passing mention
        if n:
            hit.append({**d, "hits": n})
    return sorted(hit, key=lambda r: (-r["hits"], r["name"]))


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
                    "markdown": text, "where": str(rp.relative_to(ROOT)),
                    "applies_to": _applies_to(text),
                    "words": len(text.split())}
    return None


def render_markdown(md: str) -> str:
    """Small, safe markdown -> HTML. Escapes first, so repo text cannot inject.

    ⛔ THE ESCAPE HAPPENS BEFORE ANY FORMATTING, always. A renderer that formats
    first and escapes after is a renderer that can be made to emit markup.

    Supports what these documents actually use: headings, lists, fenced code,
    tables WITH a header row, blockquotes (which is how an Invariant is
    written), horizontal rules, and inline code/bold/italic/links.
    """
    import html as _html
    out, in_code, in_list, in_table, in_quote = [], False, False, False, False
    table_head_done = False

    def close_quote():
        nonlocal in_quote
        if in_quote:
            out.append("</blockquote>")
            in_quote = False

    for raw in md.split("\n"):
        line = _html.escape(raw)
        if raw.strip().startswith("```"):
            if in_code:
                out.append("</pre>"); in_code = False
            else:
                if in_list: out.append("</ul>"); in_list = False
                close_quote()
                out.append("<pre>"); in_code = True
            continue
        if in_code:
            out.append(line); continue

        # Blockquote — this is how every Invariant in docs/system is written,
        # so it gets a real rail rather than being flattened into a paragraph.
        if raw.lstrip().startswith(">"):
            body = re.sub(r"^\s*>\s?", "", raw)
            if not in_quote:
                if in_list: out.append("</ul>"); in_list = False
                if in_table: out.append("</table>"); in_table = False
                out.append("<blockquote>"); in_quote = True
            out.append(f"<p>{_inline(_html.escape(body))}</p>" if body.strip() else "")
            continue
        close_quote()
        if raw.strip().startswith("|") and raw.strip().endswith("|"):
            cells = [c.strip() for c in raw.strip().strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_table:
                # ⚠ The FIRST row of a markdown table is its header. Emitting
                # every row as <td> throws that away and the table reads as a
                # grid of unlabelled values.
                out.append("<table><thead>"); in_table = True; table_head_done = False
            tag = "td" if table_head_done else "th"
            out.append("<tr>" + "".join(
                f"<{tag}>{_inline(_html.escape(c))}</{tag}>" for c in cells) + "</tr>")
            if not table_head_done:
                out.append("</thead><tbody>"); table_head_done = True
            continue
        if in_table:
            out.append("</tbody></table>"); in_table = False
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
    if in_table: out.append("</tbody></table>")
    if in_code: out.append("</pre>")
    close_quote()
    return "\n".join(out)


def _inline(s: str) -> str:
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", s)
    # ⭐ An in-repo .md link becomes an in-app link. Without this every
    # cross-reference between documents is a dead ./04-market-data.md that
    # 404s in a browser, which is worse than no link.
    s = re.sub(r"\[([^\]]+)\]\(\.{0,2}/?(?:[\w.-]+/)*([\w.-]+)\.md(?:#[^)]*)?\)",
               lambda m: f'<a href="/admin/docs?doc={m.group(2).lower()}">{m.group(1)}</a>', s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)",
               r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    return s
