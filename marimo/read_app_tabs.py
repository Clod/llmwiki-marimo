# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "marimo",
#     "pydantic-ai",
#     "pydantic-settings",
#     "aiosqlite",
#     "python-dotenv",
#     "anywidget==0.11.0",
# ]
# ///
"""
LLMWiki Read App — TABS VARIANT (experimental)
----------------------------------------------
Same app as read_app.py, but the 3-panel grid is reorganised into two tabs:
  📖 Lectura  — wiki picker + page navigator + reader
  💬 Diálogo  — the chat, FULL WIDTH (so advisory tables/long answers breathe)

Why a separate file: easy rollback. read_app.py + layouts/read_app.grid.json are
left untouched; this is purely additive. Launch this one to try tabs; delete it
to roll back. If tabs win, this replaces read_app.py later.

Reactivity notes (the reason this is safe):
  - The tabs are CONTROLLED: the active tab lives in `mo.state` and is passed back
    as `value=`, so when the assembly cell re-runs (page nav, save, end of a chat
    turn) it rebuilds with the SAME active tab — you are never bounced out of
    Diálogo mid-conversation.
  - The chat body is `mo.ui.chat` (a stable, self-streaming element). Streaming
    yields tokens INTO that element client-side; it never reassigns `chat_view`,
    so the assembly cell does NOT re-run per token.
  - Composing already-built widgets into a layout does not sever their `.value`
    reactivity — marimo tracks elements by object identity, not render location.
"""

import marimo

__generated_with = "0.23.6"
# No layout_file: the tabs own the layout (and this sheds the fragile positional
# grid JSON that desyncs whenever the cell count changes).
app = marimo.App(width="full")

with app.setup:
    """Initialize environment, imports, and wiki-picker defaults."""
    import sys
    import marimo as mo
    import os
    import re
    import logging
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv()

    debug_enabled = os.environ.get("DEBUG") or os.environ.get("WIKI_DEBUG")
    log_level = logging.DEBUG if debug_enabled else logging.WARNING
    logging.basicConfig(level=log_level, format="[%(levelname)s] %(name)s: %(message)s")
    logger = logging.getLogger("wiki_app")

    _project_root = Path(__file__).parent.parent
    _base = str(_project_root / "base")
    if _base not in sys.path:
        sys.path.insert(0, _base)
    _marimo = str(_project_root / "marimo")
    if _marimo not in sys.path:
        sys.path.insert(0, _marimo)
    sys.modules.pop("config", None)

    from config import settings, require_llm_config
    from domain.chat.agent import create_agent
    from domain.chat.config import load_config
    from domain.wiki_registry import (
        clean_path_input,
        load_recent,
        merge_options,
        push_recent,
        resolve_wiki_home,
        short_label,
    )
    from widgets.delete_confirm import DeleteConfirmWidget

    # WIKI_PATH/db/agent are no longer constants — they are derived reactively in
    # the `wiki_context` cell from the active-wiki state. Here we only compute the
    # default selection and the folder to scan for sibling wikis.
    _env_wiki = os.environ.get("WIKI_PATH", "").strip()
    ENV_DEFAULT = str(Path(_env_wiki).expanduser().resolve()) if _env_wiki else None
    WIKI_HOME = resolve_wiki_home(_env_wiki or None)


@app.cell
def styles():
    """Custom CSS: tighten prose spacing + let flex columns shrink.

    The `min-width: 0` rule is the canonical flexbox fix: by default flex children
    have `min-width: auto`, so a wide page table refuses to shrink below its
    content and overflows the reader beside it. Allowing them to shrink makes the
    table scroll INSIDE its own column instead of overlapping."""
    mo.Html("""<style>
    .prose span.paragraph { display: block; margin-top: 0.2em; margin-bottom: 0.2em; }
    .prose h1, .prose h2, .prose h3 { margin-top: 0.6em; margin-bottom: 0.2em; }
    .prose li { margin-top: 0.1em; margin-bottom: 0.1em; }
    /* Tabs Lectura: flex columns may shrink -> wide table scrolls in-column. */
    [style*="flex"] { min-width: 0; }
    /* Wide markdown tables (e.g. the advisory's 7 columns) scroll horizontally
       inside their chat bubble instead of clipping the rightmost column (fuente).
       display:block is the standard responsive-table trick that enables the
       horizontal scrollbar. */
    .prose table { display: block; overflow-x: auto; max-width: 100%; }
    </style>""")
    return


