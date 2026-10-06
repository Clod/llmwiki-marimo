"""History: the commits of the wiki, going back to one, and the history of one page."""

from __future__ import annotations

import html
import posixpath

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from domain.rollback.revert import DirtyWiki, IndexUnavailable, RevertError
from services import wiki as wiki_service
from services.wiki import Commit, Wiki
from web.deps import BUSY, get_state, get_templates, page_context
from web.i18n import N_, _, ngettext
from web.render import _EXTERNAL, _MD_LINK, line_diff, page_title, render_markdown
from web.state import AppState

router = APIRouter()

# Progress lines of the revert that the domain writes in English: each prefix is a message id, so a line
# that starts with one is shown in the interface language and any other line passes through unchanged.
_PROGRESS = (
    N_("⏪ Restoring the pages of "),
    N_("🗄 Restoring the index snapshot"),
    N_("🗄 No snapshot to use: rebuilding the index from the files"),
)

_INDEX_PROBLEM = {
    "java": N_("This point has no index copy, and rebuilding it needs Java, which is not installed."),
    "libreoffice": N_("This point has no index copy, and rebuilding it needs LibreOffice for the office documents "
                      "of sources/, which is not installed."),
    "nothing_extracted": N_("This point has no index copy, and the text of no source could be extracted "
                            "to rebuild it."),
    "rebuild_failed": N_("The index of this point could not be restored."),
}


def _wiki(state: AppState, wiki_id: str) -> Wiki:
    return state.get_wiki(wiki_id).wiki


def _commit(wiki: Wiki, sha: str) -> Commit:
    """The commit `sha` among those that touched wiki/; anything else is a 404."""
    try:
        full = wiki_service.commit_id(wiki, sha)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=_("That point of the history does not exist.")) from exc
    for commit in wiki_service.history(wiki):
        if commit.sha == full:
            return commit
    raise HTTPException(status_code=404, detail=_("That point of the history does not exist."))


def _list_context(wiki: Wiki, wiki_id: str) -> dict:
    return {"wiki_id": wiki_id, "commits": wiki_service.history(wiki), "dirty": wiki_service.uncommitted(wiki)}


@router.get("/w/{wiki_id}/history", response_class=HTMLResponse)
def history_page(request: Request, wiki_id: str, state: AppState = Depends(get_state),
                 templates: Jinja2Templates = Depends(get_templates)):
    context = page_context(state, wiki_id, "", section="history")
    context.update(_list_context(_wiki(state, wiki_id), wiki_id))
    return templates.TemplateResponse(request, "history.html", context)


@router.get("/w/{wiki_id}/history/list", response_class=HTMLResponse)
def history_list(request: Request, wiki_id: str, state: AppState = Depends(get_state),
                 templates: Jinja2Templates = Depends(get_templates)):
    """The list alone: the page asks for it again when a revert ends."""
    return templates.TemplateResponse(request, "_history_list.html", _list_context(_wiki(state, wiki_id), wiki_id))


@router.get("/w/{wiki_id}/history/commit/{sha}/confirm", response_class=HTMLResponse)
def revert_confirm(request: Request, wiki_id: str, sha: str, state: AppState = Depends(get_state),
                   templates: Jinja2Templates = Depends(get_templates)):
    """The confirmation of a revert, shown under the commit: what changes, how the
    index comes back, or why the revert is refused."""
    wiki = _wiki(state, wiki_id)
    commit = _commit(wiki, sha)
    plan = wiki_service.revert_plan(wiki, commit.sha)
    if plan.problem_code == "index":
        reason = _(_INDEX_PROBLEM[plan.index_code]) if plan.index_code in _INDEX_PROBLEM else plan.problem
    elif plan.problem_code == "dirty":
        n = len(plan.files)
        reason = ngettext("There is %(n)s unsaved change in wiki/ and going back would lose it.",
                          "There are %(n)s unsaved changes in wiki/ and going back would lose them.", n)
    elif plan.problem_code == "unavailable":
        reason = _("The wiki has no history: WIKI_AUTOCOMMIT is 0 or git is not available.")
    else:
        reason = plan.problem
    return templates.TemplateResponse(request, "_history_confirm.html", {
        "wiki_id": wiki_id, "commit": commit, "plan": plan, "reason": reason})


def _refusal(exc: RevertError) -> str:
    if isinstance(exc, DirtyWiki):
        return _("Did not go back: there are unsaved changes in wiki/ and they would be lost. Files: %(files)s",
                 files=", ".join(exc.files))
    if isinstance(exc, IndexUnavailable):
        reason = _(_INDEX_PROBLEM[exc.code]) if exc.code in _INDEX_PROBLEM else exc.reason
        return reason + " " + _("Nothing was changed: the pages and the index stay as they were.")
    return _("Did not go back: %(error)s", error=exc)


