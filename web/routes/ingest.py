"""Ingestion and maintenance: one operation in a thread per wiki, its progress
streamed over SSE, and the screen that lists the sources."""

from __future__ import annotations

import asyncio
import html
import queue
import urllib.parse

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from domain.ingestion.formats import sorted_extensions
from domain.tools.reindex import NothingExtracted
from services import ingest as ingest_service
from services import wiki as wiki_service
from web.deps import BUSY, get_state, get_templates, page_context
from web.i18n import _, ngettext
from web.state import AppState, LLMNotConfigured

router = APIRouter()

_UPLOAD_SUFFIXES = tuple(sorted_extensions())
# The ingestion screen's `accept` attribute and, by `format_label`, its label ("PDF, DOCX, … or TXT").
_FORMAT_NAMES = [e.lstrip(".").upper() for e in _UPLOAD_SUFFIXES]
ACCEPT = ",".join(_UPLOAD_SUFFIXES)


def format_label() -> str:
    """The accepted formats as a sentence fragment, in the interface language."""
    return _("%(formats)s or %(last)s", formats=", ".join(_FORMAT_NAMES[:-1]), last=_FORMAT_NAMES[-1])


def summary_line(label: str, results: list) -> str:
    """The closing line of an operation that returns one result per file."""
    return _("🏁 %(label)s: %(ingested)s ingested, %(skipped)s skipped, %(failed)s failed", label=label,
             ingested=sum(r.status == "ingested" for r in results), skipped=sum(r.status == "skipped" for r in results),
             failed=sum(r.status == "failed" for r in results))


def _redirect(wiki_id: str, notice: str, screen: str = "ingest") -> RedirectResponse:
    """Back to the ingestion screen, or to the maintenance one, with a notice."""
    return RedirectResponse(f"/w/{wiki_id}/{screen}?notice={urllib.parse.quote(notice)}", status_code=303)


def _unconfigured(exc: LLMNotConfigured) -> HTMLResponse:
    return HTMLResponse(f'<p class="warn">{html.escape(str(exc))}</p>')


def _log(request: Request, templates: Jinja2Templates, wiki_id: str, op_id: str | None):
    if op_id is None:
        return HTMLResponse(f'<p class="warn">{html.escape(_(BUSY))}</p>')
    return templates.TemplateResponse(request, "operation.html", {"wiki_id": wiki_id, "op_id": op_id})


@router.get("/w/{wiki_id}/ingest", response_class=HTMLResponse)
def ingest_page(request: Request, wiki_id: str, notice: str = "", state: AppState = Depends(get_state),
                templates: Jinja2Templates = Depends(get_templates)):
    """What enters and leaves sources/: upload, scan, the sources table."""
    wiki = state.get_wiki(wiki_id).wiki
    context = page_context(state, wiki_id, "", section="ingest")
    context.update(sources=ingest_service.list_sources(wiki), notice=notice,
                   format_label=format_label(), accept=ACCEPT)
    return templates.TemplateResponse(request, "ingest.html", context)


@router.get("/w/{wiki_id}/sources", response_class=HTMLResponse)
def sources_panel(request: Request, wiki_id: str, state: AppState = Depends(get_state),
                  templates: Jinja2Templates = Depends(get_templates)):
    """The sources panel alone: Ingestar asks for it again when an operation ends."""
    wiki = state.get_wiki(wiki_id).wiki
    return templates.TemplateResponse(request, "_sources_panel.html",
                                      {"wiki_id": wiki_id, "sources": ingest_service.list_sources(wiki)})


@router.get("/w/{wiki_id}/maintain", response_class=HTMLResponse)
def maintain_page(request: Request, wiki_id: str, notice: str = "", state: AppState = Depends(get_state),
                  templates: Jinja2Templates = Depends(get_templates)):
    """What reviews or rebuilds what is already in the wiki: regeneration, lint
    and repair, stale pages, the index; and the configuration line."""
    wiki = state.get_wiki(wiki_id).wiki
    model, base_url = state.ingest_model()
    context = page_context(state, wiki_id, "", section="maintain")
    context.update(sources=ingest_service.list_sources(wiki), stale=ingest_service.list_stale_pages(wiki),
                   tools=ingest_service.tool_status(), model=model, base_url=base_url, notice=notice)
    return templates.TemplateResponse(request, "maintain.html", context)