@app.cell
def wiki_helpers(WIKI_PATH):
    """File helpers: scan and read wiki pages for the active wiki."""
    wiki_dir = (WIKI_PATH / "wiki") if WIKI_PATH else None
    if wiki_dir is not None:
        wiki_dir.mkdir(parents=True, exist_ok=True)

    def scan_pages():
        if wiki_dir is None:
            return []
        return sorted(
            str(p.relative_to(wiki_dir).with_suffix(""))
            for p in wiki_dir.rglob("*.md")
        )

    def read_page(rel_path):
        if wiki_dir is None:
            return ""
        path = wiki_dir / f"{rel_path}.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    return read_page, scan_pages


@app.cell
def page_state(scan_pages):
    """Reactive state: page list and current selection (re-created on wiki switch)."""
    page_list, set_page_list = mo.state(scan_pages(), allow_self_loops=True)
    initial_pages = page_list()
    selected_page, set_selected_page = mo.state(
        initial_pages[0] if initial_pages else None
    )
    prev_page, set_prev_page = mo.state(None)
    delete_trigger, set_delete_trigger = mo.state(None)
    last_delete_event, set_last_delete_event = mo.state(0)

    def navigate_to(page):
        set_prev_page(selected_page())
        set_selected_page(page)

    return (
        delete_trigger,
        last_delete_event,
        navigate_to,
        page_list,
        prev_page,
        selected_page,
        set_delete_trigger,
        set_last_delete_event,
        set_page_list,
        set_selected_page,
    )


@app.cell
def left_panel(
    navigate_to,
    page_list,
    scan_pages,
    selected_page,
    set_page_list,
):
    """Navigation sidebar view (composed into the Lectura tab).

    Tabs variant: the sidebar sits BESIDE the reader in a flex row, so the page
    table must stay narrow or it overflows the reader column. We therefore show
    only Título + Ruta (no wide Excerpt/keyword-preview column, and no DB query
    to build it) and cap the table height. Selection is driven by the exact stem
    in the "Ruta" column, so it stays unambiguous."""
    pages = page_list()
    _current = selected_page()

    def _page_row(p):
        slug = p.rsplit("/", 1)[-1]
        title = slug.replace("-", " ").replace("_", " ").title()
        return {"Título": title, "Ruta": p}

    _table_data = [_page_row(p) for p in pages] if pages else [{"Título": "(sin páginas)", "Ruta": ""}]

    _current_idx = next(
        (i for i, r in enumerate(_table_data) if r["Ruta"] == _current),
        None,
    )

    def _on_select(rows):
        if not rows:
            return
        stem = rows[0].get("Ruta")
        if stem:
            navigate_to(stem)

    page_selector = mo.ui.table(
        _table_data,
        selection="single",
        on_change=_on_select,
        show_column_summaries=False,
        show_data_types=False,
        page_size=15,
        max_height=520,
        initial_selection=[_current_idx] if _current_idx is not None else None,
    )
    refresh_btn = mo.ui.button(
        label="⟳ Refresh",
        on_click=lambda _: set_page_list(scan_pages()),
    )
    left_view = mo.vstack([page_selector, refresh_btn], gap=2)
    return (left_view,)


@app.cell
def delete_widget_cell(selected_page, set_last_delete_event):
    """Delete confirm widget — self-contained button + inline confirmation panel."""
    delete_page = selected_page()
    set_last_delete_event(0)
    delete_widget = mo.ui.anywidget(
        DeleteConfirmWidget(
            label=delete_page or "",
            disabled=not delete_page,
        )
    )
    return delete_page, delete_widget


