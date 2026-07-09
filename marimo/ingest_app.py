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
    from config import settings, require_llm_config

    logger.info("Config loaded from: %s", sys.modules["config"].__file__)
    logger.info("WIKI_PATH=%s  LLM_MODEL=%s", settings.WIKI_PATH, settings.LLM_MODEL)

    # ── Wiki picker defaults ──────────────────────────────────────────────────
    # WORKSPACE/DB_PATH/SOURCES_DIR are no longer constants here — they are derived
    # reactively in `wiki_context` from the active-wiki state so the wiki can be
    # switched at runtime. settings.WIKI_PATH is only the default selection.
    from domain.wiki_registry import resolve_wiki_home
    ENV_DEFAULT = str(Path(settings.WIKI_PATH).resolve())
    WIKI_HOME = resolve_wiki_home(settings.WIKI_PATH)

    # ── LLM client ────────────────────────────────────────────────────────────
    wiki_base_url = settings.WIKI_LLM_BASE_URL or settings.LLM_BASE_URL
    wiki_api_key  = settings.WIKI_LLM_API_KEY  or settings.LLM_API_KEY
    llm_model     = settings.WIKI_LLM_MODEL    or settings.LLM_MODEL
    require_llm_config(wiki_base_url, wiki_api_key, llm_model, purpose="wiki generation / ingestion")
    llm_client    = OpenAI(base_url=wiki_base_url, api_key=wiki_api_key)
    logger.info("LLM: model=%s  base_url=%s", llm_model, wiki_base_url)

    return (
        mo, logger, debug_mode,
        llm_client, llm_model, wiki_base_url,
        ENV_DEFAULT, WIKI_HOME,
    )


@app.cell
def wiki_state(mo, ENV_DEFAULT):
    """Active-wiki selection + recent-wikis list (the picker's reactive roots)."""
    from domain.wiki_registry import load_recent

    active_wiki, set_active_wiki = mo.state(ENV_DEFAULT or None)
    recent_list, set_recent_list = mo.state(load_recent())
    return active_wiki, recent_list, set_active_wiki, set_recent_list


@app.cell
def wiki_context(active_wiki, logger):
    """Derive WORKSPACE/DB_PATH/SOURCES_DIR from the active wiki; init its DB.

    Re-runs whenever the active wiki changes, so every downstream cell that
    consumes these names (ingest/scan/regen/delete runners, tables, debug panel)
    automatically retargets the new wiki — no signature changes needed.
    """
    import uuid as _uuid
    from pathlib import Path as _Path
    from domain.ingestion.pipeline import open_db as _open_db

    WORKSPACE = _Path(active_wiki()).resolve()
    DB_PATH = str(WORKSPACE / ".llmwiki" / "index.db")
    SOURCES_DIR = WORKSPACE / "sources"
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    # Resolve this wiki's content language from wiki_config.toml ([wiki].language,
    # → "en" when absent). Threaded into the ingest/scan/regen runners below so a
    # Spanish wiki is generated in Spanish; re-resolved whenever the wiki changes.
    from domain.wiki_settings import load_wiki_language as _load_wiki_language
    WIKI_LANG = _load_wiki_language(WORKSPACE)

    _conn = _open_db(DB_PATH)
    _row = _conn.execute("SELECT id FROM workspace LIMIT 1").fetchone()
    if not _row:
        _ws_id = str(_uuid.uuid4())
        _conn.execute(
            "INSERT INTO workspace (id, name, description, user_id) VALUES (?,?,?,?)",
            (_ws_id, WORKSPACE.name, "", _ws_id),
        )
        _conn.commit()
        logger.info("Created workspace row: %s", _ws_id)
    _conn.close()
    logger.info("Active wiki — WORKSPACE=%s  DB=%s  LANG=%s", WORKSPACE, DB_PATH, WIKI_LANG)
    return WORKSPACE, DB_PATH, SOURCES_DIR, WIKI_LANG