@router.post("/w/{wiki_id}/ingest", response_class=HTMLResponse)
async def ingest_post(request: Request, wiki_id: str, files: list[UploadFile], full_repair: bool = Form(False),
                      state: AppState = Depends(get_state), templates: Jinja2Templates = Depends(get_templates)):
    """Start one ingestion in a thread and return the log that streams its progress."""
    entry = state.get_wiki(wiki_id)
    try:
        client, model, _url = state.ingest_llm()
    except LLMNotConfigured as exc:
        return _unconfigured(exc)
    uploads, refused = [], []
    labels = format_label()
    for f in files:
        if not f.filename:
            continue
        try:
            uploads.append(ingest_service.Upload(f.filename, await f.read()))
        except ingest_service.InvalidUpload as exc:
            refused.append(exc)

    def work(progress):
        for exc in refused:
            if isinstance(exc, ingest_service.UnsupportedUploadType):
                progress(_("⚠️ %(name)s: format not supported; supported formats are %(formats)s",
                           name=exc.name, formats=labels))
            else:
                progress(_("⚠️ %(name)s: invalid file name", name=exc.name or _("file")))
        results = ingest_service.ingest_uploads(entry.wiki, uploads, client, model,
                                                full_repair=full_repair, progress=progress)
        return summary_line(_("Ingestion"), results)

    return _log(request, templates, wiki_id, state.start_operation(entry, work))


@router.post("/w/{wiki_id}/scan", response_class=HTMLResponse)
def scan_post(request: Request, wiki_id: str, state: AppState = Depends(get_state),
              templates: Jinja2Templates = Depends(get_templates)):
    """Start the scan of the sources folder and return the log of its progress."""
    entry = state.get_wiki(wiki_id)
    try:
        client, model, _url = state.ingest_llm()
    except LLMNotConfigured as exc:
        return _unconfigured(exc)
    return _log(request, templates, wiki_id, state.start_operation(
        entry, lambda progress: summary_line(
            _("Scan"), ingest_service.scan_sources(entry.wiki, client, model, progress=progress))))


@router.post("/w/{wiki_id}/regenerate", response_class=HTMLResponse)
def regenerate_post(request: Request, wiki_id: str, state: AppState = Depends(get_state),
                    templates: Jinja2Templates = Depends(get_templates)):
    """Start the regeneration of the summary pages and return the log of its progress."""
    entry = state.get_wiki(wiki_id)
    try:
        client, model, _url = state.ingest_llm()
    except LLMNotConfigured as exc:
        return _unconfigured(exc)
    return _log(request, templates, wiki_id, state.start_operation(
        entry, lambda progress: summary_line(
            _("Regeneration"), ingest_service.regenerate_summaries(entry.wiki, client, model, progress=progress))))


@router.post("/w/{wiki_id}/lint", response_class=HTMLResponse)
def lint_post(request: Request, wiki_id: str, state: AppState = Depends(get_state),
              templates: Jinja2Templates = Depends(get_templates)):
    """Start the wiki-wide lint and repair and return the log of its progress."""
    entry = state.get_wiki(wiki_id)
    try:
        client, model, _url = state.ingest_llm()
    except LLMNotConfigured as exc:
        return _unconfigured(exc)

    def work(progress):
        issues = ingest_service.lint_and_repair(entry.wiki, client, model, progress=progress)
        return _("🏁 Review and repair: %(issues)s problem(s) found", issues=issues)

    return _log(request, templates, wiki_id, state.start_operation(entry, work))


