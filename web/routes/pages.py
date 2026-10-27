"""Pages: view, edit, delete, and the source documents."""

from __future__ import annotations

import urllib.parse
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from domain.datasets.frontmatter import split_frontmatter
from domain.ingestion.text_extract import decode_text
from domain.verify.store import State
from services import verification as verification_service
from services.wiki import (
    FIXED_PAGES,
    PageConflict,
    delete_page,
    list_pages,
    page_version,
    read_page,
    save_page,
    source_names,
    source_view,
)
from web.deps import BUSY, get_state, get_templates, page_context
from web.i18n import N_, _
from web.render import page_title, page_url, render_markdown, render_page_verified, render_source_markdown
from web.state import AppState

router = APIRouter()


def _read(state: AppState, wiki_id: str, page: str) -> str:
    """The markdown of `page`; a page id that leaves wiki/ is a 404."""
    try:
        return read_page(state.get_wiki(wiki_id).wiki, page)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=_("The page does not exist.")) from exc


def _verification_view(wiki, page: str, text: str, wiki_id: str, pages: list[str]) -> dict:
    """What the reading screen shows about the verification of `page`: the state
    line, the summary line and the rendered page with its marks.

    `status` is `none` (no verification), `outdated` (the page or a source
    changed), `valid` (the marks are shown) or `no_context` (nothing to verify
    the page against). A page outside the index has no status.
    """
    shown = verification_service.page_verification(wiki, page)
    view: dict = {"status": None, "unsupported": 0, "checked": 0, "html": None}
    if shown is None:
        return view
    if not shown.has_context:
        view["status"] = "no_context"
    elif shown.state is State.VALID:
        view["status"] = "valid"
        stored = shown.stored
        weak = {(s.start, s.end) for s in stored.unsupported(shown.threshold)}
        marks = [(s.start, s.end, None if (s.start, s.end) in weak else PurePosixPath(s.source or "").name or None)
                 for s in stored.sentences]
        # A supported sentence with no recorded source cannot occur (a probability at or
        # above the threshold comes from a passage); the guard keeps the tooltip honest.
        marks = [m for m in marks if m[2] is not None or (m[0], m[1]) in weak]
        view.update(unsupported=len(weak), checked=stored.checked,
                    html=render_page_verified(wiki_id, text, page, marks, pages, source_names(wiki)))
    else:
        view["status"] = "outdated" if shown.state is State.OUTDATED else "none"
    return view


@router.get("/w/{wiki_id}/home", response_class=HTMLResponse)
def home(request: Request, wiki_id: str, state: AppState = Depends(get_state),
         templates: Jinja2Templates = Depends(get_templates)):
    """The Home screen: the logo and the name of the wiki, beside the chat. No page is
    open, so a question asked here reaches pre-retrieval without `open_page` (node Q5a)."""
    context = page_context(state, wiki_id, "")
    context.update(home=True, title=wiki_id, kind=N_("Home"), deletable=False, html="")
    return templates.TemplateResponse(request, "page.html", context)


@router.get("/w/{wiki_id}/pages/{page:path}", response_class=HTMLResponse)
def view_page(request: Request, wiki_id: str, page: str, deleted: str = "", notice: str = "",
              state: AppState = Depends(get_state), templates: Jinja2Templates = Depends(get_templates)):
    text = _read(state, wiki_id, page)
    context = page_context(state, wiki_id, page)
    wiki = state.get_wiki(wiki_id).wiki
    html = render_markdown(wiki_id, text, page, context["pages"], source_names(wiki)) if text else ""
    check = _verification_view(wiki, page, text, wiki_id, context["pages"]) if text else {"status": None}
    context.update(
        deleted=deleted, notice=notice, deletable=page not in FIXED_PAGES,
        html=check.get("html") or html, check=check,
        title=page_title(text, page) if text else page)
    return templates.TemplateResponse(request, "page.html", context, status_code=200 if text else 404)


@router.post("/w/{wiki_id}/verify/{page:path}")
def verify(wiki_id: str, page: str, state: AppState = Depends(get_state)):
    """Verify one page against its sources and go back to it. The check runs under
    the wiki's lock; a busy wiki or a missing verifier is reported on the page."""
    def back(notice: str = "") -> RedirectResponse:
        suffix = f"?notice={urllib.parse.quote(notice)}" if notice else ""
        return RedirectResponse(page_url(wiki_id, page) + suffix, status_code=303)

    entry = state.get_wiki(wiki_id)
    if not state.verifier_available():
        return back(_("The local verifier is not installed."))
    if not entry.busy.acquire(blocking=False):
        return back(_(BUSY))
    try:
        verification_service.verify_one(entry.wiki, page, state.get_verifier())
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail=_("The page does not exist."))
    except Exception as exc:  # noqa: BLE001 — shown on the page
        return back(_("Could not verify: %(error)s", error=exc))
    finally:
        entry.busy.release()
    return back()