@app.cell
def wiki_picker(mo, active_wiki, recent_list, set_active_wiki, WIKI_HOME):
    """The picker — one dropdown over discovered + recent wikis."""
    from domain.wiki_registry import merge_options, short_label

    _opts = merge_options(WIKI_HOME, recent_list(), active_wiki())
    _label_map = {short_label(p): p for p in _opts}
    _current = short_label(active_wiki()) if active_wiki() else None

    wiki_dropdown = mo.ui.dropdown(
        options=_label_map,
        value=_current if _current in _label_map else None,
        label="📚 Wiki",
        on_change=lambda v: set_active_wiki(v) if v else None,
    )
    mo.vstack([wiki_dropdown])
    return


@app.cell
def wiki_add(mo):
    """Add / open another wiki by path — tucked into an accordion."""
    add_path = mo.ui.text(placeholder="/absolute/path/to/wiki", full_width=True)
    add_btn = mo.ui.run_button(label="Open")
    mo.accordion(
        {"➕ Open another wiki folder": mo.hstack([add_path, add_btn], justify="start")}
    )
    return add_btn, add_path


@app.cell
def wiki_add_runner(mo, add_btn, add_path, recent_list, set_active_wiki, set_recent_list):
    """Commit a typed path: sanitise, validate, make active, remember."""
    from pathlib import Path as _Path
    from domain.wiki_registry import clean_path_input, push_recent

    _cleaned = clean_path_input(add_path.value)
    if not add_btn.value:
        _out = mo.md("")
    elif not _cleaned:
        _out = mo.md("⚠️ Enter a path.")
    else:
        _resolved = str(_Path(_cleaned).expanduser().resolve())
        if not _Path(_resolved).is_dir():
            _out = mo.callout(mo.md(f"⚠️ `{_resolved}` is not a directory."), kind="warn")
        else:
            set_active_wiki(_resolved)
            set_recent_list(push_recent(_resolved, recent_list()))
            _out = mo.callout(mo.md(f"✅ Opened `{_resolved}`"), kind="success")
    _out
    return


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
    Runner cells depend on the trigger and do the real work in a background
    thread, setting running_op so the non-blocking status indicator and the 1s
    auto-refresh (see auto_refresh / op_spinner) stay live during the operation.
    """
    log_lines, set_log_lines = mo.state([])
    ingest_trigger, set_ingest_trigger = mo.state(None)
    scan_trigger,   set_scan_trigger   = mo.state(None)
    regen_trigger,  set_regen_trigger  = mo.state(None)
    lo_visible, set_lo_visible = mo.state(True)
    get_last_handled_event, set_last_handled_event = mo.state(0)
    get_last_lint_event, set_last_lint_event = mo.state(0)
    running_op, set_running_op = mo.state(None)
    return (
        log_lines, set_log_lines,
        ingest_trigger, set_ingest_trigger,
        scan_trigger,   set_scan_trigger,
        regen_trigger,  set_regen_trigger,
        lo_visible, set_lo_visible,
        get_last_handled_event, set_last_handled_event,
        get_last_lint_event, set_last_lint_event,
        running_op, set_running_op,
    )


@app.cell
def timing_helper():
    """Factory for the timed Activity Log callback (per-line elapsed + total).

    cb(msg) prefixes each line with the time elapsed since the previous message,
    so the largest numbers in the log point straight at the slow steps (the LLM
    calls). finish() appends a bold total. Defined once here and returned so every
    runner shares one implementation.

    To keep the user informed of progress, the factory also installs a logging
    handler on the "ingestion" loggers (`domain.ingestion`) so their INFO lines —
    e.g. the extractor's per-file progress, which otherwise reach neither console
    nor panel — stream into the Activity Log alongside the cb messages. The "app"
    half of the curated subset is the cb itself. Lines are de-duped (a domain `_cb`
    logs the same text to its module logger *and* via progress_cb) and the panel is
    capped to the last N lines so a chatty run can't flood the reactive UI.
    """
    import time as _time
    import logging as _logging
    import threading as _threading

    _PANEL_LOGGERS = ("domain.ingestion",)   # the "ingestion" half of the subset
    _MAX_PANEL_LINES = 200

    class _PanelHandler(_logging.Handler):
        """Mirror INFO records into the Activity Log via emit_line."""
        def __init__(self, emit_line):
            super().__init__(level=_logging.INFO)
            self._emit_line = emit_line

        def emit(self, record):
            try:
                self._emit_line(record.getMessage())
            except Exception:
                # Standard logging-handler error path; avoids recursive logging.
                self.handleError(record)

    def make_timed_logger(set_log_lines, logger, tag):
        msgs: list[str] = []
        start = _time.monotonic()
        prev = [start]
        last_raw = [None]
        done = [False]
        lock = _threading.Lock()

        def _emit_line(text: str) -> None:
            with lock:
                if text == last_raw[0]:
                    return  # de-dupe: cb and the module logger emit the same line
                now = _time.monotonic()
                dt = now - prev[0]
                prev[0] = now
                last_raw[0] = text
                msgs.append(f"`+{dt:5.1f}s` {text}")
                del msgs[:-_MAX_PANEL_LINES]
                set_log_lines(list(msgs))

        def cb(msg: str) -> None:
            _emit_line(msg)
            logger.info("[%s] %s", tag, msg)

        # Stream the ingestion loggers' INFO into the panel for the duration of the
        # run. Defensive: drop any handler leaked by a run that didn't finish.
        handler = _PanelHandler(_emit_line)
        captured = []
        for _name in _PANEL_LOGGERS:
            _lg = _logging.getLogger(_name)
            _lg.handlers = [h for h in _lg.handlers if not isinstance(h, _PanelHandler)]
            captured.append((_lg, _lg.level))
            _lg.addHandler(handler)
            if _lg.level == _logging.NOTSET or _lg.level > _logging.INFO:
                _lg.setLevel(_logging.INFO)

        def finish() -> None:
            with lock:
                if done[0]:
                    return
                done[0] = True
                total = _time.monotonic() - start
                msgs.append(f"**total: {total:.1f}s**")
                del msgs[:-_MAX_PANEL_LINES]
                set_log_lines(list(msgs))
            for _lg, _prev_level in captured:
                _lg.removeHandler(handler)
                _lg.setLevel(_prev_level)
            logger.info("[%s] total: %.1fs", tag, _time.monotonic() - start)

        return cb, finish

    return (make_timed_logger,)


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
def action_buttons(mo, set_scan_trigger, set_regen_trigger, set_log_lines):
    """Buttons that fire triggers — they do no work themselves.

    Ingestion is triggered by `ingest_form` (a form bundling the ingest button
    with the full-lint+repair checkbox) — see ingest_form_cell.
    """
    import time as _t

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
    return scan_btn, regen_btn, clear_btn


@app.cell
def ingest_form_cell(mo, upload, set_ingest_trigger):
    """Ingest trigger bundled as a form (associates the button + checkbox).

    The checkbox travels *inside* the form, so its value is read atomically when
    the submit button is clicked — marimo only emits a form's value on submit, so
    there is no checkbox-reset race. `on_change` fires only on submit; it snapshots
    the uploaded files and the flag into `ingest_trigger`, mirroring the on_click
    trigger pattern the other buttons use.

    Unchecked (default) → a cheap *deterministic* lint+repair runs after ingest.
    Checked → the full LLM lint+repair also runs (contradictions, data gaps, plus
    the LLM-backed stale/missing-concept repairs). See ingest_runner.
    """
    import time as _t

    ingest_form = mo.ui.form(
        mo.ui.checkbox(
            label="Also run full LLM lint & repair after ingest (slower, uses tokens)",
            value=False,
        ),
        submit_button_label="⚙️ Ingest uploaded file(s)",
        on_change=lambda _full: set_ingest_trigger(
            (_t.time(), list(upload.value), bool(_full)),
        ),
    )
    return (ingest_form,)



@app.cell
def upload_section(mo, upload, saved, ingest_form):
    """Upload column."""
    mo.vstack([
        mo.md("### 📂 Upload Documents"),
        mo.md("Supports `.pdf` and `.docx`. Files are saved to `sources/`."),
        upload,
        mo.vstack([mo.md(r) for r in saved]) if saved else mo.Html(""),
        ingest_form,
    ], gap=2)


@app.cell(column=1)
def activity_log(mo, log_lines, clear_btn, auto_refresh):
    """Activity Log — repaints on each auto-refresh tick while an op runs, so the
    background thread's progress streams in instead of flushing only at the end.
    The header stays fixed; the lines live in a vertically scrollable panel."""
    if auto_refresh is not None:
        auto_refresh.value  # re-run on each refresh tick
    _lines = log_lines()
    _body = (
        mo.md("\n".join(f"- {line}" for line in _lines)) if _lines
        else mo.md("_No activity yet._")
    )
    # column-reverse pins the scroll to the bottom of the (chronological) list, so
    # the newest line is always in view as it streams in — no JS needed.
    _scroll = mo.Html(
        '<div style="display:flex; flex-direction:column-reverse; '
        'max-height:14em; overflow-y:auto; padding-right:8px;">'
        f'{_body.text}'
        '</div>'
    )
    mo.vstack([
        mo.hstack([mo.md("### 📋 Activity Log"), clear_btn], justify="space-between", align="center"),
        _scroll,
    ], gap=1)


# ── Runner cells sit here so spinners appear directly below the top section ──

@app.cell
def ingest_runner(
    mo, ingest_trigger,
    WORKSPACE, DB_PATH, llm_client, llm_model, WIKI_LANG,
    set_log_lines, set_running_op, logger, make_timed_logger,
):
    """Runs ingestion when ingest_trigger changes, then a lint+repair pass.

    The third trigger element (full_repair, from the ingest form checkbox) picks
    the reconciliation depth:
      - False (default): deterministic lint+repair only — no LLM calls, fast/free.
      - True: full lint+repair, including the LLM checks (contradictions, data
        gaps) and the LLM-backed stale/missing-concept repairs.
    The pass is **scoped to the pages this ingest touched** — the summary pages of
    the ingested sources plus every wiki page that cites them — so an ingest only
    reconciles its own document and never rewrites unrelated pages. (The manual
    "Run Wiki Lint & Repair" button is the place for a wiki-wide sweep.) The
    `orphan` check is excluded either way: concept pages created by *this* ingest
    may legitimately have no inbound links yet, and repair_orphan would delete them
    (same guard as the chat→wiki post-save hook, §6.8).
    """
    mo.stop(ingest_trigger() is None)

    _, _files, _full_repair = ingest_trigger()  # snapshot at submit time
    if not _files:
        set_log_lines(["⚠️ No files uploaded — drop a PDF or DOCX first."])
        mo.stop(True)

    try:
        from domain.ingestion import ingest_file as _if
        from domain.ingestion import crosslink_wiki_pages as _crosslink
        from domain.ingestion.trace import run_scope as _run_scope
        from domain.lint.runner import lint_wiki as _lw
        from domain.lint.report import LintReport as _LintReport
        from domain.repair.runner import repair_wiki as _rw
        from domain.tools.db import get_connection as _get_conn
    except Exception as _e:
        logger.error("Import error: %s", _e, exc_info=True)
        set_log_lines([f"❌ Import error: {_e}"])
        mo.stop(True)

    def _related_pages(src_ids):
        """Wiki pages touched by this ingest: summaries of the ingested sources
        plus every wiki page that cites them. Returns a set of '/wiki/.../x.md'."""
        if not src_ids:
            return set()
        _ph = ",".join("?" * len(src_ids))
        _paths = set()
        with _get_conn(DB_PATH) as _conn:
            for _r in _conn.execute(
                f"SELECT path || filename AS p FROM documents "
                f"WHERE source_kind='wiki' AND source_document_id IN ({_ph})",
                src_ids,
            ).fetchall():
                _paths.add(_r["p"])
            for _r in _conn.execute(
                f"SELECT d.path || d.filename AS p FROM document_references dr "
                f"JOIN documents d ON dr.source_document_id = d.id "
                f"WHERE dr.target_document_id IN ({_ph}) "
                f"AND dr.reference_type = 'cites' AND d.source_kind = 'wiki'",
                src_ids,
            ).fetchall():
                _paths.add(_r["p"])
        return _paths

    _cb, _finish = make_timed_logger(set_log_lines, logger, "ingest")
    _cb("⏳ Ingestion started…")

    def _run():
        set_running_op("ingest")
        try:
            _results = []
            with _run_scope(WORKSPACE, DB_PATH):
                for _f in _files:
                    _fp = WORKSPACE / "sources" / _f.name
                    if not _fp.exists():
                        _fp.write_bytes(_f.contents)
                    _result = _if(_fp, DB_PATH, WORKSPACE, llm_client, llm_model, _cb, language=WIKI_LANG)
                    _results.append(_result)
                    logger.info("Result: %s — %s", _result.status, _result.message)

            # Reconciliation pass — deterministic by default (client=None → the LLM
            # checks/repairs are skipped), full when the checkbox was ticked. Scoped
            # to the pages this ingest touched so unrelated pages are never rewritten.
            _src_ids = [r.doc_id for r in _results if r.status == "ingested" and r.doc_id]
            _related = _related_pages(_src_ids)
            _client = llm_client if _full_repair else None
            _mode = "full LLM" if _full_repair else "deterministic"
            _cb(f"🩺 Running {_mode} lint on {len(_related)} ingested page(s)…")
            _report = _lw(DB_PATH, WORKSPACE, client=_client, model=llm_model, progress_cb=_cb)
            _fixable = [
                i for i in _report.issues
                if i.check != "orphan" and i.page in _related
            ]
            if _fixable:
                _cb(f"🔧 {len(_fixable)} issue(s) on ingested pages — repairing ({_mode})…")
                _rw(
                    _LintReport(issues=_fixable, checked_at=_report.checked_at),
                    DB_PATH, WORKSPACE,
                    llm_client=_client, model=llm_model, progress_cb=_cb,
                    language=WIKI_LANG,
                )
            else:
                _cb("✅ Ingested pages consistent — no repairs needed.")

            # Refresh "See also" cross-links across all pages now that this batch
            # is ingested — an older page may now reference a freshly-added concept.
            if _src_ids:
                _n_linked = _crosslink(WORKSPACE, DB_PATH, language=WIKI_LANG, progress_cb=_cb)
                if _n_linked:
                    _cb(f"🔗 Cross-linked {_n_linked} page(s)")
        finally:
            _finish()
            set_running_op(None)

    mo.Thread(target=_run).start()


@app.cell
def scan_runner(
    mo, scan_trigger,
    WORKSPACE, DB_PATH, llm_client, llm_model, WIKI_LANG,
    set_log_lines, set_running_op, logger, make_timed_logger,
):
    """Runs scan when scan_trigger changes."""
    mo.stop(scan_trigger() is None)

    try:
        from domain.ingestion import scan_and_ingest as _sai
    except Exception as _e:
        set_log_lines([f"❌ Import error: {_e}"])
        mo.stop(True)

    _cb, _finish = make_timed_logger(set_log_lines, logger, "scan")
    _cb("⏳ Scan started…")

    def _run():
        set_running_op("scan")
        try:
            _sai(WORKSPACE, DB_PATH, llm_client, llm_model, _cb, language=WIKI_LANG)
        finally:
            _finish()
            set_running_op(None)

    mo.Thread(target=_run).start()


@app.cell
def regen_runner(
    mo, regen_trigger,
    WORKSPACE, DB_PATH, llm_client, llm_model, WIKI_LANG,
    set_log_lines, set_running_op, logger, make_timed_logger,
):
    """Runs wiki regeneration when regen_trigger changes."""
    mo.stop(regen_trigger() is None)

    try:
        from domain.ingestion import regenerate_wiki_pages as _rwp
    except Exception as _e:
        set_log_lines([f"❌ Import error: {_e}"])
        mo.stop(True)

    _cb, _finish = make_timed_logger(set_log_lines, logger, "regen")
    _cb("⏳ Regeneration started…")

    def _run():
        set_running_op("regen")
        try:
            _rwp(WORKSPACE, DB_PATH, llm_client, llm_model, _cb, language=WIKI_LANG)
        finally:
            _finish()
            set_running_op(None)

    mo.Thread(target=_run).start()


@app.cell
def auto_refresh(mo, running_op):
    """While an operation runs, mount a 1s auto-refresh so the Activity Log repaints
    from the background thread's state on a timer.

    The work runs in a `mo.Thread`; the old blocking `op_spinner` loop held the
    kernel for the whole op, so the panel's reactive re-renders queued up and only
    flushed at the end. A frontend-driven refresh ticks independently of the worker
    thread, so progress streams in. Idle → no ticker, no polling.
    """
    auto_refresh = mo.ui.refresh(default_interval="1s") if running_op() is not None else None
    auto_refresh if auto_refresh is not None else mo.md("")
    return (auto_refresh,)


@app.cell
def op_spinner(mo, running_op, auto_refresh):
    """Non-blocking 'running' indicator — re-evaluated on each auto-refresh tick.

    Replaces the old `while running_op(): sleep(0.1)` loop, which blocked the kernel
    and prevented the Activity Log from streaming mid-operation.
    """
    if auto_refresh is not None:
        auto_refresh.value  # depend on the tick so it stays live and clears at the end
    _labels = {
        "ingest":       "Ingesting documents…",
        "scan":         "Scanning sources/…",
        "regen":        "Regenerating wiki pages…",
        "lint_repair":  "Running wiki lint & repair…",
    }
    _op = running_op()
    mo.md(f"⏳ **{_labels.get(_op, 'Running…')}**") if _op else mo.md("")


@app.cell
def bulk_actions(mo, scan_btn, regen_btn):
    """Bulk operation buttons."""
    mo.vstack([
        mo.md("### 🔧 Bulk Actions"),
        scan_btn,
        regen_btn,
    ], gap=2)


@app.cell
def lint_repair_widget_cell(mo):
    """Wiki-wide lint & repair confirmation widget."""
    import sys as _sys
    from pathlib import Path as _Path
    _widgets_dir = str(_Path(__file__).parent / "widgets")
    if _widgets_dir not in _sys.path:
        _sys.path.insert(0, _widgets_dir)
    from delete_confirm import DeleteConfirmWidget as _DeleteConfirmWidget

    lint_repair_widget = mo.ui.anywidget(_DeleteConfirmWidget(
        button_label="Run Wiki Lint & Repair",
        message="This will scan all wiki pages for issues and automatically repair them. Continue?",
        disabled=False,
    ))
    lint_repair_widget
    return (lint_repair_widget,)


@app.cell
def debug_panel(mo, ingest_form, scan_btn, upload, DB_PATH, debug_mode, logger):
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
                f"- `ingest_form.value` = `{ingest_form.value}`\n"
                f"- `scan_btn.value`   = `{scan_btn.value}`\n"
                f"- `upload.value`     = `{upload_names}`\n"
                f"- `DB_PATH`          = `{DB_PATH}`\n"
                f"- DB counts          = `{db_info}`"
            ),
            kind="info",
        )
        logger.debug(
            "debug_panel: ingest_form=%s scan_btn=%s upload=%s db=%s",
            ingest_form.value, scan_btn.value, upload_names, db_info,
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
def also_file_check_cell(mo, log_lines):
    """Secondary option — separate cell so it doesn't reset the table selection.

    Depends on log_lines so it resets to unchecked after each operation.
    """
    log_lines()
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


@app.cell
def lint_repair_runner(
    mo, lint_repair_widget,
    get_last_lint_event, set_last_lint_event,
    WORKSPACE, DB_PATH, llm_client, llm_model, WIKI_LANG,
    set_log_lines, set_running_op, logger, make_timed_logger,
):
    """Fires when the lint & repair widget is confirmed."""
    _event_id = lint_repair_widget.event_id
    _last = get_last_lint_event()

    mo.stop(_event_id <= _last)
    set_last_lint_event(_event_id)

    try:
        from domain.lint.runner import lint_wiki as _lw
        from domain.repair.runner import repair_wiki as _rw
    except Exception as _e:
        set_log_lines([f"❌ Import error: {_e}"])
        mo.stop(True)

    _cb, _finish = make_timed_logger(set_log_lines, logger, "lint-repair")
    _cb("⏳ Wiki lint & repair started…")

    def _run():
        set_running_op("lint_repair")
        try:
            _lint_report = _lw(DB_PATH, WORKSPACE, client=llm_client, model=llm_model, progress_cb=_cb)
            _issue_count = len(_lint_report.issues)
            if _issue_count == 0:
                _cb("✅ No issues found.")
            else:
                _cb(f"🔧 Found {_issue_count} issue(s) — running repairs…")
                _rw(
                    _lint_report, DB_PATH, WORKSPACE,
                    llm_client=llm_client, model=llm_model,
                    progress_cb=_cb, language=WIKI_LANG,
                )
        finally:
            _finish()
            set_running_op(None)

    mo.Thread(target=_run).start()


if __name__ == "__main__":
    app.run()
