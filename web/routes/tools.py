"""The Tools screen: every tool the chat can use, active or not in the open wiki, and the
calculation functions behind the tools of each domain. Read-only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from domain.extensions import describe_all
from domain.extensions.description import (
    CATEGORY_EXCLUDED,
    EVIDENCE,
    NO_CATEGORY,
    NO_DATASETS,
    REPLACED,
    VERBATIM,
    ToolDescription,
)
from web.deps import get_state, get_templates
from web.i18n import _
from web.state import AppState

router = APIRouter()


def reason_text(tool: ToolDescription) -> str:
    """Why `tool` is inactive in this wiki, worded in the interface language."""
    return {
        NO_DATASETS: _("The wiki has no data files."),
        REPLACED: _("The data tools of a domain replace it."),
        NO_CATEGORY: _("No data category of the domain meets its requirements."),
        CATEGORY_EXCLUDED: _("The data category of the tool does not meet its requirements."),
    }.get(tool.reason, tool.reason)


def role_text(tool: ToolDescription) -> str:
    """What the code does with the result of `tool`."""
    return {
        EVIDENCE: _("Evidence: the code cites its result as a source."),
        VERBATIM: _("Verbatim: the code appends its result to the answer."),
    }.get(tool.role, _("The code does not cite its result."))


@router.get("/w/{wiki_id}/tools", response_class=HTMLResponse)
def tools_page(request: Request, wiki_id: str, state: AppState = Depends(get_state),
               templates: Jinja2Templates = Depends(get_templates)):
    """The generic tools, then one closed section per domain."""
    generic, domains = describe_all(state.get_wiki(wiki_id).wiki.path)
    context = {"wiki_id": wiki_id, "wikis": state.wiki_options(), "section": "tools",
               "generic": generic, "domains": domains, "reason_text": reason_text, "role_text": role_text}
    return templates.TemplateResponse(request, "tools.html", context)
