# PROTOTYPE — NOT ACTIVE
#
# This is a backup of read_app.py before edit/create functionality was removed.
# It was retired because:
#   - Manual page edits write only to disk; document_chunks in SQLite are NOT
#     updated, so FTS5 search returns stale results after any manual edit.
#   - The edit/create UI was untestable due to marimo's blur-based text input
#     timing (Playwright tests could not reliably trigger the reactive updates).
#
# All wiki content now flows exclusively through the ingest pipeline
# (marimo_new/ingest_app.py), which keeps disk and DB in sync.
#
# Keep for reference: it shows the create-page validation pattern, the
# trigger-capture button pattern, and the hint-cell self-loop workaround.
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "marimo",
#     "pydantic-ai",
#     "pydantic-settings",
#     "aiosqlite",
#     "python-dotenv",
# ]
# ///
"""
LLMWiki Read App
----------------

This marimo notebook provides a user interface for reading, editing, and interacting
with a personal investment wiki. It features:
- A sidebar for page navigation and creation
- A central markdown viewer and editor
- An AI-powered chat assistant that uses the wiki content as context
"""

import marimo

__generated_with = "0.23.6"
app = marimo.App(width="full", layout_file="layouts/read_app.grid.json")

with app.setup:
    """Initialize environment, paths, and wiki agent."""
    import sys
    import marimo as mo
    import os
    import re
    import logging
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv()

    # Configure logging (enable with DEBUG=1 env var or WIKI_DEBUG=1)
    debug_enabled = os.environ.get("DEBUG") or os.environ.get("WIKI_DEBUG")
    log_level = logging.DEBUG if debug_enabled else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format='[%(levelname)s] %(name)s: %(message)s'
    )
    logger = logging.getLogger("wiki_app")
    if debug_enabled:
        logger.info("Debug logging enabled")

    WIKI_PATH = Path(os.environ["WIKI_PATH"])

    # Make api_new/ importable (api_new wins over api/ for config)
    _project_root = Path(__file__).parent.parent
    for _p in [str(_project_root / "api"), str(_project_root / "api_new")]:
        if _p not in sys.path:
            sys.path.insert(0, _p)
    sys.modules.pop("config", None)

    from config import settings
    from domain.chat.agent import create_agent
    from domain.chat.config import load_config

    wiki_db_path = str(WIKI_PATH / ".llmwiki" / "index.db")
    wiki_chat_config = load_config(WIKI_PATH)
    wiki_agent = create_agent(
        settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL,
        system_prompt=wiki_chat_config.system_prompt,
    )


@app.cell
def styles():
    """Custom CSS styles: tighten spacing for headings, paragraphs, and lists."""
    mo.Html("""<style>
    .prose span.paragraph { display: block; margin-top: 0.2em; margin-bottom: 0.2em; }
    .prose h1, .prose h2, .prose h3 { margin-top: 0.6em; margin-bottom: 0.2em; }
    .prose li { margin-top: 0.1em; margin-bottom: 0.1em; }
    </style>""")
    return


