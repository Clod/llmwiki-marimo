"""The Configuración screen: the scalar settings of `wiki_config.toml` of a wiki."""

from __future__ import annotations

import html
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from domain.ingestion.ocr import ENGINE_NAMES, ENGINE_NONE
from domain.modeltest import Check, Report
from services import model_test
from services import settings as wiki_settings
from web.deps import BUSY, get_state, get_templates, page_context
from web.i18n import N_, _, format_value
from web.settings import read_models, write_models
from web.state import AppState

router = APIRouter()

SAVED = N_("Saved. The changes apply from the next question; the earlier answers do not change.")

# Form field → key of wiki_config.toml. The checkboxes are absent from the form when unticked.
FIELDS = {
    "system_prompt": "assistant.system_prompt", "suggested_prompts": "assistant.suggested_prompts",
    "enabled": "pre_retrieval.enabled", "min_coverage": "pre_retrieval.min_coverage",
    "condense_question": "pre_retrieval.condense_question",
    "threshold": "verify.threshold", "candidates": "verify.candidates",
    "ignore": "images.ignore", "describe": "images.describe",
    "min_side_px": "images.min_side_px", "min_bytes": "images.min_bytes",
    "max_per_document": "images.max_per_document",
    "number_tolerance": "verify.number_tolerance",
}
CHECKBOXES = ("enabled", "condense_question", "ignore", "describe")

MODELS_SAVED = N_("Saved in .env. The models apply from the next question and the next ingestion.")
# Form field → key of `.env`: the reading model (chat), the ingestion model, the vision
# model and the OCR engine.
MODEL_FIELDS = {
    "llm_base_url": "LLM_BASE_URL", "llm_api_key": "LLM_API_KEY", "llm_model": "LLM_MODEL",
    "wiki_llm_base_url": "WIKI_LLM_BASE_URL", "wiki_llm_api_key": "WIKI_LLM_API_KEY",
    "wiki_llm_model": "WIKI_LLM_MODEL",
    "vision_base_url": "VISION_BASE_URL", "vision_api_key": "VISION_API_KEY", "vision_model": "VISION_MODEL",
    "ocr_engine": "OCR_ENGINE",
}
# The API keys: the page never shows them, and an empty field keeps the saved key.
KEY_FIELDS = ("llm_api_key", "wiki_llm_api_key", "vision_api_key")


def error_text(code: str) -> str:
    return {
        "empty": _("Write a text."),
        "not_a_number": _("Write a number."),
        "out_of_range": _("The value must be between 0 and 1."),
        "not_positive": _("Write a whole number, 1 or more."),
        "negative": _("Write a whole number, 0 or more."),
    }.get(code, _("The value is not valid."))


def display(values: dict) -> dict:
    """The values of the keys as the form shows them: one value per field."""
    shown = {}
    for name, key in FIELDS.items():
        value = values[key]
        shown[name] = "\n".join(value) if isinstance(value, list) else value
    return shown


def models_context(state: AppState) -> dict:
    """The models block of the page: the values written in `.env`. The keys are never shown:
    only whether one is saved."""
    env_file = state.settings.env_file
    if env_file is None:
        return {"editable": False}
    saved = read_models(env_file)
    shown = {name: saved[key] for name, key in MODEL_FIELDS.items()}
    # An `.env` with no OCR_ENGINE uses the default engine, `none`.
    shown = {**shown, "ocr_engine": shown.get("ocr_engine") or ENGINE_NONE}
    return {"editable": True, "env_file": str(env_file),
            "fields": {name: value for name, value in shown.items() if name not in KEY_FIELDS},
            "key_saved": {name: bool(saved[MODEL_FIELDS[name]]) for name in KEY_FIELDS}}


