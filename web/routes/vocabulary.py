"""The Vocabulario screen: the roster, the aliases, the rejected aliases and the blacklist."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from services import vocabulary as vocab
from web.deps import BUSY, get_state, get_templates, page_context
from web.i18n import _
from web.state import AppState

router = APIRouter()

# The panel each change belongs to: its message appears there.
_PANEL = {"blacklist-add": "blacklist", "blacklist-remove": "blacklist", "alias-add": "aliases",
          "alias-remove": "aliases", "alias-reject": "aliases", "rejected-remove": "rejected"}


def _refusal(exc: vocab.VocabularyEditError) -> str:
    term = exc.term
    return {
        "empty": _("Write a term."),
        "duplicate": _("“%(term)s” is already in the list.", term=term),
        "missing": _("“%(term)s” is not in the list.", term=term),
        "covered": _("“%(term)s” is a topic the wiki covers: it cannot go to the blacklist.", term=term),
        "unknown_canonical": _("“%(term)s” is not in the roster: choose one of its names.", term=term),
        "collision": _("“%(term)s” is the name of another topic of the wiki.", term=term),
    }.get(exc.code, str(exc))


def finding_text(finding: vocab.Finding) -> str:
    """One finding of `vocabulary_check`, worded in the interface language."""
    t = finding.terms
    if finding.check == "vocab_collision" and len(t) == 3:
        return _("The alias “%(alias)s” of “%(canonical)s” is the name of “%(other)s”.",
                 alias=t[0], canonical=t[1], other=t[2])
    if finding.check == "vocab_stale" and t:
        return _("There are aliases for “%(canonical)s”, which has no page nor dataset.", canonical=t[0])
    if finding.check == "vocab_ambiguous" and len(t) >= 2:
        names = ", ".join(_("“%(name)s”", name=name) for name in t[1:])
        return _("The alias “%(alias)s” belongs to several names: %(names)s.", alias=t[0], names=names)
    if finding.check == "vocab_covered" and t:
        return _("“%(term)s” is in the blacklist, but the wiki now covers it.", term=t[0])
    return " · ".join(t)


def _context(state: AppState, wiki_id: str, **extra) -> dict:
    context = page_context(state, wiki_id, "", section="vocabulary")
    context.update({"vocabulary": vocab.read_vocabulary(state.get_wiki(wiki_id).wiki), "finding_text": finding_text,
                    "message": None, **extra})
    return context


@router.get("/w/{wiki_id}/vocabulary", response_class=HTMLResponse)
def vocabulary_page(request: Request, wiki_id: str, state: AppState = Depends(get_state),
                    templates: Jinja2Templates = Depends(get_templates)):
    return templates.TemplateResponse(request, "vocabulary.html", _context(state, wiki_id))


@router.get("/w/{wiki_id}/vocabulary/dataset", response_class=HTMLResponse)
def vocabulary_dataset(request: Request, wiki_id: str, category: str = "", key: str = "",
                       state: AppState = Depends(get_state), templates: Jinja2Templates = Depends(get_templates)):
    """The dialog with the dataset behind a roster name: a category, or every dataset
    that holds a key, with that key's row marked."""
    tables = vocab.dataset_tables(state.get_wiki(wiki_id).wiki, category=category, key=key)
    if not tables:
        raise HTTPException(status_code=404, detail=_("No dataset holds that name."))
    return templates.TemplateResponse(request, "_dataset_dialog.html",
                                      {"tables": tables, "name": key or category})


@router.post("/w/{wiki_id}/vocabulary/{op}", response_class=HTMLResponse)
def vocabulary_change(request: Request, wiki_id: str, op: str, term: str = Form(""), canonical: str = Form(""),
                      state: AppState = Depends(get_state), templates: Jinja2Templates = Depends(get_templates)):
    """Apply one change under the wiki's lock and return the screen's body again."""
    if op not in _PANEL:
        raise HTTPException(status_code=404)
    entry = state.get_wiki(wiki_id)
    panel, ok = _PANEL[op], False
    if not entry.busy.acquire(blocking=False):
        text = _(BUSY)
    else:
        try:
            if op == "blacklist-add":
                vocab.add_blacklist_term(entry.wiki, term)
            elif op == "blacklist-remove":
                vocab.remove_blacklist_term(entry.wiki, term)
            elif op == "alias-add":
                vocab.add_alias(entry.wiki, canonical, term)
            elif op == "alias-remove":
                vocab.remove_alias(entry.wiki, canonical, term)
            elif op == "alias-reject":
                vocab.reject_alias(entry.wiki, canonical, term)
            else:
                vocab.unreject_alias(entry.wiki, canonical, term)
            ok, text = True, ""
        except vocab.VocabularyEditError as exc:
            text = _refusal(exc)
        finally:
            entry.busy.release()
    message = None if ok else {"panel": panel, "text": text}
    return templates.TemplateResponse(request, "_vocabulary_body.html", _context(state, wiki_id, message=message))
