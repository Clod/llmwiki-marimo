"""Chat route tests: a turn in each mode through the SSE stream, the thread,
clear and save. The agents are simulated; no model is called."""

from __future__ import annotations

import re
import subprocess

import pytest

from domain.chat.guardrail import REFUSAL_EN
from domain.tools.wiki_fs import create_page
from tests.web.conftest import SPANISH, WIKI_ID, fake_agents, sse_events

QUESTION = "What happened to Cinderella and her glass slipper?"


def ask(client, message=QUESTION, mode="pre-retrieval", conversation_id="c1", open_page=""):
    """POST a question; return the turn id the answer bubble streams from."""
    response = client.post(f"/w/{WIKI_ID}/chat", data={
        "message": message, "mode": mode, "conversation_id": conversation_id, "open_page": open_page})
    assert response.status_code == 200
    assert f'<div class="q">{message}</div>' in response.text
    match = re.search(r'sse-connect="/w/tales/chat/(\w+)/stream"', response.text)
    assert match, response.text
    return match.group(1)


def stream(client, turn_id):
    response = client.get(f"/w/{WIKI_ID}/chat/{turn_id}/stream")
    assert response.status_code == 200
    return sse_events(response.text)


def test_each_answer_carries_a_muted_line_with_its_mode_and_the_cited_page(client, agents):
    agents.agent_pre_retrieval.output = "Cinderella lost a glass slipper (Referencia: wiki/concepts/cinderella.md)."
    final = stream(client, ask(client))[0][1]
    meta = re.search(r'<div id="meta-\w+" hx-swap-oob="innerHTML">(.*?)</div>', final, re.S).group(1)
    assert meta.startswith("Recuperación previa · ")
    assert '<a class="mono" href="/w/tales/pages/concepts/cinderella"' in meta and ">concepts/cinderella</a>" in meta


def test_the_line_names_the_mode_used_and_has_no_link_when_nothing_is_cited(client, agents):
    agents.agent.output = "Nothing in the wiki."
    agents.agent.grounded = True
    final = stream(client, ask(client, mode="strict"))[0][1]
    meta = re.search(r'<div id="meta-\w+" hx-swap-oob="innerHTML">(.*?)</div>', final, re.S).group(1)
    assert meta.strip() == "Estricto"
    final = stream(client, ask(client, mode="streaming"))[-2][1]
    assert ">Sin verificación</div>" in final


def test_the_answer_bubble_is_followed_by_an_empty_line_for_the_meta(client):
    response = client.post(f"/w/{WIKI_ID}/chat", data={"message": QUESTION, "mode": "strict", "conversation_id": "c1"})
    turn = re.search(r'id="answer-(\w+)"', response.text).group(1)
    assert f'<div class="meta" id="meta-{turn}"></div>' in response.text


def test_the_thread_keeps_the_line_of_each_answer(client, agents):
    agents.agent_pre_retrieval.output = "Cinderella lost a glass slipper (Referencia: wiki/concepts/cinderella.md)."
    stream(client, ask(client))
    html = client.get(f"/w/{WIKI_ID}/chat/c1/thread").text
    assert '<div class="meta">Recuperación previa · <a class="mono"' in html


def test_pre_retrieval_turn_streams_the_rendered_answer(client, agents):
    agents.agent_pre_retrieval.output = "Cinderella lost a glass slipper (Referencia: wiki/concepts/cinderella.md)."
    events = stream(client, ask(client))
    names = [name for name, _ in events]
    assert names[-1] == "done" and names.count("chunk") == 1
    final = events[0][1]
    assert 'hx-swap-oob="innerHTML"' in final
    assert '<a href="/w/tales/pages/concepts/cinderella">wiki/concepts/cinderella.md</a>' in final
    assert len(agents.agent_pre_retrieval.prompts) == 1 and agents.agent.prompts == []


def test_pre_retrieval_turn_refuses_an_off_scope_question_before_the_model(client, agents):
    events = stream(client, ask(client, "Will it rain tomorrow in Rosario?"))
    assert "I cannot answer this question" in events[0][1]
    assert agents.agent_pre_retrieval.prompts == []


def test_strict_turn_without_tool_evidence_is_replaced_by_the_refusal(client, agents):
    events = stream(client, ask(client, "capital of France?", mode="strict"))
    assert REFUSAL_EN in events[0][1]
    assert agents.agent.prompts and agents.agent_pre_retrieval.prompts == []