def _context(state: AppState, wiki_id: str, **extra) -> dict:
    language, current, defaults = wiki_settings.read_settings(state.get_wiki(wiki_id).wiki)
    context = page_context(state, wiki_id, "", section="settings")
    context.update({"language": language, "defaults": display(defaults), "values": display(current),
                    "errors": {}, "message": None, "models": models_context(state), **extra})
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
    # A field absent from the form (other than a checkbox) keeps its value.
    submitted = {name: (name in form) if name in CHECKBOXES else str(form.get(name, ""))
                 for name in FIELDS if name in CHECKBOXES or name in form}
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
    shown = errors or (message and message["kind"] == "warn")
    if shown:
        extra["errors"] = {name: error_text(errors[key]) for name, key in FIELDS.items() if key in errors}
    context = _context(state, wiki_id, **extra)
    if shown:
        context["values"] = {**context["values"], **submitted}
    return templates.TemplateResponse(request, "settings.html", context)


# ── The models: one test button and one save button per group ─────────────────

# Group of the Models block → kind of model test (`services.model_test.KINDS`).
GROUP_KIND = {"llm": "reading", "wiki_llm": "ingestion", "vision": "vision"}
# An empty field of a group takes the value of this group, as in `WebSettings.with_models`.
_PREVIOUS = {"wiki_llm": "llm", "vision": "wiki_llm"}

NOT_EDITABLE = N_("The models cannot be edited here: there is no .env file to write.")
UNTESTED = N_("These values were not tested. Save them anyway?")
FAILED = N_("The last test of these values failed. Save them anyway?")
# The name of each check of `domain.modeltest`, as the console shows it.
CHECK_NAMES = {
    "answers": N_("It answers from the page and cites it"),
    "declines": N_("It declines when the page has no answer"),
    "uses_tools": N_("It searches the wiki with the tools"),
    "ingests": N_("It writes the summary page and the concept pages"),
    "reads_page": N_("It reads the scanned page"),
    "describes": N_("It describes an image in the format asked"),
}


def _warn(text: str) -> HTMLResponse:
    return HTMLResponse(f'<p class="warn">{html.escape(text)}</p>')


def _group_fields(group: str) -> list[str]:
    """The form fields of a group. The OCR engine belongs to the vision group."""
    fields = [f"{group}_base_url", f"{group}_api_key", f"{group}_model"]
    return fields + ["ocr_engine"] if group == "vision" else fields


def _submitted_models(form) -> dict[str, str]:
    """The model fields of the form. An OCR engine that is not one of `ocr.ENGINE_NAMES` is
    read as `none`."""
    submitted = {name: str(form.get(name, "")).strip() for name in MODEL_FIELDS}
    if submitted["ocr_engine"] not in ENGINE_NAMES:
        submitted["ocr_engine"] = ENGINE_NONE
    return submitted


def _check_group(group: str) -> None:
    if group not in GROUP_KIND:
        raise HTTPException(status_code=404, detail=_("That group of models does not exist."))


def effective(state: AppState, group: str, submitted: dict[str, str]) -> tuple[str, str, str]:
    """The URL, the key and the model a group uses with the values of the form.

    An empty field takes the value of the group it depends on: `wiki_llm` takes it from
    `llm`, and `vision` from `wiki_llm` already resolved. An empty key first takes the
    key saved in `.env` for the same field. This is the rule of `WebSettings.with_models`.
    """
    env_file = state.settings.env_file
    saved = read_models(env_file) if env_file is not None else {}
    previous = effective(state, _PREVIOUS[group], submitted) if group in _PREVIOUS else ("", "", "")
    url = submitted[f"{group}_base_url"] or previous[0]
    key = submitted[f"{group}_api_key"] or saved.get(MODEL_FIELDS[f"{group}_api_key"], "") or previous[1]
    model = submitted[f"{group}_model"] or previous[2]
    return url, key, model


def check_line(check: Check) -> str:
    """The console line of one check of a model test."""
    name = _(CHECK_NAMES.get(check.key, check.key))
    if check.passed:
        return _("✓ %(check)s", check=name)
    if not check.ran:
        return _("? %(check)s: no answer from the service (%(detail)s)", check=name, detail=check.detail)
    return _("✗ %(check)s: %(detail)s", check=name, detail=check.detail)