@app.cell
def wiki_helpers():
    """File helpers: scan, read, write pages and build LLM context."""
    wiki_dir = WIKI_PATH / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)

    def scan_pages():
        return sorted(p.stem for p in wiki_dir.glob("*.md"))

    def read_page(stem):
        path = wiki_dir / f"{stem}.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def write_page(stem, text):
        (wiki_dir / f"{stem}.md").write_text(text, encoding="utf-8")

    def build_context():
        parts = []
        for p in sorted(wiki_dir.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            parts.append(f"## {p.stem}\n\n{text}")
        return "\n\n---\n\n".join(parts)

    return build_context, read_page, scan_pages, write_page


@app.cell
def page_state(scan_pages):
    """Reactive state for the page list and current selection."""
    page_list, set_page_list = mo.state(scan_pages(), allow_self_loops=True)
    initial_pages = page_list()
    selected_page, set_selected_page = mo.state(initial_pages[0] if initial_pages else None)
    return (
        page_list,
        selected_page,
        set_page_list,
        set_selected_page,
    )


@app.cell
def clear_state():
    """Counter for widget reset; click_empty tracks empty-click for validation hint."""
    clear_version, set_clear_version = mo.state(0)
    click_empty, set_click_empty = mo.state(False)
    return clear_version, set_clear_version, click_empty, set_click_empty


@app.cell
def new_page_widget(clear_version):
    """Fresh text input each time clear_version increments (post-create reset)."""
    clear_version()  # reactive dependency
    new_page_input = mo.ui.text(placeholder="new-page-name", full_width=True)
    return (new_page_input,)


@app.cell
def name_hint_cell(new_page_input, click_empty, page_list):
    """Validation hint — separate cell so page_controls can set click_empty without self-loop."""
    _name = new_page_input.value.strip()
    _stem = _name.lower().replace(" ", "-") if _name else ""
    if _name and _stem in page_list():
        name_hint = mo.md("⚠️ *already exists*")
    elif not _name and click_empty():
        name_hint = mo.md("⚠️ *enter a name*")
    else:
        name_hint = mo.md("")
    return (name_hint,)


@app.cell
def edit_state():
    """
    State that drives the edit/view lifecycle:

    is_editing        — True while the editor is open.
    content_version   — Incremented after every save; forces current_page
                        to re-read from disk so view mode shows saved content.
    pending_new_stem  — Set to the stem of a page being created but not yet
                        saved. None means we are editing an existing page.
    """
    is_editing, set_is_editing = mo.state(False)
    content_version, set_content_version = mo.state(0)
    pending_new_stem, set_pending_new_stem = mo.state(None)
    return (
        content_version,
        is_editing,
        pending_new_stem,
        set_content_version,
        set_is_editing,
        set_pending_new_stem,
    )


@app.cell
def page_controls(
    new_page_input,
    clear_version,
    set_clear_version,
    set_click_empty,
    is_editing,
    page_list,
    set_is_editing,
    set_pending_new_stem,
    set_selected_page,
):
    """Left-panel widgets."""
    pages = page_list()

    def _create(_v):
        name = new_page_input.value.strip()
        stem = name.lower().replace(" ", "-") if name else ""
        if not name:
            set_click_empty(True)
            return
        if stem in page_list():
            return
        set_click_empty(False)
        set_clear_version(clear_version() + 1)
        set_pending_new_stem(stem)
        set_selected_page(stem)
        set_is_editing(True)

    _table_data = [{"Wiki Page": p} for p in pages] if pages else [{"Wiki Page": "(no pages)"}]
    page_selector = mo.ui.table(
        _table_data,
        selection="single",
        on_change=lambda rows: set_selected_page(rows[0]["Wiki Page"]) if rows else None,
    )
    create_btn = mo.ui.button(label="+ Create", kind="success", on_click=_create)
    refresh_btn = mo.ui.button(label="⟳ Refresh")
    return create_btn, page_selector, refresh_btn


@app.cell
def handle_refresh(refresh_btn, scan_pages, set_page_list):
    """Refresh the page list from disk."""
    if refresh_btn.value:
        set_page_list(scan_pages())
    return


@app.cell
def current_page(content_version, pending_new_stem, read_page, selected_page):
    """
    Load the selected page from disk.

    For pending new pages (not yet saved), returns a template instead of
    reading from disk. content_version forces re-reads after saves/cancels.
    """
    content_version()  # reactive dependency
    selected_stem = selected_page() if selected_page() else ""
    if selected_stem and pending_new_stem() == selected_stem:
        # New page draft — not on disk yet, show an empty template
        current_content = f"# {selected_stem.replace('-', ' ').title()}\n\n"
    else:
        current_content = read_page(selected_stem) if selected_stem else ""
    return current_content, selected_stem


@app.cell
def page_links_nav(current_content, page_list, set_selected_page):
    """Dropdown of internal wiki links on the current page for in-app navigation."""
    raw_links = re.findall(r'\[([^\]]+)\]\(([^)]+)\)', current_content or "")
    seen = set()
    valid = {}
    for _label, stem in raw_links:
        page = stem.removesuffix(".md")
        if page.startswith("http") or page.startswith("mailto"):
            continue
        if page in page_list() and page not in seen:
            seen.add(page)
            valid[page.replace("-", " ").title()] = page
    def _make_handler(page):
        def _go(_v):
            set_selected_page(page)
        return _go

    buttons = [
        mo.ui.button(label=title, on_click=_make_handler(page), kind="neutral")
        for title, page in valid.items()
    ]
    nav_widget = mo.hstack(buttons, wrap=True, gap=1) if buttons else mo.Html("")
    return (nav_widget,)


@app.cell
def edit_panel(
    content_version,
    current_content,
    page_list,
    pending_new_stem,
    scan_pages,
    selected_stem,
    set_content_version,
    set_is_editing,
    set_page_list,
    set_pending_new_stem,
    set_selected_page,
    write_page,
):
    """
    Manages the edit lifecycle: creates all four widgets fresh on every
    mode change. Save writes to disk; Cancel discards all changes.

    For new pages: Save writes the file and adds it to the list.
                   Cancel clears the draft and returns to the previous page.
    For existing pages: Cancel increments content_version so view mode
                        re-reads from disk (discarding any in-memory edits).
    """

    def _enter_edit(_v):
        set_is_editing(True)

    def _save(_v):
        if selected_stem:
            write_page(selected_stem, editor.value)
        if pending_new_stem():
            # New page just saved — add it to the page list
            set_page_list(scan_pages())
            set_pending_new_stem(None)
        # Increment version so current_page re-reads from disk
        set_content_version(content_version() + 1)
        set_is_editing(False)

    def _cancel(_v):
        if pending_new_stem():
            # Discard the new-page draft — go back to first existing page
            set_pending_new_stem(None)
            pages = page_list()
            set_selected_page(pages[0] if pages else None)
        else:
            # Existing page edit cancelled — force re-read so view mode
            # shows on-disk content, not the typed-but-unsaved text.
            set_content_version(content_version() + 1)
        set_is_editing(False)

    edit_btn = mo.ui.button(label="✏️ Edit", on_click=_enter_edit)
    editor = mo.ui.text_area(value=current_content, rows=28, full_width=True)
    save_btn = mo.ui.button(label="💾 Save", kind="success", on_click=_save)
    cancel_btn = mo.ui.button(label="✕ Cancel", on_click=_cancel)
    return cancel_btn, edit_btn, editor, save_btn


@app.cell
def left_panel(
    name_hint,
    create_btn,
    is_editing,
    new_page_input,
    page_selector,
    refresh_btn,
):
    """Navigation sidebar — column 0."""
    if is_editing():
        _view = mo.vstack(
            [
                mo.Html(
                    f'<div style="opacity:0.45;pointer-events:none;user-select:none">'
                    f'{page_selector}'
                    f'</div>'
                ),
                mo.callout(mo.md("🔒 Save or cancel to navigate"), kind="warn"),
            ],
            gap=2,
        )
    else:
        _view = mo.vstack(
            [
                page_selector,
                refresh_btn,
                mo.md("---"),
                new_page_input,
                mo.hstack([create_btn, name_hint], align="center", gap=2),
            ],
            gap=2,
        )
    _view
    return


@app.cell(column=1)
def middle_panel(
    cancel_btn,
    current_content,
    edit_btn,
    editor,
    is_editing,
    nav_widget,
    save_btn,
    selected_stem,
):
    """Content viewer / editor — column 1."""
    _title = selected_stem.replace("-", " ").title() if selected_stem else "No page selected"

    if is_editing():
        _header = mo.md(f"## {_title}")
        _content = mo.vstack(
            [editor, mo.hstack([save_btn, cancel_btn], gap=1)],
            gap=1,
        )
    else:
        _header = mo.hstack(
            [mo.md(f"## {_title}"), edit_btn],
            justify="space-between",
            align="center",
        )
        logger.debug(f"Rendering page: {selected_stem}")
        _text = (
            re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', current_content)
            if current_content
            else "*Select a page from the left panel.*"
        )
        _scroll = mo.Html(
            f'<div style="height:65vh; overflow-y:auto; padding-right:8px;">'
            f'{mo.md(_text).text}'
            f'</div>'
        )
        _content = mo.vstack([_scroll, nav_widget], gap=2)

    mo.vstack([_header, _content], gap=2)
    return


@app.cell(column=2)
def chat_panel():
    """AI chat assistant with FTS5 retrieval — column 2."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart, ModelResponse, TextPart

    async def respond(messages, config):
        history = []
        for msg in messages[:-1]:
            if msg.role == "user":
                history.append(ModelRequest(parts=[UserPromptPart(content=msg.content)]))
            elif msg.role == "assistant":
                history.append(ModelResponse(parts=[TextPart(content=msg.content)]))

        user_message = messages[-1].content

        async with wiki_agent.run_stream(
            user_message, deps=wiki_db_path, message_history=history
        ) as result:
            async for chunk in result.stream_text(delta=True):
                yield chunk

    _chat = mo.ui.chat(
        respond,
        prompts=wiki_chat_config.suggested_prompts,
        max_height=600,
    )
    mo.vstack([mo.md("### Chat with your Wiki"), _chat], gap=2)
    return


if __name__ == "__main__":
    app.run()