def test_streaming_turn_sends_raw_chunks_then_the_rendered_answer(client, agents):
    agents.agent.output = "A **bold** answer <b>with markup</b>."
    events = stream(client, ask(client, mode="streaming"))
    names = [name for name, _ in events]
    assert names[-1] == "done" and names.count("chunk") == 3   # two chunks, then the rendered answer
    raw = events[0][1] + events[1][1]
    assert raw == "A **bold** answer &lt;b&gt;with markup&lt;/b&gt;."   # escaped: shown as text
    assert "<strong>bold</strong>" in events[2][1] and 'hx-swap-oob="innerHTML"' in events[2][1]


def test_the_open_page_follows_the_question_to_the_model(client, agents):
    stream(client, ask(client, mode="strict", open_page="concepts/prince"))
    assert "concepts/prince" in agents.agent.prompts[0] and QUESTION in agents.agent.prompts[0]
    stream(client, ask(client, mode="strict", open_page="", message="and then?"))
    assert agents.agent.prompts[1] == "and then?"


def test_a_follow_up_keeps_the_conversation(client, agents, app):
    stream(client, ask(client))
    stream(client, ask(client, "And what about the prince?"))
    messages = app.state.web.conversations[(WIKI_ID, "c1")]
    assert [m.role for m in messages] == ["user", "assistant", "user", "assistant"]


def test_a_conversation_belongs_to_its_wiki_and_its_id(client, agents, app):
    stream(client, ask(client, conversation_id="a"))
    assert (WIKI_ID, "b") not in app.state.web.conversations
    assert client.get(f"/w/{WIKI_ID}/chat/b/thread").text.count('class="q"') == 0


def test_an_unknown_mode_and_an_unknown_turn_are_refused(client):
    response = client.post(f"/w/{WIKI_ID}/chat", data={"message": "x", "mode": "other", "conversation_id": "c1"})
    assert response.status_code == 422
    assert client.get(f"/w/{WIKI_ID}/chat/nope/stream").status_code == 404


def test_a_failing_model_shows_a_spanish_error_and_forgets_the_question(client, agents, app):
    async def fail(*args, **kwargs):
        raise RuntimeError("connection refused")

    agents.agent_pre_retrieval.run = fail
    events = stream(client, ask(client))
    assert "No se pudo responder: connection refused" in events[0][1]
    assert events[-1][0] == "done"
    assert app.state.web.conversations[(WIKI_ID, "c1")] == []


# ── The conversation survives navigation (defect A.3.1) ─────────────────────

def test_the_thread_of_a_conversation_is_rendered_again_on_a_later_page(client, agents):
    agents.agent_pre_retrieval.output = "Cinderella lost it (Referencia: wiki/concepts/cinderella.md)."
    stream(client, ask(client))
    thread = client.get(f"/w/{WIKI_ID}/chat/c1/thread").text
    assert f'<div class="q">{QUESTION}</div>' in thread
    assert '<div class="a">' in thread
    assert '<a href="/w/tales/pages/concepts/cinderella">' in thread
    assert 'id="empty"' not in thread


def test_an_empty_conversation_renders_the_suggested_prompts(client, wiki):
    from domain.chat.config import load_config

    prompt = load_config(wiki.path).suggested_prompts[0]
    thread = client.get(f"/w/{WIKI_ID}/chat/c1/thread").text
    assert 'id="empty"' in thread and prompt in thread


def test_every_page_carries_the_hooks_that_restore_the_conversation(client):
    html = client.get(f"/w/{WIKI_ID}/pages/concepts/prince").text
    assert f'data-wiki="{WIKI_ID}"' in html and "data-conversation=" in html
    assert 'src="/static/js/reader.js"' in html


# ── Clear ────────────────────────────────────────────────────────────────────

def test_clear_forgets_the_messages_and_returns_the_empty_thread(client, agents, app):
    stream(client, ask(client))
    response = client.post(f"/w/{WIKI_ID}/chat/clear", data={"conversation_id": "c1"})
    assert response.status_code == 200 and 'id="empty"' in response.text
    assert (WIKI_ID, "c1") not in app.state.web.conversations
    assert 'class="q"' not in client.get(f"/w/{WIKI_ID}/chat/c1/thread").text


