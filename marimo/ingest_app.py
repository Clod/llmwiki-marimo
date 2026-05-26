# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "marimo",
#     "openai>=1.0.0",
#     "python-dotenv",
#     "pydantic-settings",
#     "aiosqlite",
#     "opendataloader-pdf",
#     "anywidget",
#     "traitlets",
# ]
# ///
"""
LLMWiki Ingestion App
---------------------
Upload PDFs and DOCXs, trigger ingestion, scan for changes,
and regenerate wiki pages.

Debug mode: set WIKI_DEBUG=1 in .env or environment to enable verbose logging.
"""

import marimo

__generated_with = "0.23.4"
app = marimo.App(width="full")


@app.cell
def setup():
    """Load config, configure logging, resolve paths, initialise DB."""
    import sys
    import logging
    import uuid
    import marimo as mo
    import os
    from pathlib import Path
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()

    # ── Logging ───────────────────────────────────────────────────────────────
    # Root logger stays at WARNING so Marimo internals (MARKDOWN, etc.) are silent.
    # Only the "wiki" hierarchy is elevated. Toggle with WIKI_DEBUG=1 in .env.
    debug_mode = bool(os.environ.get("WIKI_DEBUG"))
    _fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger().setLevel(logging.WARNING)          # silence everything else
    _wiki_log = logging.getLogger("wiki")
    _wiki_log.setLevel(logging.DEBUG if debug_mode else logging.INFO)
    if not _wiki_log.handlers:
        _h = logging.StreamHandler()
        _h.setFormatter(_fmt)
        _wiki_log.addHandler(_h)
    _wiki_log.propagate = False                            # don't bubble to root
    logger = logging.getLogger("wiki.app")
    logger.info("Logging ready — debug_mode=%s", debug_mode)

    # ── sys.path ──────────────────────────────────────────────────────────────
    # base/ must be inserted AFTER api/ so it takes precedence (last insert
    # at position 0 wins).
    _project_root = Path(__file__).parent.parent
    _base = str(_project_root / "base")
    if _base not in sys.path:
        sys.path.insert(0, _base)

    # Force fresh import so base/config.py wins over api/config.py
    sys.modules.pop("config", None)
    from config import settings

    logger.info("Config loaded from: %s", sys.modules["config"].__file__)
    logger.info("WIKI_PATH=%s  LLM_MODEL=%s", settings.WIKI_PATH, settings.LLM_MODEL)

    # ── Paths ─────────────────────────────────────────────────────────────────
    WORKSPACE = Path(settings.WIKI_PATH).resolve()
    DB_PATH   = str(WORKSPACE / ".llmwiki" / "index.db")
    SOURCES_DIR = WORKSPACE / "sources"
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("WORKSPACE=%s  DB=%s", WORKSPACE, DB_PATH)

    # ── Initialise DB + workspace row ─────────────────────────────────────────
    from domain.ingestion.pipeline import open_db as _open_db
    _conn = _open_db(DB_PATH)
    _row = _conn.execute("SELECT id FROM workspace LIMIT 1").fetchone()
    if not _row:
        ws_id = str(uuid.uuid4())
        _conn.execute(
            "INSERT INTO workspace (id, name, description, user_id) VALUES (?,?,?,?)",
            (ws_id, WORKSPACE.name, "", ws_id),
        )
        _conn.commit()
        logger.info("Created workspace row: %s", ws_id)
    else:
        logger.debug("Workspace row exists: %s", _row["id"])
    _conn.close()

    # ── LLM client ────────────────────────────────────────────────────────────
    wiki_base_url = settings.WIKI_LLM_BASE_URL or settings.LLM_BASE_URL
    wiki_api_key  = settings.WIKI_LLM_API_KEY  or settings.LLM_API_KEY
    llm_model     = settings.WIKI_LLM_MODEL    or settings.LLM_MODEL
    llm_client    = OpenAI(base_url=wiki_base_url, api_key=wiki_api_key)
    logger.info("LLM: model=%s  base_url=%s", llm_model, wiki_base_url)

    return (
        mo, logger, debug_mode,
        WORKSPACE, DB_PATH, SOURCES_DIR,
        llm_client, llm_model,
        wiki_base_url,
    )


