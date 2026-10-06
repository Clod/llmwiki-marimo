"""The relations screen: the graph of the wiki, and its data."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from services.wiki import graph
from web.deps import get_state, get_templates
from web.i18n import _
from web.state import AppState

router = APIRouter()


@router.get("/w/{wiki_id}/relations", response_class=HTMLResponse)
def relations(request: Request, wiki_id: str, page: str = "", depth: int = 1,
              state: AppState = Depends(get_state), templates: Jinja2Templates = Depends(get_templates)):
    """The screen. With `page`, the local graph of that page up to `depth` 1 or 2."""
    wiki = state.get_wiki(wiki_id).wiki
    title = ""
    if page:
        found = {n["id"]: n["title"] for n in graph(wiki)["nodes"]}
        if page not in found:
            raise HTTPException(status_code=404, detail=_("The page does not exist."))
        title = found[page]
    context = {"wiki_id": wiki_id, "wikis": state.wiki_options(), "section": "relations",
               "page": page, "page_title": title, "depth": 2 if depth >= 2 else 1}
    return templates.TemplateResponse(request, "relations.html", context)


@router.get("/w/{wiki_id}/relations.json")
def relations_json(wiki_id: str, state: AppState = Depends(get_state)) -> JSONResponse:
    return JSONResponse(graph(state.get_wiki(wiki_id).wiki), headers={"Cache-Control": "no-cache"})
