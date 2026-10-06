"""What every route shares: the application state, the templates and the page
context the reading layout needs."""

from __future__ import annotations

import posixpath

from fastapi import Request
from fastapi.templating import Jinja2Templates

from domain.chat.config import load_config
from domain.tools.db import get_connection
from services.wiki import list_pages
from web.i18n import N_, _
from web.state import AppState, OpenWiki

BUSY = N_("Another operation is running on this wiki.")

# The three chat modes: value, label and tooltip. The selector of the composer and the
# line under each answer both read this list.
MODES = (
    ("pre-retrieval", N_("Pre-retrieval"),
     N_("The code searches the wiki before calling the model and passes it the pages found")),
    ("strict", N_("Strict"),
     N_("The model queries the wiki with tools; if it did not, the answer is replaced by a refusal")),
    ("streaming", N_("No verification"),
     N_("The answer appears as the model generates it. The code does not check that the answer comes from the wiki")),
)
MODE_LABELS = {value: label for value, label, _tip in MODES}

# The texts are marked here and translated where they are used (`kind_label`, `index_groups`).
_KIND = {"concepts": N_("Concept"), "summaries": N_("Summary"), "": N_("Wiki")}
_GROUP = {"concepts": N_("Concepts"), "summaries": N_("Summaries"), "": N_("General")}

# The values of `documents.status` (`database/sqlite_schema.sql`); the pill keeps the raw value as its CSS class.
_STATUS = {"pending": N_("pending"), "processing": N_("processing"), "ready": N_("ready"), "failed": N_("failed")}


def status_label(status: str) -> str:
    """A source status in the interface language; an unknown value is shown as it is."""
    return _(_STATUS[status]) if status in _STATUS else status


def get_state(request: Request) -> AppState:
    return request.app.state.web


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def index_groups(entry: OpenWiki, pages: list[str]) -> dict[str, list[tuple[str, str]]]:
    """The index column: section name → (page id, title), sorted by title."""
    with get_connection(entry.wiki.db_path) as conn:
        titles = {row["relative_path"].removeprefix("wiki/").removesuffix(".md"): row["title"]
                  for row in conn.execute(
                      "SELECT relative_path, title FROM documents WHERE source_kind = 'wiki'")}
    groups: dict[str, list[tuple[str, str]]] = {}
    for p in sorted(pages, key=lambda x: (titles.get(x) or x).lower()):
        label = titles.get(p) or p.rsplit("/", 1)[-1].replace("-", " ")
        folder = posixpath.dirname(p)
        groups.setdefault(_(_GROUP[folder]) if folder in _GROUP else folder, []).append((p, label))
    return groups


def page_context(state: AppState, wiki_id: str, page: str, section: str = "read") -> dict:
    """The context of every screen that shows the header and the index column.

    `conversation_id` is a fresh id: the page replaces it with the one the
    browser keeps for this wiki (`static/js/reader.js`).
    """
    entry = state.get_wiki(wiki_id)
    pages = list_pages(entry.wiki)
    return {"wiki_id": wiki_id, "page": page, "pages": pages,
            "groups": index_groups(entry, pages), "wikis": state.wiki_options(),
            "conversation_id": state.new_id(), "section": section,
            "kind": _KIND.get(posixpath.dirname(page), N_("Page")),
            "path_label": f"wiki/{page}.md" if page else "",
            "model": state.settings.llm_model, "modes": MODES,
            "prompts": load_config(entry.wiki.path).suggested_prompts}