@app.cell
def libreoffice_check(mo, logger, set_lo_visible):
    """Check LibreOffice availability and start a 10-second hide timer."""
    import time as _t
    from domain.ingestion import check_libreoffice

    lo = check_libreoffice()
    logger.info("LibreOffice: %s", lo or "NOT FOUND")

    def _hide():
        _t.sleep(10)
        set_lo_visible(False)

    mo.Thread(target=_hide).start()
    return (lo,)


@app.cell
def libreoffice_display(mo, lo, lo_visible, llm_model, wiki_base_url):
    """Config summary + LibreOffice callout (auto-hides after 10 s)."""
    if lo_visible():
        _lo_callout = mo.callout(
            mo.md(f"✅ **LibreOffice found:** `{lo}`"), kind="success",
        ) if lo else mo.callout(
            mo.md(
                "⚠️ **LibreOffice not found** — DOCX files will fail.\n\n"
                "- **macOS:** `brew install --cask libreoffice`\n"
                "- **Linux:** `sudo apt-get install libreoffice`\n"
                "- **Windows:** `winget install TheDocumentFoundation.LibreOffice`"
            ),
            kind="warn",
        )
    else:
        _lo_callout = mo.Html("")

    mo.vstack([
        mo.md(f"**LLM:** `{llm_model}` via `{wiki_base_url}`"),
        _lo_callout,
    ], gap=1)


@app.cell
def op_state(mo):
    """Shared log + per-operation trigger states.

    Buttons set a trigger via on_click (fast, no work done).
    Runner cells depend on the trigger and do the real work — this lets
    marimo show mo.status.spinner() while the operation runs.
    """
    log_lines, set_log_lines = mo.state([])
    ingest_trigger, set_ingest_trigger = mo.state(None)
    scan_trigger,   set_scan_trigger   = mo.state(None)
    regen_trigger,  set_regen_trigger  = mo.state(None)
    lo_visible, set_lo_visible = mo.state(True)
    get_last_handled_event, set_last_handled_event = mo.state(0)
    return (
        log_lines, set_log_lines,
        ingest_trigger, set_ingest_trigger,
        scan_trigger,   set_scan_trigger,
        regen_trigger,  set_regen_trigger,
        lo_visible, set_lo_visible,
        get_last_handled_event, set_last_handled_event,
    )


@app.cell
def upload_widget(mo):
    """Upload widget — created alone so other cells can read its .value."""
    upload = mo.ui.file(
        filetypes=[".pdf", ".docx"],
        multiple=True,
        label="Drop PDFs or DOCXs here",
    )
    return (upload,)


@app.cell
def handle_upload(upload, SOURCES_DIR, logger):
    """Save uploaded files to sources/ as soon as they are dropped."""
    saved = []
    if upload.value:
        for _uf in upload.value:
            _dest = SOURCES_DIR / _uf.name
            if not _dest.exists():
                _dest.write_bytes(_uf.contents)
                saved.append(f"✅ Saved `sources/{_uf.name}`")
                logger.info("Auto-saved: %s", _uf.name)
    return (saved,)


@app.cell
def action_buttons(mo, upload, set_ingest_trigger, set_scan_trigger, set_regen_trigger, set_log_lines):
    """Buttons that fire triggers — they do no work themselves."""
    import time as _t

    ingest_btn = mo.ui.button(
        label="⚙️ Ingest uploaded file(s)", kind="success",
        on_click=lambda _: set_ingest_trigger((_t.time(), list(upload.value))),
    )
    scan_btn = mo.ui.button(
        label="🔄 Scan sources/ for changes", kind="neutral",
        on_click=lambda _: set_scan_trigger(_t.time()),
    )
    regen_btn = mo.ui.button(
        label="🤖 Regenerate all wiki pages", kind="warn",
        on_click=lambda _: set_regen_trigger(_t.time()),
    )
    clear_btn = mo.ui.button(
        label="🗑 Clear log", kind="neutral",
        on_click=lambda _: set_log_lines([]),
    )
    return ingest_btn, scan_btn, regen_btn, clear_btn



@app.cell
def top_section(mo, upload, saved, ingest_btn, log_lines, clear_btn):
    """Upload + Activity Log side by side."""
    _upload_col = mo.vstack([
        mo.md("### 📂 Upload Documents"),
        mo.md("Supports `.pdf` and `.docx`. Files are saved to `sources/`."),
        upload,
        mo.vstack([mo.md(r) for r in saved]) if saved else mo.Html(""),
        ingest_btn,
    ], gap=2)

    _lines = log_lines()
    _log_col = mo.vstack([
        mo.hstack([mo.md("### 📋 Activity Log"), clear_btn], justify="space-between", align="center"),
        mo.md("\n".join(f"- {line}" for line in _lines)) if _lines else mo.md("_No activity yet._"),
    ], gap=1)

    mo.hstack([_upload_col, _log_col], widths=[1, 1], gap=4)


