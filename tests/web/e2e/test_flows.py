"""Playwright flows of the web application, run against a copy of
`examples/finanzas-argentinas`.

The agents are simulated and the ingestion services are replaced (no model, no
Java, no LibreOffice). The tests skip when no browser is installed."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect

from services import ingest
from tests.web.e2e.server import WIKI_ID

CONCEPT = "concepts/plazo-fijo-uva"


def goto(page, live, path=""):
    page.goto(f"{live.url}/w/{WIKI_ID}/{path}")


def ask(page, text, mode=None):
    if mode:
        page.get_by_label("Modo").select_option(label=mode)
    page.locator("#composer textarea").fill(text)
    page.locator("#composer textarea").press("Enter")


def open_index(page):
    if page.locator("#index-btn").get_attribute("aria-pressed") != "true":
        page.locator("#index-btn").click()


def git_log(live) -> str:
    return subprocess.run(["git", "-C", str(live.wiki_dir), "log", "--oneline"],
                          capture_output=True, text=True).stdout


# ── 3.1 and 3.2: picker and navigation ───────────────────────────────────────

def test_picker_opens_the_overview(page, live):
    page.goto(live.url)
    expect(page.get_by_role("heading", name="Elegí una wiki")).to_be_visible()
    page.get_by_role("link", name=WIKI_ID).click()
    expect(page).to_have_url(f"{live.url}/w/{WIKI_ID}/pages/overview")


def test_navigation_by_index_by_link_and_by_back_button(page, live):
    goto(page, live, "pages/overview")
    open_index(page)
    page.locator(".index a", has_text="Plazo fijo UVA").click()
    expect(page).to_have_url(f"{live.url}/w/{WIKI_ID}/pages/{CONCEPT}")
    expect(page.locator(".index a.current")).to_have_text("Plazo fijo UVA")

    link = page.locator(".prose a[href*='/pages/']").first
    target = link.get_attribute("href")
    link.click()
    expect(page).to_have_url(f"{live.url}{target}")
    page.go_back()
    expect(page).to_have_url(f"{live.url}/w/{WIKI_ID}/pages/{CONCEPT}")


def test_a_page_that_does_not_exist(page, live):
    goto(page, live, "pages/concepts/no-existe")
    expect(page.get_by_text("La página no existe.")).to_be_visible()


# ── 3.3: the editor ──────────────────────────────────────────────────────────

def test_edit_a_page_keeps_the_front_matter_and_commits(page, live):
    path = live.wiki_dir / "wiki" / f"{CONCEPT}.md"
    front = path.read_text().split("---")[1]
    goto(page, live, f"pages/{CONCEPT}")
    page.get_by_role("link", name="Editar").click()
    textarea = page.locator("textarea[name=body]")
    assert "type: concept" not in textarea.input_value()
    expect(page.get_by_text("El bloque de front-matter no se edita: se conserva tal como está.")).to_be_visible()
    textarea.fill(textarea.input_value() + "\n\nUna línea nueva del editor.")
    page.locator("form button", has_text="Guardar").click()
    expect(page).to_have_url(f"{live.url}/w/{WIKI_ID}/pages/{CONCEPT}")
    expect(page.get_by_text("Una línea nueva del editor.")).to_be_visible()
    assert "edit: concepts/plazo-fijo-uva" in git_log(live)
    assert path.read_text().split("---")[1] == front


def test_the_preview_follows_what_is_typed_and_nothing_is_saved_until_guardar(page, live):
    path = live.wiki_dir / "wiki" / f"{CONCEPT}.md"
    before = path.read_text()
    goto(page, live, f"edit/{CONCEPT}")
    preview = page.locator("#preview")
    expect(preview.locator("h1, h2").first).to_be_visible()      # rendered when the page opens
    page.locator("textarea[name=body]").press_sequentially("\n\n## Texto en vivo\n\nEscrito ahora.")
    expect(preview.get_by_role("heading", name="Texto en vivo")).to_be_visible()
    expect(preview).to_contain_text("Escrito ahora.")
    assert path.read_text() == before


def test_two_tabs_editing_one_page_the_second_save_is_refused(context, live):
    first, second = context.new_page(), context.new_page()
    for tab in (first, second):
        tab.goto(f"{live.url}/w/{WIKI_ID}/edit/{CONCEPT}")
    first.locator("textarea[name=body]").fill("Primera versión.")
    first.locator("form button", has_text="Guardar").click()
    expect(first).to_have_url(f"{live.url}/w/{WIKI_ID}/pages/{CONCEPT}")
    second.locator("textarea[name=body]").fill("Segunda versión.")
    second.locator("form button", has_text="Guardar").click()
    expect(second.get_by_text("changed since it was opened (version")).to_be_visible()
    assert "Segunda versión." not in (live.wiki_dir / "wiki" / f"{CONCEPT}.md").read_text()


# ── 3.4: chat ────────────────────────────────────────────────────────────────

def test_the_three_modes_answer(page, live):
    goto(page, live, "pages/overview")
    ask(page, "¿Qué tasa de plazo fijo ofrece el Banco Galicia?")
    expect(page.locator("#messages .a td", has_text="36.5%")).to_be_visible()

    ask(page, "¿Qué es un plazo fijo UVA?", mode="Estricto")
    reference = page.locator("#messages .a a", has_text="wiki/concepts/plazo-fijo-uva.md")
    expect(reference).to_be_visible()
    reference.click()
    expect(page).to_have_url(f"{live.url}/w/{WIKI_ID}/pages/{CONCEPT}")


def test_the_streaming_mode_shows_the_text_in_pieces_then_the_formatted_answer(page, live):
    live.agents.agent.output = "Primera parte de la respuesta. Segunda parte de la respuesta con **negrita**."
    goto(page, live, "pages/overview")
    ask(page, "¿Qué es una caución bursátil?", mode="Sin verificación")
    answer = page.locator("#messages .a")
    # The first piece arrives before the second: the delay between them is one second.
    page.wait_for_function(
        "() => { const a = document.querySelector('#messages .a');"
        " return a && a.textContent.includes('Primera') && !a.textContent.includes('negrita'); }")
    expect(answer.locator("strong")).to_have_text("negrita")


def test_a_follow_up_question_keeps_the_conversation(page, live):
    goto(page, live, "pages/overview")
    ask(page, "¿Qué es una caución bursátil?")
    expect(page.locator("#messages .a").first).to_have_text("Una caución bursátil es un préstamo garantizado con títulos.")
    ask(page, "¿Y cuánto rinde?")
    expect(page.locator("#messages .a").nth(1)).to_have_text("Rinde según la tasa vigente.")
    assert len(live.agents.agent_pre_retrieval.prompts) == 2
    # The server holds both exchanges of the one conversation.
    (messages,) = live.app.state.web.conversations.values()
    assert [m.role for m in messages] == ["user", "assistant", "user", "assistant"]


AT_END = "t => t.scrollHeight - t.scrollTop - t.clientHeight <= 48"


def test_the_thread_follows_the_answers_until_the_user_scrolls_up(page, live):
    page.set_viewport_size({"width": 1440, "height": 600})
    goto(page, live, "pages/overview")
    thread = page.locator("#messages")
    for n in range(1, 6):
        ask(page, "¿Qué es una caución bursátil?")
        expect(page.locator("#messages .a").nth(n - 1)).to_have_text("Una caución bursátil es un préstamo garantizado con títulos.")
    assert thread.evaluate("t => t.scrollHeight > t.clientHeight"), "five answers overflow the thread at 600 px"
    page.wait_for_function(f"({AT_END})(document.getElementById('messages'))")
    expect(page.locator("#messages .a").last).to_be_in_viewport()

    # Scrolled up, new text at the end does not move the thread.
    thread.evaluate("t => { t.scrollTop = 0; t.dispatchEvent(new Event('scroll')); }")
    thread.evaluate("t => [...t.querySelectorAll('.a')].at(-1).append(' más texto')")
    page.wait_for_timeout(100)
    assert thread.evaluate("t => t.scrollTop") == 0

    # A new question resumes the following.
    ask(page, "¿Qué es una caución bursátil?")
    expect(page.locator("#messages .a")).to_have_count(6)
    page.wait_for_function(f"({AT_END})(document.getElementById('messages'))")


def test_the_conversation_survives_navigation_and_reload(page, live):
    """Defect A.3.1: following a link does not empty the chat."""
    goto(page, live, "pages/overview")
    ask(page, "¿Qué tasa de plazo fijo ofrece el Banco Galicia?")
    expect(page.locator("#messages .a td", has_text="36.5%")).to_be_visible()

    open_index(page)
    page.locator(".index a", has_text="Plazo fijo UVA").click()
    expect(page).to_have_url(f"{live.url}/w/{WIKI_ID}/pages/{CONCEPT}")
    expect(page.locator("#messages .q")).to_have_text("¿Qué tasa de plazo fijo ofrece el Banco Galicia?")
    expect(page.locator("#messages .a td", has_text="36.5%")).to_be_visible()
    expect(page.locator("#save-btn")).to_be_enabled()

    # A follow-up on the new page joins the same conversation, and the open page follows the page in view.
    ask(page, "¿Y cuánto rinde?")
    expect(page.locator("#messages .a")).to_have_count(2)
    page.reload()
    expect(page.locator("#messages .q")).to_have_count(2)


def test_clear_empties_the_conversation_and_disables_the_buttons(page, live):
    goto(page, live, "pages/overview")
    ask(page, "¿Qué es una caución bursátil?")
    expect(page.locator("#messages .a")).to_have_count(1)
    page.get_by_role("button", name="Limpiar").click()
    expect(page.locator("#messages .q")).to_have_count(0)
    expect(page.locator("#save-btn")).to_be_disabled()
    page.reload()
    expect(page.locator("#messages .q")).to_have_count(0)


@pytest.fixture
def drafts(monkeypatch):
    """The draft step without the model: the page is the conversation under a heading."""
    import domain.chat.wiki_tools as wiki_tools
    from domain.tools.wiki_fs import read_page

    def draft_wiki_page(db_path, workspace, title, content, category, **kwargs):
        slug = "mi-conversacion"
        exists = bool(read_page(db_path, workspace, "/wiki/concepts/", slug))
        return wiki_tools.PageDraft(title, category, slug, f"wiki/concepts/{slug}.md",
                                    f"# {title}\n\nUna frase del borrador.\n\n{content}", exists)

    monkeypatch.setattr(wiki_tools, "draft_wiki_page", draft_wiki_page)


def open_review(page, title="Mi conversación"):
    ask(page, "¿Qué es una caución bursátil?")
    expect(page.locator("#messages .a")).to_have_count(1)
    expect(page.locator("#save-btn")).to_be_enabled()
    page.locator("#save-btn").click()
    page.locator("#save-panel input[name=title]").fill(title)
    page.locator("#save-panel button.primary").click()
    dialog = page.locator("dialog.review")
    expect(dialog).to_be_visible()
    return dialog


def test_save_the_conversation_after_editing_the_draft_in_the_dialog(page, live, drafts):
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))   # a browser alert would land here
    goto(page, live, "pages/overview")
    review = open_review(page)
    editor = review.locator("textarea[name=markdown]")
    expect(editor).to_be_focused()                                          # focus moves into the dialog
    assert "Una frase del borrador." in editor.input_value()
    editor.fill(editor.input_value().replace("Una frase del borrador.", "Una frase que escribí yo."))

    # The preview renders the edited text.
    review.get_by_role("button", name="Vista previa").click()
    expect(review.locator(".review-preview")).to_contain_text("Una frase que escribí yo.")
    review.get_by_role("button", name="Editar").click()
    expect(editor).to_be_visible()

    review.get_by_role("button", name="Guardar en la wiki").click()
    expect(page.locator("#save-result .ok")).to_contain_text("Se creó la página «Mi conversación».")
    expect(page.locator("dialog.review")).to_have_count(0)
    expect(page.locator("#save-btn")).to_be_focused()                        # focus returns to "Guardar"
    open_index(page)
    expect(page.locator(".index a", has_text="Mi conversación")).to_have_count(1)

    page.get_by_role("link", name="Abrir la página").click()
    expect(page).to_have_url(f"{live.url}/w/{WIKI_ID}/pages/concepts/mi-conversacion")
    expect(page.get_by_text("Una frase que escribí yo.")).to_be_visible()
    assert "Una frase del borrador." not in (live.wiki_dir / "wiki/concepts/mi-conversacion.md").read_text()
    assert dialogs == []


def test_cancel_and_escape_close_the_dialog_and_write_nothing(page, live, drafts):
    goto(page, live, "pages/overview")
    review = open_review(page)
    review.get_by_role("button", name="Cancelar").click()
    expect(page.locator("dialog.review")).to_have_count(0)
    expect(page.locator("#save-btn")).to_be_focused()
    assert not (live.wiki_dir / "wiki/concepts/mi-conversacion.md").exists()

    page.locator("#save-panel button.primary").click()
    expect(page.locator("dialog.review")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("dialog.review")).to_have_count(0)
    expect(page.locator("#save-btn")).to_be_focused()
    assert not (live.wiki_dir / "wiki/concepts/mi-conversacion.md").exists()
    assert "chat: " not in git_log(live)


def test_the_dialog_warns_when_the_page_exists(page, live, drafts):
    from domain.tools.wiki_fs import create_page

    wiki = live.wiki()
    create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "mi-conversacion", "Mi conversación", "Texto viejo.", [])
    goto(page, live, "pages/overview")
    review = open_review(page)
    expect(review.locator(".notice")).to_have_text(
        "Ya existe la página «Mi conversación». El borrador integra la conversación a esa página; al guardar, la reemplaza.")


# ── 3.5: refusals, produced by the code before any model call ───────────────

def test_the_pre_retrieval_mode_states_why_it_refuses(page, live):
    goto(page, live, "pages/overview")
    ask(page, "¿Qué son los CEDEARs y conviene comprarlos?")
    expect(page.locator("#messages .a")).to_contain_text(
        "No puedo responder esta pregunta: menciona «cedears», que está en la lista de temas "
        "sobre los que esta wiki no tiene permitido responder.")
    assert live.agents.agent_pre_retrieval.prompts == []


def test_a_question_outside_the_topics_of_the_wiki_is_refused_with_no_page_open(page, live):
    """A page in view makes the question covered (`preretrieval.py`, node Q5a), so the
    out-of-scope refusal of the test plan, §3.5.2, shows only with no page open: here,
    on the screen of a page that does not exist."""
    goto(page, live, "pages/concepts/no-existe")
    ask(page, "¿Va a llover mañana en Rosario?")
    expect(page.locator("#messages .a")).to_contain_text(
        "No puedo responder esta pregunta: no nombra ninguno de los temas de esta wiki.")
    assert live.agents.agent_pre_retrieval.prompts == []


# ── 3.6: ingestion ───────────────────────────────────────────────────────────

def test_the_progress_console_follows_the_lines_until_the_user_scrolls_up(page, live, monkeypatch):
    def fake_ingest(wiki, uploads, client, model, *, full_repair, progress):
        for n in range(40):
            progress(f"🔍 Paso {n + 1}")
        return [SimpleNamespace(status="skipped", doc_id=None)]

    monkeypatch.setattr(ingest, "ingest_uploads", fake_ingest)
    page.goto(f"{live.url}/w/{WIKI_ID}/ingest")
    page.locator("input[type=file]").set_input_files(
        {"name": "Uno.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4"})
    page.get_by_role("button", name="Ingestar", exact=True).click()
    log = page.locator("#progress .log")
    expect(log).to_contain_text("🏁")
    assert log.evaluate("l => l.scrollHeight > l.clientHeight"), "forty lines overflow the console"
    page.wait_for_function(f"({AT_END})(document.querySelector('#progress .log'))")
    expect(log.locator("li").last).to_be_in_viewport()

    # Scrolled up, a new line does not move the log.
    log.evaluate("l => { l.scrollTop = 0; l.dispatchEvent(new Event('scroll')); }")
    log.evaluate("l => l.append(Object.assign(document.createElement('li'), {textContent: 'otra línea'}))")
    page.wait_for_timeout(100)
    assert log.evaluate("l => l.scrollTop") == 0


def test_ingest_streams_the_log_refuses_a_second_run_and_lists_the_source(page, context, live, monkeypatch):
    import threading

    started, release = threading.Event(), threading.Event()

    def fake_ingest(wiki, uploads, client, model, *, full_repair, progress):
        results = []
        for upload in uploads:
            if (wiki.sources_dir / upload.name).exists():
                progress(f"⏭ {upload.name} — already up to date")
                results.append(SimpleNamespace(status="skipped", doc_id=None))
                continue
            progress(f"🔍 Validating {upload.name}")
            started.set()
            assert release.wait(20)
            (wiki.sources_dir / upload.name).write_bytes(upload.contents)
            from domain.tools.db import get_connection
            with get_connection(wiki.db_path) as conn:
                conn.execute(
                    "INSERT INTO documents (id, user_id, filename, relative_path, path, source_kind, file_type, "
                    "status, page_count) VALUES ('new-1', 'local', ?, ?, 'sources/', 'source', 'pdf', 'ready', 2)",
                    (upload.name, f"sources/{upload.name}"))
                conn.commit()
            progress(f"✅ {upload.name}")
            results.append(SimpleNamespace(status="ingested", doc_id="new-1"))
        return results

    monkeypatch.setattr(ingest, "ingest_uploads", fake_ingest)
    pdf = {"name": "Little Red Riding Hood.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4"}

    page.goto(f"{live.url}/w/{WIKI_ID}/")
    page.get_by_role("link", name="Ingestar").click()
    expect(page).to_have_url(f"{live.url}/w/{WIKI_ID}/ingest")
    page.locator("input[type=file]").set_input_files(pdf)
    page.get_by_role("button", name="Ingestar", exact=True).click()
    expect(page.locator("#progress .log")).to_contain_text("🔍 Validating Little Red Riding Hood.pdf")

    # A second ingestion from another tab, while the first one runs.
    other = context.new_page()
    other.goto(f"{live.url}/w/{WIKI_ID}/ingest")
    other.locator("input[type=file]").set_input_files(pdf)
    other.get_by_role("button", name="Ingestar", exact=True).click()
    expect(other.get_by_text("Hay otra operación en curso sobre esta wiki.")).to_be_visible()

    release.set()
    expect(page.locator("#progress .log")).to_contain_text("🏁 Ingesta: 1 ingestados, 0 omitidos, 0 fallidos")

    # The sources panel reloads itself when the operation ends: no page reload.
    expect(page.locator("#h-sources")).to_contain_text("Fuentes · ")
    row = page.locator("table.sources tr", has_text="Little Red Riding Hood.pdf")
    expect(row.locator(".pill")).to_have_text("lista")   # the status is translated since the i18n branch

    # The same file again is skipped.
    page.locator("input[type=file]").set_input_files(pdf)
    page.get_by_role("button", name="Ingestar", exact=True).click()
    expect(page.locator("#progress .log")).to_contain_text("⏭ Little Red Riding Hood.pdf — already up to date")
    expect(page.locator("#progress .log")).to_contain_text("🏁 Ingesta: 0 ingestados, 1 omitidos, 0 fallidos")


def test_a_md_file_is_uploaded_ingested_and_opened_as_a_source(page, live, monkeypatch):
    """The ingestion services are simulated: the upload filter, the screen and the
    source view are the real ones."""
    from domain.tools.db import get_connection

    def fake_ingest(wiki, uploads, client, model, *, full_repair, progress):
        for upload in uploads:
            (wiki.sources_dir / upload.name).write_bytes(upload.contents)
            with get_connection(wiki.db_path) as conn:
                conn.execute(
                    "INSERT INTO documents (id, user_id, filename, relative_path, path, source_kind, file_type, "
                    "status, page_count, parser) VALUES ('md-1', 'local', ?, ?, 'sources/', 'source', 'md', "
                    "'ready', 1, 'text:utf-8')", (upload.name, f"sources/{upload.name}"))
                conn.commit()
            progress(f"✅ {upload.name}")
        return [SimpleNamespace(status="ingested", doc_id="md-1")]

    monkeypatch.setattr(ingest, "ingest_uploads", fake_ingest)
    goto(page, live, "ingest")
    expect(page.locator("label.drop")).to_contain_text("DOC, DOCX, MD, ODT, PDF, RTF o TXT")
    note = {"name": "notas.md", "mimeType": "text/markdown",
            "buffer": "---\ntitle: x\n---\n# Notas de campo\n\nUn **texto** cualquiera.\n".encode()}
    page.locator("input[type=file]").set_input_files(note)
    page.get_by_role("button", name="Ingestar", exact=True).click()
    expect(page.locator("#progress .log")).to_contain_text("🏁 Ingesta: 1 ingestados, 0 omitidos, 0 fallidos")

    page.reload()
    row = page.locator("table.sources tr", has_text="notas.md")
    expect(row.locator(".pill")).to_have_text("lista")   # the status is translated since the i18n branch
    with page.context.expect_page() as opened:
        row.get_by_role("link", name="notas.md").click()
    view = opened.value
    expect(view.locator(".prose h1")).to_have_text("Notas de campo")
    expect(view.locator(".prose strong")).to_have_text("texto")
    expect(view.locator("main.reader")).to_be_visible()
    expect(view.get_by_role("link", name="Editar")).to_have_count(0)
    expect(view.get_by_role("button", name="Borrar")).to_have_count(0)


# ── In-page confirmation, never a browser dialog ─────────────────────────────

def test_destructive_actions_ask_inside_the_page(page, live):
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))

    goto(page, live, "pages/concepts/byma")
    page.get_by_role("button", name="Borrar").click()
    panel = page.locator("#confirm-delete")
    expect(panel).to_be_visible()
    panel.get_by_role("button", name="Cancelar").click()
    expect(panel).to_be_hidden()
    assert (live.wiki_dir / "wiki/concepts/byma.md").exists()
    page.get_by_role("button", name="Borrar").click()
    panel.get_by_role("button", name="Borrar la página").click()
    expect(page).to_have_url(f"{live.url}/w/{WIKI_ID}/pages/overview")   # the ?deleted= notice is shown, then dropped from the URL
    expect(page.get_by_text("Se borró la página «BYMA».")).to_be_visible()
    assert not (live.wiki_dir / "wiki/concepts/byma.md").exists()

    goto(page, live, "ingest")
    page.locator("table.sources tr", has_text="01 Acciones Locales.docx").get_by_role("button", name="Borrar").click()
    expect(page.locator("#confirm-src-1")).to_be_visible()
    goto(page, live, "maintain")
    page.get_by_role("button", name="Revisar y reparar la wiki").click()
    expect(page.locator("#confirm-lint")).to_be_visible()
    page.locator("#confirm-lint").get_by_role("button", name="Cancelar").click()
    page.get_by_role("button", name="Reconstruir el índice").click()
    expect(page.locator("#confirm-reindex")).to_be_visible()
    assert dialogs == []


# ── The reading layout ───────────────────────────────────────────────────────

def test_layout_toggles_are_remembered_and_the_index_is_a_drawer_when_narrow(page, live):
    goto(page, live, "pages/overview")
    body = page.locator("body")
    assert "index-open" not in (body.get_attribute("class") or "")          # hidden by default
    expect(page.locator("#chat")).to_be_visible()

    page.locator("#index-btn").click()
    expect(body).to_have_class("index-open")
    page.locator("#chat-btn").click()
    expect(page.locator("#chat")).to_be_hidden()
    page.reload()
    expect(body).to_have_class("index-open chat-hidden")                     # both remembered

    page.locator("#chat-btn").click()
    page.set_viewport_size({"width": 900, "height": 800})
    page.reload()
    expect(page.locator("#chat")).to_be_visible()                            # the chat keeps its column
    expect(page.locator("#index")).not_to_be_in_viewport()                   # the index is a drawer, closed
    page.locator("#index-btn").click()
    expect(page.locator("#index")).to_be_in_viewport()


def test_rebuild_the_index_from_the_maintenance_screen(page, live, monkeypatch):
    """The rebuild runs for real over the copy of the finance example; only the
    extractor is replaced, since it needs Java and LibreOffice."""
    from domain.ingestion import extractor

    monkeypatch.setattr(extractor, "extract", lambda path, cache_dir: ([(1, f"Texto de {path.name}.")], "fake"))
    before = {r["relative_path"] for r in ingest_rows(live)}
    goto(page, live, "maintain")
    page.get_by_role("button", name="Reconstruir el índice").click()
    page.locator("#confirm-reindex").get_by_role("button", name="Reconstruir", exact=True).click()
    expect(page.locator("#progress .log")).to_contain_text("🏁 Índice reconstruido: 6 fuentes, 35 páginas")
    assert {r["relative_path"] for r in ingest_rows(live)} == before
    assert (live.wiki_dir / ".llmwiki" / "index.db.bak").is_file()


def ingest_rows(live):
    from domain.tools.db import get_connection

    with get_connection(live.wiki().db_path) as conn:
        return [dict(r) for r in conn.execute("SELECT relative_path FROM documents")]


# ── The relations screen ─────────────────────────────────────────────────────

def relations(page, live, query=""):
    page.goto(f"{live.url}/w/{WIKI_ID}/relations{query}")
    page.wait_for_selector("#relations[data-ready='1']")
    page.wait_for_function("document.getElementById('relations').dataset.settled === '1'")


def click_node(page, node_id):
    x, y = page.evaluate("id => { const p = window.relationsGraph.screenOf(id); return [p.x, p.y]; }", node_id)
    box = page.locator("#stage canvas").first.bounding_box()
    page.mouse.move(box["x"] + x - 3, box["y"] + y)
    page.mouse.move(box["x"] + x, box["y"] + y)
    page.wait_for_timeout(250)   # the canvas picks the hovered node on the next frame
    page.mouse.click(box["x"] + x, box["y"] + y)


def test_the_zoom_buttons_change_the_zoom_and_fit_returns_to_the_whole_graph(page, live):
    relations(page, live)
    zoom = lambda: page.evaluate("window.relationsGraph.fg.zoom()")   # noqa: E731
    fitted = zoom()
    for _ in range(20):   # the fit after the settle takes a moment: wait until the zoom stops changing
        page.wait_for_timeout(300)
        if abs(zoom() - fitted) < 1e-6:
            break
        fitted = zoom()
    page.get_by_role("button", name="Acercar").click()
    page.wait_for_function(f"window.relationsGraph.fg.zoom() > {fitted * 1.4}")
    page.get_by_role("button", name="Alejar").click()
    page.wait_for_function(f"Math.abs(window.relationsGraph.fg.zoom() - {fitted}) < {fitted * 0.03}")   # the transition ends
    page.get_by_role("button", name="Alejar").click()
    page.wait_for_function(f"window.relationsGraph.fg.zoom() < {fitted * 0.8}")
    page.get_by_role("button", name="Encuadrar").click()
    page.wait_for_function(f"Math.abs(window.relationsGraph.fg.zoom() - {fitted}) < {fitted * 0.25}")


def test_the_relations_tab_search_and_click_open_the_reading_screen(page, live):
    goto(page, live)
    page.get_by_role("link", name="Ver relaciones").click()
    page.wait_for_selector("#relations[data-ready='1']")
    expect(page.locator("#counts")).to_contain_text("nodos")
    page.locator("#search").fill("Plazo fijo UVA")
    page.locator("#search").press("Enter")
    page.wait_for_function("document.getElementById('relations').dataset.settled === '1'")
    page.wait_for_timeout(600)   # the centring transition
    click_node(page, CONCEPT)
    page.wait_for_url(f"**/w/{WIKI_ID}/pages/{CONCEPT}")
    expect(page.locator(".prose")).to_be_visible()


def test_the_sources_are_hidden_until_the_toggle_and_the_local_graph_is_small(page, live):
    relations(page, live)
    before = int(page.locator("#relations").get_attribute("data-nodes"))
    page.get_by_label("Mostrar fuentes").check()
    assert int(page.locator("#relations").get_attribute("data-nodes")) > before
    relations(page, live, f"?page={CONCEPT}&depth=1")
    local = int(page.locator("#relations").get_attribute("data-nodes"))
    assert 1 <= local < before


def test_the_reading_screen_links_to_the_local_graph(page, live):
    goto(page, live, f"pages/{CONCEPT}")
    page.get_by_role("link", name="Relaciones", exact=True).click()
    page.wait_for_selector("#relations[data-ready='1']")
    assert f"page={CONCEPT}" in page.url


@pytest.mark.parametrize("height", [900, 700])
def test_ingest_takes_the_window_height_and_the_sources_table_scrolls(page, live, height):
    from domain.tools.db import get_connection
    with get_connection(live.wiki_dir / ".llmwiki" / "index.db") as conn:
        for n in range(40):
            conn.execute(
                "INSERT INTO documents (id, user_id, filename, relative_path, path, source_kind, file_type, status, "
                "page_count) VALUES (?, 'local', ?, ?, 'sources/', 'source', 'pdf', 'ready', 1)",
                (f"extra-{n}", f"Extra {n}.pdf", f"sources/Extra {n}.pdf"))
        conn.commit()
    page.set_viewport_size({"width": 1440, "height": height})
    page.goto(f"{live.url}/w/{WIKI_ID}/ingest")
    assert page.evaluate("document.documentElement.scrollHeight") == height    # the page does not scroll
    panel = page.locator("#sources-panel").bounding_box()
    assert panel["y"] + panel["height"] <= height, "the sources panel ends inside the window"
    scroll = page.locator(".sources-scroll")
    client, total = scroll.evaluate("e => [e.clientHeight, e.scrollHeight]")
    assert client < total, "forty more sources scroll inside the table"
    scroll.evaluate("e => e.scrollTop = e.scrollHeight")
    expect(page.locator(".sources thead th").first).to_be_in_viewport()   # the head stays at the top


@pytest.mark.parametrize("path, text", [
    ("ingest?notice=Se%20borr%C3%B3%20la%20fuente", "Se borró la fuente"),
    ("maintain?notice=Listo", "Listo"),
    ("pages/overview?deleted=BYMA", "Se borró la página «BYMA»."),
])
def test_an_action_notice_closes_with_its_button_and_does_not_come_back_on_reload(page, live, path, text):
    goto(page, live, path)
    notice = page.locator(".notice.dismissible")
    expect(notice).to_contain_text(text)
    page.wait_for_function("!location.search.includes('notice=') && !location.search.includes('deleted=')")
    notice.get_by_role("button", name="Cerrar el aviso").click()
    expect(notice).to_have_count(0)
    page.reload()
    expect(page.locator(".notice.dismissible")).to_have_count(0)


def test_deleting_a_source_shows_its_steps_in_the_console_and_refreshes_the_sources(page, live):
    goto(page, live, "ingest")
    row = page.locator("table.sources tr", has_text="01 Acciones Locales.docx")
    count = page.locator("#h-sources").inner_text()
    row.get_by_role("button", name="Borrar").click()
    confirm = page.locator("#confirm-src-1")
    expect(confirm).to_be_visible()
    page.wait_for_function("!document.querySelector('.htmx-request, .htmx-settling, .htmx-swapping')")
    confirm.get_by_role("button", name="Borrar la fuente").click()
    log = page.locator("#progress .log")
    expect(log).to_contain_text("🗑 Borrando la fuente «01 Acciones Locales.docx»")
    expect(log).to_contain_text("🏁 Se borró la fuente «01 Acciones Locales.docx».")
    expect(page.locator("table.sources tr", has_text="01 Acciones Locales.docx")).to_have_count(0)
    expect(page.locator("#h-sources")).not_to_have_text(count)       # the panel reloaded itself
    expect(page).to_have_url(f"{live.url}/w/{WIKI_ID}/ingest")       # no reload, no notice