@router.get("/w/{wiki_id}/view/{filename}")
def source_file(request: Request, wiki_id: str, filename: str, state: AppState = Depends(get_state),
                templates: Jinja2Templates = Depends(get_templates)):
    """A source document shown in the browser (`services.wiki.source_view`): a PDF
    as it is, an office file as the PDF ingestion converted it to, a .txt as
    inline text and a .md rendered inside the application layout, read-only.
    A name outside `sources/` is a 404."""
    try:
        path = source_view(state.get_wiki(wiki_id).wiki, filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — LibreOffice missing or failing
        raise HTTPException(
            status_code=500, detail=_("%(filename)s cannot be shown: %(error)s", filename=filename, error=exc)) from exc
    # no-cache: the same document can be ingested again, and its PDF with it.
    headers = {"Cache-Control": "no-cache"}
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        # Decoded as ingestion decodes it, and sent as UTF-8: a cp1252 file would
        # otherwise show wrong characters.
        text, _encoding = decode_text(path.read_bytes(), filename)
        if suffix == ".txt":
            return Response(text, media_type="text/plain; charset=utf-8", headers=headers)
        context = page_context(state, wiki_id, "")
        context.update(source=filename, title=filename, kind=N_("Source document"), deletable=False,
                       path_label=f"sources/{filename}",
                       html=render_source_markdown(text) or f'<p class="missing">{_("The document is empty.")}</p>')
        return templates.TemplateResponse(request, "page.html", context, headers=headers)
    if suffix == ".pdf":
        return FileResponse(path, media_type="application/pdf", content_disposition_type="inline",
                            filename=Path(filename).with_suffix(".pdf").name, headers=headers)
    return FileResponse(path, media_type="application/octet-stream", filename=path.name, headers=headers)


@router.get("/w/{wiki_id}/edit/{page:path}", response_class=HTMLResponse)
def edit_page(request: Request, wiki_id: str, page: str, error: str = "",
              state: AppState = Depends(get_state), templates: Jinja2Templates = Depends(get_templates)):
    text = _read(state, wiki_id, page)
    if not text:
        raise HTTPException(status_code=404, detail=_("The page does not exist."))
    _block, body = split_frontmatter(text)
    context = page_context(state, wiki_id, page)
    wiki = state.get_wiki(wiki_id).wiki
    context.update(body=body.strip(), version=page_version(wiki, page), error=error,
                   preview=render_markdown(wiki_id, body, page, context["pages"], source_names(wiki)))
    return templates.TemplateResponse(request, "edit.html", context)


@router.post("/w/{wiki_id}/preview/{page:path}", response_class=HTMLResponse)
def preview_edit(wiki_id: str, page: str, body: str = Form(""), state: AppState = Depends(get_state)):
    """The editor's live preview: the markdown the user is typing, rendered as the page
    will look (`web/render.py`), with its links relative to `page`. Nothing is saved."""
    wiki = state.get_wiki(wiki_id).wiki
    return render_markdown(wiki_id, body, page, list_pages(wiki), source_names(wiki))


@router.post("/w/{wiki_id}/edit/{page:path}")
def save_edit(wiki_id: str, page: str, body: str = Form(...), version: str = Form(""),
              state: AppState = Depends(get_state)):
    wiki = state.get_wiki(wiki_id).wiki
    try:
        save_page(wiki, page, body, int(version) if version else None)
    except PageConflict as exc:
        # `quote`, not `html.escape`: the text goes into a URL.
        return RedirectResponse(f"/w/{wiki_id}/edit/{page}?error={urllib.parse.quote(str(exc))}",
                                status_code=303)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=_("The page does not exist.")) from exc
    return RedirectResponse(page_url(wiki_id, page), status_code=303)


@router.post("/w/{wiki_id}/delete/{page:path}")
def delete(wiki_id: str, page: str, title: str = Form(""), state: AppState = Depends(get_state)):
    if page in FIXED_PAGES:
        raise HTTPException(status_code=400, detail=_("The page “%(page)s” is fixed and cannot be deleted.", page=page))
    try:
        existed = delete_page(state.get_wiki(wiki_id).wiki, page)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=_("The page does not exist.")) from exc
    if not existed:
        raise HTTPException(status_code=404, detail=_("The page does not exist."))
    return RedirectResponse(f"/w/{wiki_id}/pages/overview?deleted={urllib.parse.quote(title or page)}",
                            status_code=303)
