"""Markdown rendering with every internal reference rewritten to application URLs.

The HTML is sanitized with an allow-list (`sanitize_html`). No FastAPI import: the module is tested alone (`tests/web/test_render.py`).
It needs `base/` on `sys.path`, as every module that imports `domain`.
"""

from __future__ import annotations

import difflib
import posixpath
import re
import urllib.parse
from collections.abc import Callable, Collection
from dataclasses import dataclass

import markdown
import nh3

from domain.datasets.frontmatter import parse_frontmatter, split_frontmatter
from web.i18n import _, ngettext

_MD_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")
# A bare page reference in a chat answer: `/wiki/concepts/x.md`,
# `wiki/concepts/x.md` or `x.md`, the forms `ensure_citation` writes
# (`domain/chat/postprocess.py`). It is applied only outside markdown links
# (`_outside_links`), so a reference in parentheses, `(Referencia: wiki/x.md)`,
# is linked too.
_BARE_MD = re.compile(r"(?<![\w/\[])((?:/?wiki/)?[\w\-./]+\.md)(?![\w\]])")
_EXTERNAL = ("http:", "https:", "mailto:", "#")


# The allow-list of the HTML that reaches the browser. The tags are nh3's
# defaults (no script, style, iframe, form or object); the attributes are the
# ones the markdown renderer writes: link href and title, image src, alt and
# title, the language class of a fenced code block, and the column alignment of a
# table (the only style property kept). Only http, https and mailto links
# survive; a relative link, such as the application URLs, is kept.
_ALLOWED_ATTRIBUTES = {
    **{tag: set(attrs) for tag, attrs in nh3.ALLOWED_ATTRIBUTES.items()},
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "code": {"class"},
    "th": {"style", "align", "colspan", "rowspan", "scope"},
    "td": {"style", "align", "colspan", "rowspan"},
}
_ALLOWED_SCHEMES = {"http", "https", "mailto"}


def sanitize_html(html: str) -> str:
    """`html` reduced to the allow-list: no script, event handler or `javascript:` link."""
    return nh3.clean(
        html,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_ALLOWED_SCHEMES,
        filter_style_properties={"text-align"},
        link_rel=None,
        clean_content_tags={"script", "style"},
    )


def page_url(wiki_id: str, page: str) -> str:
    return f"/w/{wiki_id}/pages/{page}"


def source_url(wiki_id: str, filename: str) -> str:
    return f"/w/{wiki_id}/view/{urllib.parse.quote(filename)}"


def _outside_links(text: str, fn: Callable[[str], str]) -> str:
    """Apply `fn` to the parts of `text` that are not already markdown links."""
    parts, last = [], 0
    for match in _MD_LINK.finditer(text):
        parts.append(fn(text[last:match.start()]))
        parts.append(match.group(0))
        last = match.end()
    parts.append(fn(text[last:]))
    return "".join(parts)