@router.post("/w/{wiki_id}/history/commit/{sha}/revert", response_class=HTMLResponse)
def revert_post(request: Request, wiki_id: str, sha: str, state: AppState = Depends(get_state),
                templates: Jinja2Templates = Depends(get_templates)):
    """Go back to a commit as a streamed operation under the wiki's lock. The checks
    run first, so a refusal answers at once and never takes the lock."""
    entry = state.get_wiki(wiki_id)
    commit = _commit(entry.wiki, sha)
    plan = wiki_service.revert_plan(entry.wiki, commit.sha)
    if plan.problem:
        refusal = (DirtyWiki(list(plan.files)) if plan.problem_code == "dirty"
                   else IndexUnavailable(plan.index_code, plan.problem) if plan.problem_code == "index"
                   else RevertError(plan.problem))
        return HTMLResponse(f'<p class="warn" role="alert">{html.escape(_refusal(refusal))}</p>')

    def translate(line: str) -> str:
        for prefix in _PROGRESS:
            if line.startswith(prefix):
                return _(prefix) + line[len(prefix):]
        return line

    def work(progress):
        try:
            result = wiki_service.revert(entry.wiki, commit.sha, progress=lambda line: progress(translate(line)))
        except RevertError as exc:
            raise RuntimeError(_refusal(exc)) from exc
        index = _("restored from its copy") if result.db_via == "snapshot" else _("rebuilt from the files")
        recorded = (" " + _("Recorded as point %(sha)s.", sha=result.commit_sha[:7]) if result.commit_sha
                    else " " + _("The pages were already identical, so no point was added."))
        return (_("🏁 The wiki went back to point %(sha)s: %(pages)s pages; index %(index)s.",
                  sha=result.target_sha[:7], pages=result.restored_pages, index=index) + recorded)

    op_id = state.start_operation(entry, work)
    if op_id is None:
        return HTMLResponse(f'<p class="warn">{html.escape(_(BUSY))}</p>')
    return templates.TemplateResponse(request, "operation.html", {"wiki_id": wiki_id, "op_id": op_id})


# ── History of one page ──────────────────────────────────────────────────────

def _old_links(wiki_id: str, text: str, page: str, now: set[str], then: set[str]) -> str:
    """The links of an old version, made to work. A link to a page that still exists is left
    to `render_markdown`; one to a page that only existed then opens that page's history; one
    to a page that never existed in the wiki is reduced to its label."""
    base_dir = posixpath.dirname(page)

    def fix(match):
        label, target = match.group(1), match.group(2)
        if target.startswith(_EXTERNAL):
            return match.group(0)
        resolved = posixpath.normpath(posixpath.join(base_dir, target.split("#", 1)[0].removesuffix(".md")))
        if resolved in now:
            return match.group(0)
        return f"[{label}](/w/{wiki_id}/history/page/{resolved})" if resolved in then else label

    return _MD_LINK.sub(fix, text)


def _page_view(state: AppState, wiki_id: str, page: str) -> tuple[Wiki, list[Commit]]:
    wiki = _wiki(state, wiki_id)
    try:
        return wiki, wiki_service.page_history(wiki, page)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=_("The page does not exist.")) from exc


@router.get("/w/{wiki_id}/history/page/{page:path}/at/{sha}", response_class=HTMLResponse)
def page_version(request: Request, wiki_id: str, page: str, sha: str, state: AppState = Depends(get_state),
                 templates: Jinja2Templates = Depends(get_templates)):
    """A page as a commit had it, read-only and rendered as on the reading screen."""
    wiki, commits = _page_view(state, wiki_id, page)
    try:
        text = wiki_service.page_at(wiki, page, sha)
        full = wiki_service.commit_id(wiki, sha)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=_("That point of the history does not exist.")) from exc
    commit = next((c for c in commits if c.sha == full), None) or _commit(wiki, full)
    pages = wiki_service.list_pages(wiki)
    context = page_context(state, wiki_id, "", section="history")
    context.update(
        page=page, commit=commit, exists_then=bool(text), exists_now=page in pages,
        title=page_title(text, page) if text else page,
        html=render_markdown(
            wiki_id, _old_links(wiki_id, text, page, set(pages), set(wiki_service.pages_at(wiki, full))),
            page, pages, wiki_service.source_names(wiki)) if text else "")
    return templates.TemplateResponse(request, "history_version.html", context, status_code=200 if text else 404)


@router.get("/w/{wiki_id}/history/page/{page:path}/diff/{sha}", response_class=HTMLResponse)
def page_diff(request: Request, wiki_id: str, page: str, sha: str, state: AppState = Depends(get_state),
              templates: Jinja2Templates = Depends(get_templates)):
    """The lines that differ between a commit's version of the page and the current one."""
    wiki, _commits = _page_view(state, wiki_id, page)
    try:
        old = wiki_service.page_at(wiki, page, sha)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=_("That point of the history does not exist.")) from exc
    new = wiki_service.read_page(wiki, page)
    return templates.TemplateResponse(request, "_history_diff.html", {
        "rows": line_diff(old, new), "exists_now": bool(new), "exists_then": bool(old)})


@router.get("/w/{wiki_id}/history/page/{page:path}", response_class=HTMLResponse)
def page_history(request: Request, wiki_id: str, page: str, state: AppState = Depends(get_state),
                 templates: Jinja2Templates = Depends(get_templates)):
    """The commits that touched one page, newest first."""
    wiki, commits = _page_view(state, wiki_id, page)
    current = wiki_service.read_page(wiki, page)
    if not commits and not current:
        raise HTTPException(status_code=404, detail=_("The page does not exist."))
    context = page_context(state, wiki_id, "", section="history")
    context.update(page=page, commits=commits, exists_now=bool(current),
                   title=page_title(current, page) if current else page)
    return templates.TemplateResponse(request, "history_page.html", context)