def reindex_line(report) -> str:
    """The closing line of the index rebuild."""
    line = _("🏁 Index rebuilt: %(sources)s sources, %(pages)s pages, %(references)s references",
             sources=report.sources_indexed, pages=report.wiki_pages_indexed, references=report.references)
    if report.sources_failed:
        line += _("; %(failed)s sources with errors", failed=len(report.sources_failed))
    return line


@router.post("/w/{wiki_id}/reindex", response_class=HTMLResponse)
def reindex_post(request: Request, wiki_id: str, state: AppState = Depends(get_state),
                 templates: Jinja2Templates = Depends(get_templates)):
    """Start the rebuild of the index from the files of the wiki and return the log of
    its progress. It calls no model, so it needs no model configured."""
    entry = state.get_wiki(wiki_id)

    def work(progress):
        try:
            return reindex_line(wiki_service.reindex(entry.wiki, progress=progress))
        except NothingExtracted as exc:
            # The service message is English; the page shows the interface language.
            first = str(exc).split("First error: ", 1)[-1]
            raise RuntimeError(_("The text of no source could be extracted, so the current index stays as it was. "
                                 "First error: %(error)s", error=first)) from exc

    return _log(request, templates, wiki_id, state.start_operation(entry, work))


@router.post("/w/{wiki_id}/stale/delete")
def stale_delete(wiki_id: str, state: AppState = Depends(get_state)):
    entry = state.get_wiki(wiki_id)
    if not entry.busy.acquire(blocking=False):
        return _redirect(wiki_id, _(BUSY), "maintain")
    try:
        deleted, _names = ingest_service.delete_stale_pages(entry.wiki)
    finally:
        entry.busy.release()
    return _redirect(wiki_id, ngettext("Deleted %(n)s stale page.", "Deleted %(n)s stale pages.", deleted),
                     "maintain")


def _page_id(path: str) -> str:
    """"/wiki/summaries/tale.md" → "summaries/tale", as the screens name a page."""
    return path.lstrip("/").removeprefix("wiki/").removesuffix(".md")


@router.post("/w/{wiki_id}/sources/{doc_id}/delete", response_class=HTMLResponse)
def source_delete(request: Request, wiki_id: str, doc_id: str, filename: str = Form(""),
                  also_delete_file: bool = Form(False), state: AppState = Depends(get_state),
                  templates: Jinja2Templates = Depends(get_templates)):
    """Delete a source as a streamed operation: one line per step in the console."""
    entry = state.get_wiki(wiki_id)

    def work(progress) -> str:
        progress(_("🗑 Deleting the source “%(filename)s”", filename=filename))
        result = ingest_service.remove_source(entry.wiki, doc_id, also_delete_file=also_delete_file)
        if not result.success:
            raise RuntimeError(_("Could not delete the source “%(filename)s”: %(error)s",
                                 filename=filename, error=result.message))
        for page in result.deleted_pages:
            progress(_("Deleted the summary page %(page)s", page=_page_id(page)))
        if result.stale_pages:
            pages = ", ".join(_page_id(p) for p in result.stale_pages)
            progress(ngettext("%(count)s page marked as stale: %(pages)s",
                              "%(count)s pages marked as stale: %(pages)s",
                              len(result.stale_pages), count=len(result.stale_pages), pages=pages))
        if result.file_removed:
            progress(_("Removed the file from sources/"))
        return _("🏁 Deleted the source “%(filename)s”.", filename=result.filename or filename)

    return _log(request, templates, wiki_id, state.start_operation(entry, work))


@router.get("/w/{wiki_id}/ops/{op_id}/events")
async def operation_events(wiki_id: str, op_id: str, state: AppState = Depends(get_state)):
    operation = state.operations.get(op_id)
    if operation is None:
        raise HTTPException(status_code=404, detail=_("That operation has already finished."))

    async def events():
        while True:
            try:
                line = operation.lines.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.1)
                continue
            if line is None:
                state.operations.pop(op_id, None)
                yield {"event": "done", "data": ""}
                return
            yield {"event": "line", "data": f"<li>{html.escape(line)}</li>"}

    return EventSourceResponse(events())