@app.cell
def delete_event_cell(
    delete_page,
    delete_widget,
    last_delete_event,
    set_delete_trigger,
    set_last_delete_event,
):
    """Fires delete_trigger when the widget reports a confirmed deletion."""
    import time as _time

    _event_id = delete_widget.event_id
    if _event_id > last_delete_event() and delete_page:
        set_last_delete_event(_event_id)
        set_delete_trigger((delete_page, _time.time()))
    return


@app.cell
def delete_runner(
    WIKI_PATH,
    delete_trigger,
    scan_pages,
    set_page_list,
    set_selected_page,
    wiki_db_path,
):
    """Executes wiki page deletion when delete_trigger fires. Unlike the grid
    version this uses a conditional (not mo.stop) so `delete_result` is always
    defined and can be composed into the Lectura tab."""
    from domain.tools.wiki_fs import delete_page as _delete_page

    if delete_trigger() is None:
        delete_result = mo.md("")
    else:
        page_stem, _ = delete_trigger()

        parts = page_stem.rsplit("/", 1)
        dir_path = f"/wiki/{parts[0]}/" if len(parts) > 1 else "/wiki/"
        slug = parts[-1]

        try:
            _delete_page(wiki_db_path, WIKI_PATH, dir_path, slug)
            set_page_list(scan_pages())
            set_selected_page(None)
            delete_result = mo.callout(mo.md(f"✅ Deleted `{page_stem}`"), kind="success")
        except Exception as exc:
            delete_result = mo.callout(mo.md(f"❌ Deletion failed: {exc}"), kind="danger")
    return (delete_result,)


@app.cell
def current_page(read_page, selected_page):
    """Load the selected page from disk."""
    selected_stem = selected_page() if selected_page() else ""
    current_content = read_page(selected_stem) if selected_stem else ""
    return current_content, selected_stem


@app.cell
def page_links_nav(
    current_content,
    navigate_to,
    page_list,
    prev_page,
    selected_stem,
):
    """Navigation buttons for internal wiki links found on the current page.

    Links are relative to the current page's directory (e.g. a concept page
    links to a sibling as `cinderella.md` or to a summary as `../summaries/x.md`),
    so resolve them against that directory before matching the scanned page list,
    which stores directory-prefixed stems like `concepts/cinderella`.
    """
    import posixpath

    # (?<!!) excludes image embeds ![alt](src) — matches references.py:_WIKI_LINK_RE.
    raw_links = re.findall(r'(?<!!)\[([^\]]+)\]\(([^)]+)\)', current_content or "")
    current_dir = posixpath.dirname(selected_stem or "")
    all_pages = page_list()
    seen = set()
    valid = {}
    for _label, _target in raw_links:
        if _target.startswith("http") or _target.startswith("mailto"):
            continue
        resolved = posixpath.normpath(posixpath.join(current_dir, _target.removesuffix(".md")))
        if resolved in all_pages and resolved not in seen:
            seen.add(resolved)
            valid[_label] = resolved

    def _make_handler(page):
        def _go(_v):
            navigate_to(page)
        return _go

    _back_target = prev_page() if prev_page() is not None else selected_stem
    _back_label = _back_target.rsplit("/", 1)[-1].replace("-", " ").title() if _back_target else ""
    back_btn = mo.ui.button(
        label=f"← {_back_label}" if _back_label else "←",
        on_click=lambda _: navigate_to(_back_target),
        kind="neutral",
    )
    link_buttons = [
        mo.ui.button(label=label, on_click=_make_handler(page), kind="neutral")
        for label, page in valid.items()
    ]
    nav_widget = mo.hstack([back_btn] + link_buttons, wrap=True, gap=1)
    return (nav_widget,)