# ── Save: the draft, the review, the write ──────────────────────────────────

@pytest.fixture
def drafts(wiki, monkeypatch):
    """Replace the model call of the draft step. Counts the drafts; the write step is real."""
    import domain.chat.wiki_tools as wiki_tools
    from domain.tools.wiki_fs import read_page

    calls = []

    def draft_wiki_page(db_path, workspace, title, content, category, **kwargs):
        calls.append(dict(title=title, content=content, category=category))
        slug = "slipper-story"
        return wiki_tools.PageDraft(title, category, slug, f"wiki/concepts/{slug}.md",
                                    f"# {title}\n\nDRAFT: {content}",
                                    bool(read_page(db_path, workspace, "/wiki/concepts/", slug)))

    monkeypatch.setattr(wiki_tools, "draft_wiki_page", draft_wiki_page)
    return calls


def git_log(wiki) -> str:
    return subprocess.run(["git", "-C", str(wiki.path), "log", "--oneline"], capture_output=True, text=True).stdout


def tree(wiki) -> dict:
    return {str(p): p.read_bytes() for p in sorted(wiki.path.rglob("*")) if p.is_file() and ".git" not in p.parts}


def answered(client, agents):
    stream(client, ask(client))
    stream(client, ask(client, "And the prince?"))


def draft(client, title=" Slipper story ", open_page="concepts/prince"):
    return client.post(f"/w/{WIKI_ID}/chat/draft", data={
        "conversation_id": "c1", "title": title, "open_page": open_page})


def test_the_draft_opens_the_review_dialog_and_writes_nothing(client, agents, wiki, drafts):
    answered(client, agents)
    before, log = tree(wiki), git_log(wiki)
    response = draft(client)
    assert response.status_code == 200
    html = response.text
    assert '<dialog class="review"' in html and "Revisar la página" in html
    assert "<b>Slipper story</b>" in html and "wiki/concepts/slipper-story.md" in html
    assert "DRAFT: " in html and "And the prince?" in html           # the draft, in the editor
    assert 'name="markdown"' in html and "Vista previa" in html and "Guardar en la wiki" in html and "Cancelar" in html
    assert "Ya existe la página" not in html
    assert drafts[0]["title"] == "Slipper story" and drafts[0]["category"] == "concept"
    assert tree(wiki) == before and git_log(wiki) == log              # no file, no commit


def test_the_dialog_says_when_the_page_already_exists(client, agents, wiki, drafts):
    answered(client, agents)
    create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "slipper-story", "Slipper story", "Old text.", [])
    html = draft(client).text
    assert ("Ya existe la página «Slipper story». El borrador integra la conversación a esa página; "
            "al guardar, la reemplaza.") in html


def test_cancelling_leaves_the_wiki_unchanged(client, agents, wiki, drafts):
    """Cancel is a button of the dialog: the page never posts, so after a draft the wiki is as before."""
    answered(client, agents)
    before, log = tree(wiki), git_log(wiki)
    draft(client)
    assert tree(wiki) == before and git_log(wiki) == log
    assert 'onclick="this.closest(\'dialog\').close()"' in draft(client).text


def test_save_writes_exactly_the_text_the_user_edited(client, agents, wiki, drafts):
    answered(client, agents)
    calls = len(drafts)
    edited = "# Slipper story\n\nA sentence the user wrote in the dialog, not the model.\n"
    response = client.post(f"/w/{WIKI_ID}/chat/save", data={
        "title": " Slipper story ", "markdown": edited, "open_page": "concepts/prince"})
    assert response.status_code == 200
    page = (wiki.wiki_dir / "concepts" / "slipper-story.md").read_text()
    assert page.endswith(edited) and "DRAFT" not in page
    assert len(drafts) == calls                                       # saving does not call the model
    assert "chat: Slipper story" in git_log(wiki)
    # The notice replaces the dialog; the index comes back out of band.
    assert response.headers["hx-retarget"] == "#save-result" and response.headers["hx-reswap"] == "innerHTML"
    assert 'class="save-msg ok"' in response.text and "Se creó la página «Slipper story»." in response.text
    assert f'<a href="/w/{WIKI_ID}/pages/concepts/slipper-story">Abrir la página</a>' in response.text
    assert 'id="index-groups" hx-swap-oob="innerHTML"' in response.text
    assert ">Slipper story</a>" in response.text and 'class="current"' in response.text