def closing_line(report: Report) -> str:
    """The last console line of a model test. A check that did not run does not count as a
    failure of the model."""
    n, seconds = len(report.checks), format_value(report.seconds)
    if report.passed:
        return _("✓ The model passed the %(n)s checks (%(seconds)s s). Passing is necessary, not sufficient.",
                 n=n, seconds=seconds)
    failed = sum(c.ran and not c.passed for c in report.checks)
    if failed:
        return _("✗ The model failed %(failed)s of %(n)s checks (%(seconds)s s).", failed=failed, n=n, seconds=seconds)
    return _("? The tests could not run: the service gave no answer.")


@router.post("/w/{wiki_id}/settings/models/{group}/test", response_class=HTMLResponse)
async def model_test_post(request: Request, wiki_id: str, group: str, state: AppState = Depends(get_state),
                          templates: Jinja2Templates = Depends(get_templates)):
    """Run the model test of a group with the values of the form, as a streamed operation.
    Nothing is written to `.env`. One test runs at a time in the server. The result is kept
    with the fingerprint of the values, so that saving those values can tell whether they
    passed."""
    entry = state.get_wiki(wiki_id)
    _check_group(group)
    form = await request.form()
    if state.settings.env_file is None:
        return _warn(_(NOT_EDITABLE))
    base_url, api_key, model = effective(state, group, _submitted_models(form))
    if not (base_url and api_key and model):
        return _warn(_("Write the URL, the key and the model before testing."))
    fingerprint = model_test.fingerprint(base_url, api_key, model)
    language = entry.wiki.language

    def work(progress) -> str:
        report = model_test.run(GROUP_KIND[group], base_url, api_key, model, language,
                                progress=lambda check: progress(check_line(check)))
        state.model_tests[group] = (fingerprint, report.passed)
        return closing_line(report)

    op_id = state.start_operation(None, work, lock=state.model_test_lock)
    if op_id is None:
        return _warn(_("A test is already running."))
    return templates.TemplateResponse(request, "operation.html", {"wiki_id": wiki_id, "op_id": op_id})


@router.post("/w/{wiki_id}/settings/models/{group}", response_class=HTMLResponse)
async def model_save(request: Request, wiki_id: str, group: str, state: AppState = Depends(get_state),
                     templates: Jinja2Templates = Depends(get_templates)):
    """Write the keys of one group to `.env` and use them from the next question and the
    next ingestion, without a restart. The vision group also writes the OCR engine.

    An empty API key field keeps the saved key. A URL that does not start with http:// or
    https:// writes nothing. Values that did not pass their last test, or that were not
    tested, are saved only after a confirmation (`confirm=1`)."""
    state.get_wiki(wiki_id)
    _check_group(group)
    form = await request.form()
    env_file = state.settings.env_file
    if env_file is None:
        return _warn(_(NOT_EDITABLE))
    submitted = _submitted_models(form)
    url = submitted[f"{group}_base_url"]
    if url and not re.fullmatch(r"https?://\S+", url):
        text = html.escape(_("Write a URL that starts with http:// or https://."))
        return HTMLResponse(f'<p class="field-error" role="alert">{text}</p>')
    if form.get("confirm") != "1":
        fingerprint = model_test.fingerprint(*effective(state, group, submitted))
        last = state.model_tests.get(group)
        if last != (fingerprint, True):
            message = FAILED if last is not None and last[0] == fingerprint else UNTESTED
            return templates.TemplateResponse(request, "_model_confirm.html", {
                "wiki_id": wiki_id, "group": group, "message": _(message)})
    saved = read_models(env_file)
    values = {MODEL_FIELDS[name]: submitted[name] or (saved[MODEL_FIELDS[name]] if name in KEY_FIELDS else "")
              for name in _group_fields(group)}
    try:
        write_models(env_file, values)
    except OSError as exc:
        return _warn(_("The .env file could not be written: %(error)s", error=str(exc)))
    state.set_models({**saved, **values})
    return HTMLResponse(f'<p class="notice" role="status">{html.escape(_(MODELS_SAVED))}</p>')