# ── Runner cells sit here so spinners appear directly below the top section ──

@app.cell
def ingest_runner(
    mo, ingest_trigger,
    WORKSPACE, DB_PATH, llm_client, llm_model,
    set_log_lines, logger,
):
    """Runs ingestion when ingest_trigger changes."""
    mo.stop(ingest_trigger() is None)

    _, _files = ingest_trigger()  # files captured at button-click time
    if not _files:
        set_log_lines(["⚠️ No files uploaded — drop a PDF or DOCX first."])
        mo.stop(True)

    try:
        from domain.ingestion import ingest_file as _if
    except Exception as _e:
        logger.error("Import error: %s", _e, exc_info=True)
        set_log_lines([f"❌ Import error: {_e}"])
        mo.stop(True)

    _msgs = []
    def _cb(msg):
        _msgs.append(msg)
        logger.info("[ingest] %s", msg)

    with mo.status.spinner(title="Ingesting documents…"):
        for _f in _files:
            _fp = WORKSPACE / "sources" / _f.name
            if not _fp.exists():
                _fp.write_bytes(_f.contents)
            _result = _if(_fp, DB_PATH, WORKSPACE, llm_client, llm_model, _cb)
            logger.info("Result: %s — %s", _result.status, _result.message)

    set_log_lines(_msgs)


@app.cell
def scan_runner(
    mo, scan_trigger,
    WORKSPACE, DB_PATH, llm_client, llm_model,
    set_log_lines, logger,
):
    """Runs scan when scan_trigger changes."""
    mo.stop(scan_trigger() is None)

    try:
        from domain.ingestion import scan_and_ingest as _sai
    except Exception as _e:
        set_log_lines([f"❌ Import error: {_e}"])
        mo.stop(True)

    _msgs = []
    def _cb(msg):
        _msgs.append(msg)
        logger.info("[scan] %s", msg)

    with mo.status.spinner(title="Scanning sources/…"):
        _sai(WORKSPACE, DB_PATH, llm_client, llm_model, _cb)

    set_log_lines(_msgs)


@app.cell
def regen_runner(
    mo, regen_trigger,
    WORKSPACE, DB_PATH, llm_client, llm_model,
    set_log_lines, logger,
):
    """Runs wiki regeneration when regen_trigger changes."""
    mo.stop(regen_trigger() is None)

    try:
        from domain.ingestion import regenerate_wiki_pages as _rwp
    except Exception as _e:
        set_log_lines([f"❌ Import error: {_e}"])
        mo.stop(True)

    _msgs = []
    def _cb(msg):
        _msgs.append(msg)
        logger.info("[regen] %s", msg)

    with mo.status.spinner(title="Regenerating wiki pages…"):
        _rwp(WORKSPACE, DB_PATH, llm_client, llm_model, _cb)

    set_log_lines(_msgs)


@app.cell
def bulk_actions(mo, scan_btn, regen_btn):
    """Bulk operation buttons."""
    mo.vstack([
        mo.md("### 🔧 Bulk Actions"),
        scan_btn,
        regen_btn,
    ], gap=2)


@app.cell
def debug_panel(mo, ingest_btn, scan_btn, upload, DB_PATH, debug_mode, logger):
    """Debug panel — only visible when WIKI_DEBUG=1."""
    from domain.ingestion.pipeline import open_db

    if not debug_mode:
        debug_view = mo.Html("")
    else:
        upload_names = [f.name for f in upload.value] if upload.value else []
        try:
            _conn_dbg = open_db(DB_PATH)
            doc_count = _conn_dbg.execute(
                "SELECT COUNT(*) FROM documents WHERE source_kind='source'"
            ).fetchone()[0]
            wiki_count = _conn_dbg.execute(
                "SELECT COUNT(*) FROM documents WHERE source_kind='wiki'"
            ).fetchone()[0]
            _conn_dbg.close()
            db_info = f"source docs: {doc_count} | wiki pages: {wiki_count}"
        except Exception as exc:
            db_info = f"DB error: {exc}"

        debug_view = mo.callout(
            mo.md(
                f"**🐛 Debug panel** (`WIKI_DEBUG=1`)\n\n"
                f"- `ingest_btn.value` = `{ingest_btn.value}`\n"
                f"- `scan_btn.value`   = `{scan_btn.value}`\n"
                f"- `upload.value`     = `{upload_names}`\n"
                f"- `DB_PATH`          = `{DB_PATH}`\n"
                f"- DB counts          = `{db_info}`"
            ),
            kind="info",
        )
        logger.debug(
            "debug_panel: ingest_btn=%s scan_btn=%s upload=%s db=%s",
            ingest_btn.value, scan_btn.value, upload_names, db_info,
        )
    return (debug_view,)