def test_saving_over_an_existing_page_replaces_it(client, wiki, drafts):
    create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "slipper-story", "Slipper story", "Old text.", [])
    response = client.post(f"/w/{WIKI_ID}/chat/save", data={"title": "Slipper story", "markdown": "# S\n\nNew text.\n"})
    text = (wiki.wiki_dir / "concepts" / "slipper-story.md").read_text()
    assert "New text." in text and "Old text." not in text
    assert "Se actualizó la página «Slipper story»." in response.text


def test_a_failing_save_stays_in_the_dialog_so_the_edits_are_kept(client, wiki, monkeypatch):
    import web.routes.chat as chat_routes

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(chat_routes.chat_service, "save_reviewed_page", boom)
    response = client.post(f"/w/{WIKI_ID}/chat/save", data={"title": "T", "markdown": "# T\n\nText.\n"})
    assert "No se pudo guardar: disk full" in response.text
    assert "hx-retarget" not in response.headers


def test_saving_an_empty_page_or_with_a_blank_title_is_refused(client, wiki, drafts):
    before = tree(wiki)
    assert "La página no puede estar vacía." in client.post(
        f"/w/{WIKI_ID}/chat/save", data={"title": "T", "markdown": "  \n"}).text
    assert "La página no puede estar vacía." in client.post(
        f"/w/{WIKI_ID}/chat/save", data={"title": "  ", "markdown": "# x"}).text
    assert tree(wiki) == before


def test_the_preview_renders_the_draft_with_render_py(client):
    html = client.post(f"/w/{WIKI_ID}/chat/preview", data={
        "markdown": "---\ntitle: X\n---\n\n# Slipper\n\nSee [the prince](prince.md) and **bold**.",
        "page": "concepts/slipper-story"}).text
    assert "<h1>Slipper</h1>" in html and "<strong>bold</strong>" in html
    assert f'<a href="/w/{WIKI_ID}/pages/concepts/prince">the prince</a>' in html
    assert "title: X" not in html


def test_drafting_a_conversation_without_answers_shows_a_spanish_text(client, drafts):
    """Defect A.3.3: the service message is English; the page shows Spanish."""
    response = client.post(f"/w/{WIKI_ID}/chat/draft", data={"conversation_id": "unknown", "title": "T"})
    assert "No hay ninguna pregunta con respuesta para guardar." in response.text
    assert "no answered question" not in response.text and "<dialog" not in response.text
    assert drafts == []


def test_drafting_with_a_blank_title_is_refused(client, drafts):
    response = client.post(f"/w/{WIKI_ID}/chat/draft", data={"conversation_id": "c1", "title": "   "})
    assert "Escribí un título" in response.text and drafts == []


def test_a_failing_draft_shows_why_and_opens_no_dialog(client, agents, monkeypatch):
    import domain.chat.wiki_tools as wiki_tools

    def boom(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(wiki_tools, "draft_wiki_page", boom)
    answered(client, agents)
    response = draft(client)
    assert "No se pudo preparar el borrador: model unavailable" in response.text and "<dialog" not in response.text


def test_the_save_panel_asks_for_the_title_and_posts_to_the_draft_step(client):
    html = client.get(f"/w/{WIKI_ID}/pages/concepts/prince").text
    assert f'hx-post="/w/{WIKI_ID}/chat/draft"' in html and "Preparando el borrador…" in html


def test_a_conversation_uses_the_agents_of_its_wiki_once(app, client):
    calls = []
    app.state.web._agents_factory = lambda wiki: calls.append(wiki) or fake_agents("x")
    stream(client, ask(client))
    stream(client, ask(client, "again"))
    assert len(calls) == 1


def test_a_chat_without_a_model_configured_says_what_to_configure(settings):
    import dataclasses

    from fastapi.testclient import TestClient

    from web.app import create_app

    client = TestClient(create_app(dataclasses.replace(settings, llm_api_key="")), follow_redirects=False,
                        cookies=SPANISH)
    events = stream(client, ask(client))
    assert "Falta configurar el modelo: define LLM_API_KEY" in events[0][1]
