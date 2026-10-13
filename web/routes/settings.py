"""The Configuración screen: the scalar settings of `wiki_config.toml` of a wiki."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from services import settings as wiki_settings
from web.deps import BUSY, get_state, get_templates, page_context
from web.i18n import N_, _
from web.state import AppState

router = APIRouter()

SAVED = N_("Saved. The changes apply from the next question; the earlier answers do not change.")

# Form field → key of wiki_config.toml. The checkboxes are absent from the form when unticked.
FIELDS = {
    "system_prompt": "assistant.system_prompt", "suggested_prompts": "assistant.suggested_prompts",
    "enabled": "pre_retrieval.enabled", "min_coverage": "pre_retrieval.min_coverage",
    "condense_question": "pre_retrieval.condense_question",
    "threshold": "verify.threshold", "candidates": "verify.candidates",
}
CHECKBOXES = ("enabled", "condense_question")


def error_text(code: str) -> str:
    return {
        "empty": _("Write a text."),
        "not_a_number": _("Write a number."),
        "out_of_range": _("The value must be between 0 and 1."),
        "not_positive": _("Write a whole number, 1 or more."),
    }.get(code, _("The value is not valid."))


def display(values: dict) -> dict:
    """The values of the keys as the form shows them: one value per field."""
    shown = {}
    for name, key in FIELDS.items():
        value = values[key]
        shown[name] = "\n".join(value) if isinstance(value, list) else value
    return shown


def _context(state: AppState, wiki_id: str, **extra) -> dict:
    language, current, defaults = wiki_settings.read_settings(state.get_wiki(wiki_id).wiki)
    context = page_context(state, wiki_id, "", section="settings")
    context.update({"language": language, "defaults": display(defaults), "values": display(current),
                    "errors": {}, "message": None, **extra})
    return context


@router.get("/w/{wiki_id}/settings", response_class=HTMLResponse)
def settings_page(request: Request, wiki_id: str, state: AppState = Depends(get_state),
                  templates: Jinja2Templates = Depends(get_templates)):
    return templates.TemplateResponse(request, "settings.html", _context(state, wiki_id))


@router.post("/w/{wiki_id}/settings", response_class=HTMLResponse)
async def settings_save(request: Request, wiki_id: str, state: AppState = Depends(get_state),
                        templates: Jinja2Templates = Depends(get_templates)):
    """Validate and write the form under the wiki's lock; drop the cached agents so that the
    next question reads the new file. An invalid form writes nothing and is shown again."""
    form = await request.form()
    submitted = {name: (name in form) if name in CHECKBOXES else str(form.get(name, "")) for name in FIELDS}
    raw = {FIELDS[name]: value for name, value in submitted.items()}
    values, errors = wiki_settings.parse_form(raw)
    entry = state.get_wiki(wiki_id)
    message = None
    if not errors:
        if not entry.busy.acquire(blocking=False):
            message = {"kind": "warn", "text": _(BUSY)}
        else:
            try:
                wiki_settings.save_settings(entry.wiki, values)
                state.reset_agents(wiki_id)
                message = {"kind": "notice", "text": _(SAVED)}
            except wiki_settings.SettingsError as exc:
                errors = exc.errors
            finally:
                entry.busy.release()
    extra: dict = {"message": message}
    if errors or (message and message["kind"] == "warn"):
        extra["values"] = submitted
        extra["errors"] = {name: error_text(errors[key]) for name, key in FIELDS.items() if key in errors}
    return templates.TemplateResponse(request, "settings.html", _context(state, wiki_id, **extra))