@app.cell
def sources_table_cell(mo, DB_PATH, log_lines):
    """Searchable table of indexed sources. Selection arms the delete widget."""
    import sqlite3 as _sqlite3
    from domain.ingestion.pipeline import open_db as _open_db

    log_lines()  # reactive refresh after any operation

    _conn = _open_db(DB_PATH)
    try:
        _src_rows = _conn.execute(
            "SELECT id, filename, status, page_count, parser, error_message, updated_at "
            "FROM documents WHERE source_kind='source' ORDER BY filename"
        ).fetchall()
    except _sqlite3.OperationalError:
        _src_rows = []
    _conn.close()

    _icon_map = {"ready": "✅", "processing": "⏳", "failed": "❌", "pending": "🕐"}
    _table_data = [
        {
            "id": r["id"],
            "file": r["filename"],
            "status": f"{_icon_map.get(r['status'], '?')} {r['status']}",
            "pages": r["page_count"] or "-",
            "parser": r["parser"] or "-",
            "error": (r["error_message"] or "")[:40] or "-",
            "updated": (r["updated_at"] or "")[:16],
        }
        for r in _src_rows
    ]

    sources_table = mo.ui.table(_table_data, selection="single", label="")

    mo.vstack([
        mo.md("### 📁 Sources / Delete"),
        mo.callout(
            mo.md("**Warning:** Deleting a source **permanently deletes** any wiki pages derived from it."),
            kind="warn",
        ) if _table_data else mo.Html(""),
        sources_table if _table_data else mo.md("_No indexed sources available._"),
    ], gap=2)
    return (sources_table,)


@app.cell
def also_file_check_cell(mo):
    """Secondary option — separate cell so it doesn't reset the table selection."""
    also_file_check = mo.ui.checkbox(label="Also remove file from sources/")
    also_file_check
    return (also_file_check,)


@app.cell
def delete_widget_cell(mo, sources_table):
    """Delete confirmation widget — shown only when a row is selected in the table."""
    import sys as _sys
    from pathlib import Path as _Path
    _widgets_dir = str(_Path(__file__).parent / "widgets")
    if _widgets_dir not in _sys.path:
        _sys.path.insert(0, _widgets_dir)
    from delete_confirm import DeleteConfirmWidget

    _selected = sources_table.value
    _label = _selected[0]["file"] if _selected else ""

    delete_widget = mo.ui.anywidget(DeleteConfirmWidget(label=_label, disabled=not _label))
    delete_widget if _label else mo.Html("")
    return (delete_widget,)


@app.cell
def delete_runner(
    mo, delete_widget, sources_table, also_file_check,
    get_last_handled_event, set_last_handled_event,
    WORKSPACE, DB_PATH, set_log_lines, logger,
):
    """Fires when the anywidget's event_id increments (user confirmed deletion)."""
    _event_id = delete_widget.event_id
    _last = get_last_handled_event()

    mo.stop(_event_id <= _last)
    set_last_handled_event(_event_id)

    _selected = sources_table.value or []
    _also_file = also_file_check.value

    if not _selected:
        set_log_lines(["⚠️ No source selected."])
        mo.stop(True)

    _doc_id = _selected[0]["id"]

    try:
        from domain.tools.deletion import delete_source as _ds
    except Exception as _e:
        set_log_lines([f"❌ Import error: {_e}"])
        mo.stop(True)

    with mo.status.spinner(title="Deleting source…"):
        _result = _ds(DB_PATH, WORKSPACE, _doc_id, also_delete_file=_also_file)
        logger.info("delete_source result: %s — %s", _result.action, _result.message)

    _icon = "✅" if _result.success else "❌"
    set_log_lines([f"{_icon} {_result.message}"])


if __name__ == "__main__":
    app.run()
