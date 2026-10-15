"""The Data screen: the data files of the wiki (`datasets/*.md`) and the content of one of them,
its front-matter and its table. Read-only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from services import vocabulary as vocab
from web.deps import get_state, get_templates
from web.i18n import _
from web.state import AppState

router = APIRouter()


def attribute_text(value: object) -> str:
    """One front-matter value as one line: a mapping as `key: value` pairs, a list joined by commas."""
    if isinstance(value, dict):
        return ", ".join(f"{k}: {attribute_text(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(attribute_text(v) for v in value)
    return str(value)


@router.get("/w/{wiki_id}/data", response_class=HTMLResponse)
def data_page(request: Request, wiki_id: str, category: str = "", state: AppState = Depends(get_state),
              templates: Jinja2Templates = Depends(get_templates)):
    """The list of the data files and, beside it, the chosen one; the first one when none is chosen."""
    wiki = state.get_wiki(wiki_id).wiki
    categories = vocab.dataset_categories(wiki)
    if category and category not in categories:
        raise HTTPException(status_code=404, detail=_("The data file does not exist."))
    selected = category or (categories[0] if categories else "")
    tables = vocab.dataset_tables(wiki, category=selected) if selected else ()
    attributes = [(k, attribute_text(v)) for k, v in vocab.dataset_attributes(wiki, selected).items()] if selected else []
    context = {"wiki_id": wiki_id, "wikis": state.wiki_options(), "section": "data",
               "categories": categories, "selected": selected, "tables": tables, "attributes": attributes}
    return templates.TemplateResponse(request, "data.html", context)