def render_markdown(wiki_id: str, text: str, page: str | None, pages: Collection[str],
                    sources: Collection[str] = frozenset()) -> str:
    """Render markdown to HTML with every internal reference pointing into the app.

    `page` is the id of the page being rendered, or None for a chat answer.
    A link in a page is relative to that page's directory; a link in a chat
    answer is `/wiki/...md` or relative to wiki/. A bare page reference
    (`/wiki/concepts/x.md`, `wiki/concepts/x.md` or `x.md`), as in the
    `Referencia:` line and in the sources list of a page saved from the chat,
    becomes a link too. In a chat answer a bare file name becomes a link only
    when exactly one page has that name. The name of a source document, such as
    `10 Plazos Fijos.docx`, becomes a link to that document, which opens in a
    new tab; it stays a source when a page has the same name (`notas.md`). A link to an external address, a mail address or an anchor stays
    untouched.
    """
    pages = set(pages)
    sources = set(sources)
    _block, body = split_frontmatter(text)
    base_dir = posixpath.dirname(page) if page else ""

    def resolve(target: str) -> str | None:
        if target.startswith(_EXTERNAL):
            return None
        clean = target.split("#", 1)[0].removesuffix(".md").lstrip("/")
        if clean.startswith("wiki/"):
            candidate = clean[len("wiki/"):]
        elif "/" not in clean and page is None:
            by_name = [p for p in pages if p.rsplit("/", 1)[-1] == clean]
            candidate = by_name[0] if len(by_name) == 1 else clean
        else:
            candidate = posixpath.normpath(posixpath.join(base_dir, clean))
        return candidate if candidate in pages else None

    def link(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        resolved = resolve(target)
        return f"[{label}]({page_url(wiki_id, resolved)})" if resolved else match.group(0)

    def bare(match: re.Match[str]) -> str:
        # A bare name that is a file of sources/ is that source, even when a page
        # has the same file name (the summary of `notas.md` is `notas.md` too).
        if match.group(1) in sources:
            return match.group(1)
        resolved = resolve(match.group(1))
        return f"[{match.group(1)}]({page_url(wiki_id, resolved)})" if resolved else match.group(1)

    body = _MD_LINK.sub(link, body)
    body = _outside_links(body, lambda part: _BARE_MD.sub(bare, part))
    if sources:
        names = re.compile("|".join(re.escape(n) for n in sorted(sources, key=len, reverse=True)))
        body = _outside_links(body, lambda part: names.sub(
            lambda m: f"[{m.group(0)}]({source_url(wiki_id, m.group(0))})", part))
    # Sanitize first: the target and rel of a source link are ours, added below,
    # and the text of a page or an answer cannot set them.
    rendered = sanitize_html(markdown.markdown(body, extensions=["tables", "fenced_code"]))
    return rendered.replace(
        f'<a href="/w/{wiki_id}/view/',
        f'<a target="_blank" rel="noopener" title="{_("Open the source document in a new tab")}" '
        f'href="/w/{wiki_id}/view/')


def render_source_markdown(text: str) -> str:
    """A .md source document as HTML, as it is: no link of the text is rewritten
    and its front-matter block is left out. Raw HTML passes through, as in
    `render_markdown`."""
    _block, body = split_frontmatter(text)
    return markdown.markdown(body, extensions=["tables", "fenced_code"])


def page_title(text: str, page: str) -> str:
    """The page's title: the front-matter `title`, else its first H1, else its id."""
    block, body = split_frontmatter(text or "")
    if block:
        try:
            title = parse_frontmatter(block).get("title")
            if title:
                return str(title)
        except ValueError:
            pass
    heading = re.search(r"^#\s+(.+)$", body, re.M)
    return heading.group(1).strip() if heading else page.rsplit("/", 1)[-1].replace("-", " ")


@dataclass(frozen=True)
class DiffRow:
    """One line of a line diff. `sign` is " " (unchanged), "-" (only in the old text),
    "+" (only in the new text) or "…" (a run of unchanged lines left out; `text` says how many)."""

    sign: str
    text: str


def _unchanged(n: int) -> str:
    return ngettext("%(n)s unchanged line", "%(n)s unchanged lines", n)


def line_diff(old: str, new: str, context: int = 3) -> list[DiffRow]:
    """A line diff of `old` against `new`, with `context` unchanged lines around each change
    and longer unchanged runs collapsed. An empty list means the two texts are identical."""
    a, b = old.splitlines(), new.splitlines()
    rows: list[DiffRow] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            rows.extend(DiffRow(" ", line) for line in a[i1:i2])
        else:
            rows.extend(DiffRow("-", line) for line in a[i1:i2])
            rows.extend(DiffRow("+", line) for line in b[j1:j2])
    if all(r.sign == " " for r in rows):
        return []
    changed = [i for i, r in enumerate(rows) if r.sign != " "]
    keep = set()
    for i in changed:
        keep.update(range(max(0, i - context), min(len(rows), i + context + 1)))
    out: list[DiffRow] = []
    skipped = 0
    for i, row in enumerate(rows):
        if i in keep:
            if skipped:
                out.append(DiffRow("…", _unchanged(skipped)))
                skipped = 0
            out.append(row)
        else:
            skipped += 1
    if skipped:
        out.append(DiffRow("…", _unchanged(skipped)))
    return out
