"""The wiki picker."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from domain.wiki_registry import clean_path_input, is_wiki_dir, load_recent, prune_recent, push_recent, remove_recent
from domain.wiki_settings import load_wiki_language
from services.ingest import peek_stats
from services.wiki import list_pages, open_wiki
from web.deps import get_state, get_templates
from web.i18n import _
from web.render import page_url
from web.state import AppState

router = APIRouter()


def wiki_row(wiki_id: str, path: str, last_opened: str | None) -> dict:
    """One row of the picker: the wiki's language and its number of sources and pages.

    The folder is read, never opened: a wiki without an index shows no counts, and
    listing the wikis creates nothing on disk. The index is read only (`peek_stats`),
    so an index older than the schema is not migrated by a listing.
    """
    root = Path(path)
    row = {"id": wiki_id, "path": path, "language": load_wiki_language(root), "sources": None, "pages": None,
           "last_opened": last_opened is not None and str(Path(last_opened).expanduser().resolve()) == str(root.resolve())}
    db_path = root / ".llmwiki" / "index.db"
    if db_path.is_file():
        row.update(peek_stats(db_path) or {})   # an unreadable index shows no counts, the wiki still opens
    return row


@router.get("/", response_class=HTMLResponse)
def picker(request: Request, state: AppState = Depends(get_state),
           templates: Jinja2Templates = Depends(get_templates)):
    recent = prune_recent(state.settings.recent_file)          # folders that stopped being wikis leave the list
    removable = state.recent_only()
    rows = [{**wiki_row(wid, path, recent[0] if recent else None), "removable": path in removable}
            for wid, path in state.wiki_options().items()]
    return templates.TemplateResponse(request, "picker.html", {
        "wikis": rows, "home": state.settings.wiki_home})


def check_folder(raw: str) -> tuple[Path | None, str]:
    """The folder a typed path names, or ``None`` and the reason it cannot be opened.

    A valid value is an absolute path to an existing folder. The folder need not hold a
    wiki yet: opening an empty folder starts one.
    """
    text = clean_path_input(raw)
    if not text:
        return None, ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        return None, _("Write an absolute path.")
    if not path.is_dir():
        return None, _("No folder at that path.")
    return path.resolve(), ""


def browse_listing(raw: str, fallback: Path) -> dict:
    """What the folder browser shows for a typed or clicked path.

    The listing holds the subfolders of the folder, hidden ones left out, each marked
    when it looks like a wiki (``is_wiki_dir``). A path that is not an existing absolute
    folder falls back to ``fallback`` and carries the reason.
    """
    folder, error = check_folder(raw)
    if folder is None:
        folder = fallback.expanduser().resolve()
    dirs: list[dict] = []
    try:
        children = sorted((c for c in folder.iterdir() if c.is_dir() and not c.name.startswith(".")),
                          key=lambda c: c.name.casefold())
        dirs = [{"name": c.name, "path": str(c), "wiki": is_wiki_dir(c)} for c in children]
    except OSError:
        error = error or _("The folder cannot be read.")
    parent = folder.parent if folder.parent != folder else None
    return {"path": str(folder), "parent": str(parent) if parent else "", "dirs": dirs,
            "is_wiki": is_wiki_dir(folder), "error": error}


def new_folder_error(parent: Path, name: str) -> str:
    """Why ``name`` cannot be created under ``parent``, or an empty string when it can."""
    if not name:
        return _("Write a name for the folder.")
    if "/" in name or "\\" in name or name.startswith("."):
        return _("A folder name cannot contain “/” nor start with a dot.")
    if (parent / name).exists():
        return _("A folder with that name already exists.")
    return ""


@router.get("/browse", response_class=HTMLResponse)
def browse_dialog(request: Request, path: str = "", state: AppState = Depends(get_state),
                  templates: Jinja2Templates = Depends(get_templates)):
    """The folder browser, a modal dialog opened from the picker."""
    return templates.TemplateResponse(request, "_browse_dialog.html",
                                      browse_listing(path, state.settings.wiki_home))


@router.get("/browse/panel", response_class=HTMLResponse)
def browse_panel(request: Request, path: str = "", state: AppState = Depends(get_state),
                 templates: Jinja2Templates = Depends(get_templates)):
    """The content of the folder browser for another folder."""
    return templates.TemplateResponse(request, "_browse_panel.html",
                                      browse_listing(path, state.settings.wiki_home))


@router.post("/browse/mkdir", response_class=HTMLResponse)
def browse_mkdir(request: Request, parent: str = Form(...), name: str = Form(""),
                 state: AppState = Depends(get_state), templates: Jinja2Templates = Depends(get_templates)):
    """Create a folder under the folder the browser shows, and show the new folder."""
    folder, error = check_folder(parent)
    name = name.strip()
    if folder is not None:
        error = new_folder_error(folder, name)
    if folder is None or error:
        listing = browse_listing(parent, state.settings.wiki_home)
        listing["error"] = error or listing["error"]
        listing["new_name"] = name
        return templates.TemplateResponse(request, "_browse_panel.html", listing)
    try:
        (folder / name).mkdir()
    except OSError as exc:
        listing = browse_listing(str(folder), state.settings.wiki_home)
        listing["error"] = _("The folder could not be created: %(reason)s", reason=exc.strerror or str(exc))
        return templates.TemplateResponse(request, "_browse_panel.html", listing)
    return templates.TemplateResponse(request, "_browse_panel.html",
                                      browse_listing(str(folder / name), state.settings.wiki_home))


@router.post("/open")
def open_path(path: str = Form(...), create: bool = Form(False), state: AppState = Depends(get_state)):
    """Add a wiki folder to the recent list the marimo apps share, then open it.

    A folder without a wiki (`is_wiki_dir`) is turned into one only with `create`,
    the confirmation of the folder browser: the wiki is created here, before the
    folder enters the recent list. Without it, as for a path :func:`check_folder`
    refuses, nothing is created and the request returns to the picker.
    """
    folder, _reason = check_folder(path)
    if folder is None or (not is_wiki_dir(folder) and not create):
        return RedirectResponse("/", status_code=303)
    if not is_wiki_dir(folder):
        open_wiki(folder)
    recent_file = state.settings.recent_file
    push_recent(str(folder), load_recent(recent_file), recent_file=recent_file)
    for wiki_id, wiki_path in state.wiki_options().items():
        if Path(wiki_path).expanduser().resolve() == folder:
            return RedirectResponse(f"/w/{quote(wiki_id)}/", status_code=303)
    return RedirectResponse("/", status_code=303)


@router.post("/recent/remove")
def recent_remove(path: str = Form(...), state: AppState = Depends(get_state)):
    """Take a folder out of the recent list; the folder and its wiki stay on disk."""
    remove_recent(path, state.settings.recent_file)
    return RedirectResponse("/", status_code=303)


@router.get("/w/{wiki_id}/")
def wiki_home(wiki_id: str, state: AppState = Depends(get_state)):
    """The wiki on its overview page, or on its first page; a wiki without pages opens on Ingest."""
    pages = list_pages(state.get_wiki(wiki_id).wiki)
    if not pages:
        return RedirectResponse(f"/w/{quote(wiki_id)}/ingest", status_code=303)
    first = "overview" if "overview" in pages else pages[0]
    return RedirectResponse(page_url(wiki_id, first), status_code=303)