@app.cell
def middle_panel(current_content, nav_widget, selected_stem):
    """Content viewer view (composed into the Lectura tab)."""
    _title = selected_stem.rsplit("/", 1)[-1].replace("-", " ").title() if selected_stem else "No page selected"
    _header = mo.md(f"## {_title}")

    _text = (
        re.sub(r'- \[\^[^\]]+\]:\s*', '- ', re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', current_content))
        if current_content
        else "*Select a page from the left panel.*"
    )
    _scroll = mo.Html(
        f'<div style="height:70vh; overflow-y:auto; padding-right:8px;">'
        f'{mo.md(_text).text}'
        f'</div>'
    )
    middle_view = mo.vstack([_header, mo.vstack([_scroll, nav_widget], gap=2)], gap=2)
    return (middle_view,)


@app.cell
def guardrail_flag():
    """Shared mutable flag for the grounding guardrail. A plain dict (NOT
    mo.state): the chat handler reads it live without making chat_panel reactive,
    so flipping the toggle never rebuilds the chat. The toggle cell mutates it in
    place. (A mo.state getter is not reliably live inside the async chat closure,
    which is why this is a plain dict rather than mo.state.)"""
    grounding_flag = {"strict": True}
    return (grounding_flag,)


@app.cell
def chat_panel(grounding_flag, wiki_agent, wiki_chat_config, wiki_db_path):
    """AI chat assistant with FTS5 retrieval. Produces `chat_view` (a stable
    layout wrapping mo.ui.chat) for the Diálogo tab; streaming updates happen
    inside that element, so this view object never churns per token."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart, ModelResponse, TextPart

    last_response, set_last_response = mo.state("")

    async def respond(messages, config):
        # The "Modo estricto" toggle (grounding_flag["strict"], read live at
        # call-time) controls two coupled behaviors:
        #   ON  -> strict: run to completion so the guardrail can inspect the
        #          tool history, then gate the answer (refuse if ungrounded).
        #          Cannot stream — you can't retract text already shown.
        #   OFF -> normal: stream token-by-token (original UX), no gating.
        # Strict+streaming is incoherent, so the two move together by necessity.
        from domain.chat.guardrail import (
            enforce_grounding,
            has_grounding,
            refusal_for,
            strip_refused_exchanges,
        )
        from domain.chat.trace import (
            build_turn_record,
            chat_trace_enabled,
            record_turn,
        )
        from domain.chat.postprocess import answer_with_table, ensure_citation
        from domain.chat.history import trim_history

        # Drop prior refusals from the context: a citation-less "not in my
        # knowledge base" turn primes the model to answer the next question
        # without a citation too (verified). They carry nothing forward.
        history = []
        for msg in trim_history(strip_refused_exchanges(messages[:-1])):
            if msg.role == "user":
                history.append(ModelRequest(parts=[UserPromptPart(content=msg.content)]))
            elif msg.role == "assistant":
                history.append(ModelResponse(parts=[TextPart(content=msg.content)]))

        _lang = wiki_chat_config.language if wiki_chat_config else None
        _question = messages[-1].content

        def _trace(*, raw_output, final_answer, result, refusal_substituted):
            # Opt-in (WIKI_CHAT_TRACE=1): one JSONL row per turn so a session can
            # be diagnosed offline — history, tool calls + retrieved content, the
            # raw output vs the guardrail's final answer. See domain/chat/trace.py.
            # Fully defensive: a trace failure must never break the chat turn.
            if not chat_trace_enabled():
                return
            try:
                msgs = result.all_messages()
                workspace = Path(wiki_db_path).parent.parent if wiki_db_path else None
                hist = [
                    {"role": getattr(m, "role", None), "content": getattr(m, "content", None)}
                    for m in messages[:-1]
                ]
                record_turn(workspace, build_turn_record(
                    question=_question, language=_lang,
                    strict_mode=grounding_flag["strict"], history=hist, messages=msgs,
                    raw_output=raw_output, final_answer=final_answer,
                    grounded=has_grounding(msgs), refusal_substituted=refusal_substituted,
                ))
            except Exception:  # noqa: BLE001 — tracing is best-effort
                pass

        if wiki_chat_config and wiki_chat_config.pre_retrieval:
            # Hybrid pre-retrieval (opt-in per wiki): the CODE retrieves + gates;
            # the model (built without wiki search tools) answers only from the
            # injected context. See domain/chat/preretrieval.py.
            from domain.chat.preretrieval import pre_retrieval_answer
            _ws = Path(wiki_db_path).parent.parent

            async def _run_agent(_prompt, _hist):
                return await wiki_agent.run(_prompt, deps=wiki_db_path, message_history=_hist)

            def _pre_trace(*, raw, final, result, refusal_substituted):
                _trace(raw_output=raw, final_answer=final, result=result,
                       refusal_substituted=refusal_substituted)

            answer = await pre_retrieval_answer(
                _question, config=wiki_chat_config, db_path=wiki_db_path,
                workspace=_ws, history=history, language=_lang,
                run_agent=_run_agent, on_trace=_pre_trace,
            )
            set_last_response(answer)
            yield answer
            return

        if grounding_flag["strict"]:
            result = await wiki_agent.run(
                _question, deps=wiki_db_path, message_history=history
            )
            raw = result.output
            _msgs = result.all_messages()
            answer = enforce_grounding(raw, _msgs, refusal=refusal_for(_lang))
            refusal_substituted = answer != raw
            # Deterministic post-processing (domain/chat/postprocess.py): guarantee
            # the advisory table and a source citation regardless of whether the
            # model reproduced them under history priming. Both no-op on a refusal.
            answer = answer_with_table(answer, _msgs)
            answer = ensure_citation(answer, _msgs)
            _trace(raw_output=raw, final_answer=answer, result=result,
                   refusal_substituted=refusal_substituted)
            set_last_response(answer)
            yield answer
        else:
            full_text = ""
            async with wiki_agent.run_stream(
                _question, deps=wiki_db_path, message_history=history
            ) as result:
                async for chunk in result.stream_text(delta=True):
                    full_text += chunk
                    yield chunk
            _trace(raw_output=full_text, final_answer=full_text, result=result,
                   refusal_substituted=False)
            set_last_response(full_text)

    if wiki_agent is None:
        _body = mo.md("*Select a wiki (in the Lectura tab) to start chatting.*")
    else:
        _body = mo.ui.chat(
            respond,
            prompts=(wiki_chat_config.suggested_prompts if wiki_chat_config else []),
            max_height=720,
        )
    chat_view = mo.vstack([mo.md("### 💬 Chat con tu Wiki"), _body], gap=2)
    return chat_view, last_response


@app.cell
def guardrail_toggle(grounding_flag):
    """Pluggable grounding guardrail toggle — own cell (one concern), so toggling
    never re-runs chat_panel (which depends only on the stable grounding_flag
    dict). on_change mutates that shared dict, which the chat handler reads live."""
    def _on_change(checked):
        grounding_flag["strict"] = checked

    _strict = mo.ui.checkbox(
        value=grounding_flag["strict"],
        on_change=_on_change,
        label="Modo estricto: responder solo con fuentes del wiki",
    )
    strict_view = mo.vstack([_strict])
    return (strict_view,)


@app.cell
def save_feedback_state():
    """State for the save flow (kept in its own cell — one concern per cell):
    `save_tick` forces the form to rebuild after a save so the title box clears;
    `saved_notice` holds the last result so the confirmation survives that
    rebuild."""
    save_tick, set_save_tick = mo.state(0)
    saved_notice, set_saved_notice = mo.state(None)
    return save_tick, saved_notice, set_save_tick, set_saved_notice


@app.cell
def save_form(last_response, save_tick):
    """Save-to-wiki form. Depends on `save_tick` so a completed save rebuilds the
    widgets with an empty title: this clears the box AND resets `form.value` to
    None, which also prevents an accidental re-save when the chat later updates."""
    save_tick()  # dependency only — rebuild fresh widgets after each save
    _title = mo.ui.text(label="Title", placeholder="Page title...")
    _category = mo.ui.dropdown(
        {"Concept": "concept", "Summary": "summary"},
        label="Category",
        value="Concept",
    )

    def _validate(v):
        if not last_response():
            return "Chat with the assistant first."
        if not (v.get("title") or "").strip():
            return "Title cannot be empty."
        return None

    form = (
        mo.md("### Save last response to wiki\n\n{title}\n\n{category}")
        .batch(title=_title, category=_category)
        .form(submit_button_label="💾 Save to wiki", validate=_validate)
    )
    return (form,)


@app.cell
def save_action(
    WIKI_PATH,
    form,
    last_response,
    set_save_tick,
    set_saved_notice,
    wiki_chat_config,
    wiki_db_path,
):
    """Perform the save exactly once per submission, store the result, then bump
    `save_tick` to clear the form. Rendering is handled by `save_notice`."""
    from openai import OpenAI
    from domain.chat.wiki_tools import save_to_wiki

    if form.value is not None:
        _client = OpenAI(base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY)
        _title = (form.value.get("title") or "").strip()
        _category = form.value.get("category", "concept")
        # Save chat-sourced pages in this wiki's content language (no-op for "en").
        _language = wiki_chat_config.language if wiki_chat_config else "en"
        try:
            _msg = save_to_wiki(
                wiki_db_path, WIKI_PATH, _title, last_response(), _category,
                client=_client, model=settings.LLM_MODEL, language=_language,
            )
            set_saved_notice(("success", _msg))
        except Exception as exc:
            set_saved_notice(("danger", f"Save failed: {exc}"))
        # Rebuild the form: clears the title box and resets form.value (→ no re-save).
        set_save_tick(lambda t: t + 1)
    return


@app.cell
def save_notice(saved_notice):
    """Persistent save confirmation — reads state (not form.value) so it survives
    the form rebuild and stays visible until the next save."""
    _n = saved_notice()
    if _n is None:
        save_notice_view = mo.md("")
    else:
        _kind, _text = _n
        _icon = "✅" if _kind == "success" else "❌"
        save_notice_view = mo.callout(mo.md(f"{_icon} {_text}"), kind=_kind)
    return (save_notice_view,)


@app.cell
def wiki_state():
    """Active-wiki selection + recent-wikis list (the picker's reactive roots)."""
    active_wiki, set_active_wiki = mo.state(ENV_DEFAULT or None)
    recent_list, set_recent_list = mo.state(load_recent())
    return active_wiki, recent_list, set_active_wiki, set_recent_list


@app.cell
def wiki_context(active_wiki):
    """Derive path-bound objects from the active wiki; re-runs on switch."""
    _ap = active_wiki()
    WIKI_PATH = Path(_ap) if _ap else None
    if WIKI_PATH is not None and WIKI_PATH.is_dir():
        require_llm_config(
            settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL,
            purpose="chat",
        )
        wiki_db_path = str(WIKI_PATH / ".llmwiki" / "index.db")
        wiki_chat_config = load_config(WIKI_PATH)
        # Optional Argentine-finance overlay: registers the `estimar_alternativas`
        # tool only when this workspace's data satisfies the finance manifest.
        # The engine stays finance-agnostic; activation is decided here (the
        # composition root) and injected via extra_tools/extra_prompt.
        from domain.finance_argentina.agent_tool import activate as _activate_finance
        _fin_tools, _fin_prompt = _activate_finance(WIKI_PATH)
        wiki_agent = create_agent(
            settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL,
            system_prompt=wiki_chat_config.system_prompt,
            language=wiki_chat_config.language,
            workspace=WIKI_PATH,
            extra_tools=_fin_tools,
            extra_prompt=_fin_prompt,
            include_wiki_tools=not wiki_chat_config.pre_retrieval,
        )
    else:
        wiki_db_path = None
        wiki_chat_config = None
        wiki_agent = None
    return WIKI_PATH, wiki_agent, wiki_chat_config, wiki_db_path


@app.cell
def wiki_picker(active_wiki, recent_list, set_active_wiki):
    """The picker — one dropdown over discovered + recent wikis."""
    _opts = merge_options(WIKI_HOME, recent_list(), active_wiki())
    _label_map = {short_label(p): p for p in _opts}
    _current = short_label(active_wiki()) if active_wiki() else None

    wiki_dropdown = mo.ui.dropdown(
        options=_label_map,
        value=_current if _current in _label_map else None,
        label="📚 Wiki",
        on_change=lambda v: set_active_wiki(v) if v else None,
    )
    picker_view = mo.vstack([wiki_dropdown])
    return (picker_view,)


@app.cell
def wiki_add():
    """Add / open another wiki by path — tucked into an accordion."""
    add_path = mo.ui.text(placeholder="/absolute/path/to/wiki", full_width=True)
    add_btn = mo.ui.run_button(label="Open")
    add_view = mo.accordion(
        {"➕ Open another wiki folder": mo.hstack([add_path, add_btn], justify="start")}
    )
    return add_btn, add_path, add_view


@app.cell
def wiki_add_runner(
    add_btn,
    add_path,
    recent_list,
    set_active_wiki,
    set_recent_list,
):
    """Commit a typed path: sanitise, validate, make active, remember."""
    _cleaned = clean_path_input(add_path.value)
    if not add_btn.value:
        add_result = mo.md("")
    elif not _cleaned:
        add_result = mo.md("⚠️ Enter a path.")
    else:
        _resolved = str(Path(_cleaned).expanduser().resolve())
        if not Path(_resolved).is_dir():
            add_result = mo.callout(mo.md(f"⚠️ `{_resolved}` is not a directory."), kind="warn")
        else:
            set_active_wiki(_resolved)
            set_recent_list(push_recent(_resolved, recent_list()))
            add_result = mo.callout(mo.md(f"✅ Opened `{_resolved}`"), kind="success")
    return (add_result,)


@app.cell
def tab_switch():
    """Tab selector. A plain radio (NOT mo.ui.tabs) so `tab_body` below renders
    ONLY the active tab. mo.ui.tabs builds BOTH tab bodies and rebuilds them on
    every re-run, which re-parents the interactive mo.ui.chat and invalidates its
    server callback ("Could not find function ... send_prompt"). This selector has
    no deps, so it runs once and is stable; its value drives the two cells below."""
    tab_choice = mo.ui.radio(
        options=["📖 Lectura", "💬 Diálogo"],
        value="📖 Lectura",
        inline=True,
    )
    tab_choice
    return (tab_choice,)


@app.cell
def tab_body(
    tab_choice,
    picker_view,
    add_view,
    add_result,
    left_view,
    delete_widget,
    delete_result,
    middle_view,
    strict_view,
    chat_view,
):
    """Render ONLY the active tab.

    Reactivity contract (this is what keeps the chat alive): this cell depends on
    stable view objects (chat_view, strict_view) and the reading views — but NOT
    on `form` / `last_response`. So finishing a chat turn does NOT re-run this cell
    and the chat is never re-parented mid-conversation. The chat is (re)built only
    on a tab switch or a wiki switch — each a fresh render the frontend adopts
    cleanly. Streaming happens inside the stable mo.ui.chat element regardless."""
    if tab_choice.value == "💬 Diálogo":
        _body = mo.vstack([strict_view, chat_view], gap=2)
    else:
        _sidebar = mo.vstack(
            [picker_view, add_view, add_result, left_view, delete_widget, delete_result],
            gap=2,
        )
        _body = mo.hstack([_sidebar, middle_view], widths=[1, 1.5], gap=2, align="start")
    _body
    return


@app.cell
def save_area(tab_choice, form, save_notice_view):
    """The "save last response" accordion, shown only under the Diálogo tab. Kept
    in its OWN cell (separate from tab_body) so that a chat turn — which reassigns
    `form` via last_response — re-runs only THIS cell, re-parenting the little
    form/notice but never the chat rendered by tab_body above it."""
    if tab_choice.value == "💬 Diálogo":
        _out = mo.accordion(
            {"💾 Guardar la última respuesta en el wiki": mo.vstack([form, save_notice_view], gap=1)}
        )
    else:
        _out = mo.md("")
    _out
    return


if __name__ == "__main__":
    app.run()
