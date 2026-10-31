"""Sentence verification in the chat: the checkbox, the extra SSE event, the
marks on the answer. The agents and the verifier are simulated; no model runs."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from domain.verify import FakeVerifier, nli
from tests.web.conftest import SPANISH, WIKI_ID, fake_agents
from tests.web.test_chat import drafts, stream  # noqa: F401 — drafts is a fixture
from web.app import create_app
from web.render import render_verified

# The tooltip of a marked sentence, as the Spanish interface the tests run with writes it.
UNSUPPORTED_TIP = "Ninguna de las páginas que leyó el modelo respalda esta oración"

SUPPORTED = "Cinderella lost a glass slipper at the royal ball."
INVENTED = "The prince married a mermaid in Atlantis."
ANSWER = f"{SUPPORTED} {INVENTED}"


@pytest.fixture
def verifier():
    return FakeVerifier()


@pytest.fixture
def agents():
    return fake_agents(ANSWER)


@pytest.fixture
def app(settings, agents, verifier):
    return create_app(settings, agents_factory=lambda wiki: agents, verifier=verifier)


def ask_verified(client, mode="pre-retrieval", verify="1"):
    response = client.post(f"/w/{WIKI_ID}/chat", data={
        "message": "What happened to Cinderella and her glass slipper?", "mode": mode,
        "conversation_id": "c1", "open_page": "", **({"verify": verify} if verify else {})})
    return re.search(r'sse-connect="/w/tales/chat/(\w+)/stream"', response.text).group(1)


def test_the_answer_arrives_first_and_the_verification_follows_before_done(client, verifier):
    events = stream(client, ask_verified(client))
    assert [name for name, _ in events] == ["chunk", "verification", "done"]
    first, verification = events[0][1], events[1][1]
    assert SUPPORTED in first and "<mark" not in first and "Verificando las oraciones" in first
    assert 'hx-swap-oob="innerHTML"' in verification
    assert f'<mark class="unsupported" title="{UNSUPPORTED_TIP}">{INVENTED}</mark>' in verification
    assert f"<mark class=\"unsupported\" title=\"{UNSUPPORTED_TIP}\">{SUPPORTED}" not in verification
    assert "1 de 2 oraciones sin respaldo" in verification


def test_the_verification_reads_the_pages_the_model_read(client, verifier):
    stream(client, ask_verified(client))
    passages = " ".join(passage for call in verifier.calls for passage, _ in call)
    assert "glass slipper" in passages


def test_the_conversation_keeps_the_answer_without_marks(client, app):
    stream(client, ask_verified(client))
    messages = app.state.web.conversations[(WIKI_ID, "c1")]
    assert messages[-1].content.startswith(f"{ANSWER}\n\nReference: apparently used:")
    assert "<mark" not in messages[-1].content


def test_the_verification_splits_the_injected_pages_into_used_and_not_used(client):
    events = stream(client, ask_verified(client))
    first, verification = events[0][1], events[1][1]
    assert "Reference: undetermined." in first
    assert "Reference: undetermined." not in verification
    assert "Reference: apparently used:" in verification
    assert "Apparently not used:" in verification


def test_the_strict_mode_is_verified_against_its_tool_returns(client, agents, verifier):
    agents.agent.grounded = True
    agents.agent.output = "Texto de la página de la wiki. Una oración inventada sobre sirenas marinas."
    events = stream(client, ask_verified(client, mode="strict"))
    assert [name for name, _ in events] == ["chunk", "verification", "done"]
    assert "1 de 2 oraciones sin respaldo" in events[1][1]


def test_an_unchecked_box_runs_no_verification(client, verifier):
    events = stream(client, ask_verified(client, verify=""))
    assert [name for name, _ in events] == ["chunk", "done"]
    assert verifier.calls == []


def test_the_streaming_mode_is_never_verified(client, verifier):
    events = stream(client, ask_verified(client, mode="streaming"))
    assert "verification" not in [name for name, _ in events]
    assert verifier.calls == []


def test_a_refusal_has_no_sentences_to_verify(client):
    response = client.post(f"/w/{WIKI_ID}/chat", data={
        "message": "Will it rain tomorrow in Rosario?", "mode": "pre-retrieval",
        "conversation_id": "c1", "open_page": "", "verify": "1"})
    turn = re.search(r'sse-connect="/w/tales/chat/(\w+)/stream"', response.text).group(1)
    events = stream(client, turn)
    assert "I received no information from the wiki" in events[0][1]
    assert "0 de 0 oraciones sin respaldo" in events[1][1] and "<mark" not in events[1][1]


def test_a_failed_verification_leaves_the_answer_and_says_so(settings, agents):
    class Broken(FakeVerifier):
        def entailment(self, pairs):
            raise RuntimeError("model not loaded")

    app = create_app(settings, agents_factory=lambda wiki: agents, verifier=Broken())
    client = TestClient(app, cookies=SPANISH)
    events = stream(client, ask_verified(client))
    assert [name for name, _ in events] == ["chunk", "verification", "done"]
    assert SUPPORTED in events[1][1] and "<mark" not in events[1][1]
    assert "No se pudo verificar" in events[1][1]


def test_the_threshold_of_the_wiki_config_decides(settings, agents, home):
    (home / WIKI_ID / "wiki_config.toml").write_text("[verify]\nthreshold = 0.1\n", encoding="utf-8")
    from domain.chat.config import load_config

    agents.config.verify_threshold = load_config(home / WIKI_ID).verify_threshold
    app = create_app(settings, agents_factory=lambda wiki: agents, verifier=FakeVerifier())
    client = TestClient(app, cookies=SPANISH)
    events = stream(client, ask_verified(client))
    assert "0 de 2 oraciones sin respaldo" in events[1][1]     # 0.25 overlap reaches 0.1


# ── The checkbox ─────────────────────────────────────────────────────────────

def test_the_checkbox_is_offered_when_a_verifier_is_available(client):
    page = client.get(f"/w/{WIKI_ID}/pages/overview").text
    assert "Verificar las oraciones" in page
    assert re.search(r'<input type="checkbox" name="verify" id="verify-box" value="1">', page)


def test_the_checkbox_is_disabled_when_the_local_verifier_is_not_installed(settings, agents, monkeypatch):
    monkeypatch.setattr(nli, "is_available", lambda *a, **k: False)
    app = create_app(settings, agents_factory=lambda wiki: agents)
    client = TestClient(app, cookies=SPANISH)
    page = client.get(f"/w/{WIKI_ID}/pages/overview").text
    assert re.search(r'<input type="checkbox" name="verify" id="verify-box" value="1" disabled>', page)
    # a posted `verify` is ignored: no verifier, no event
    events = stream(client, ask_verified(client))
    assert [name for name, _ in events] == ["chunk", "done"]


def test_the_page_names_the_reason_in_each_tooltip(client):
    html = client.get(f"/w/{WIKI_ID}/pages/overview").text
    assert "python3 quickstart.py --with-verifier" in html
    assert "Sin verificación" in html and "no comprueba la respuesta" in html


# ── Rendering ────────────────────────────────────────────────────────────────

def test_a_sentence_in_a_list_item_is_marked_inside_the_item():
    text = "- Primera oración válida.\n- Segunda oración inventada."
    start = text.index("Segunda")
    html = render_verified("w", text, [(start, len(text))], set())
    assert "<li>Primera oración válida.</li>" in html
    assert f'<li><mark class="unsupported" title="{UNSUPPORTED_TIP}">Segunda oración inventada.</mark></li>' in html


def test_a_table_row_is_marked_cell_by_cell_so_the_table_survives():
    text = "| banco | tasa |\n|---|---|\n| Galicia | 32,5 % |"
    start = text.index("| Galicia")
    html = render_verified("w", text, [(start, len(text))], set())
    assert html.count("<mark") == 2 and "<table>" in html
    assert "<td><mark" in html and "</mark></td>" in html


def test_no_marker_character_is_left_in_the_output():
    text = "Una **oración con énfasis.** Otra oración `con código`."
    html = render_verified("w", text, [(0, 26), (27, len(text))], set())
    assert "" not in html and "" not in html and html.count("<mark") == 2



# ── The draft of a saved conversation ───────────────────────────────────────

DRAFT_TIP = UNSUPPORTED_TIP


def verify_draft(client, markdown, conversation_id="c1"):
    return client.post(f"/w/{WIKI_ID}/chat/verify-draft", data={
        "conversation_id": conversation_id, "markdown": markdown, "page": "concepts/slipper-story"})


def test_every_answer_keeps_the_passages_the_model_read(client, app):
    stream(client, ask_verified(client, verify=""))
    messages = app.state.web.conversations[(WIKI_ID, "c1")]
    assert messages[-1].role == "assistant"
    assert any("glass slipper" in passage.text for passage in messages[-1].context)


def test_the_draft_is_verified_against_the_passages_of_the_conversation(client, verifier, wiki):
    stream(client, ask_verified(client, verify=""))
    before = sorted(p.read_bytes() for p in wiki.path.rglob("*.md"))
    html = verify_draft(client, f"# Slipper story\n\n{SUPPORTED} {INVENTED}\n").text
    assert f'<mark class="unsupported" title="{DRAFT_TIP}">{INVENTED}</mark>' in html
    assert "1 de 2 oraciones sin respaldo" in html
    passages = " ".join(passage for call in verifier.calls for passage, _ in call)
    assert "glass slipper" in passages
    assert sorted(p.read_bytes() for p in wiki.path.rglob("*.md")) == before      # nothing is written


def test_a_conversation_without_passages_says_so_and_verifies_nothing(client, verifier):
    html = verify_draft(client, f"# Slipper story\n\n{INVENTED}\n", conversation_id="nobody").text
    assert "La conversación no conservó ningún pasaje" in html and "<mark" not in html
    assert verifier.calls == []


@pytest.mark.usefixtures("drafts")
def test_without_a_local_verifier_the_draft_button_is_disabled(settings, agents, monkeypatch):
    monkeypatch.setattr(nli, "is_available", lambda: False)
    plain = TestClient(create_app(settings, agents_factory=lambda wiki: agents), cookies=SPANISH)
    stream(plain, ask_verified(plain, verify=""))
    html = plain.post(f"/w/{WIKI_ID}/chat/draft", data={
        "conversation_id": "c1", "title": "Slipper story", "open_page": ""}).text
    assert re.search(r'data-url="/w/tales/chat/verify-draft"[^>]*disabled', html, re.S)
    assert "python3 quickstart.py --with-verifier" in html


@pytest.mark.usefixtures("drafts")
def test_the_review_dialog_offers_verify_the_draft(client):
    stream(client, ask_verified(client, verify=""))
    html = client.post(f"/w/{WIKI_ID}/chat/draft", data={
        "conversation_id": "c1", "title": "Slipper story", "open_page": ""}).text
    assert "Verificar el borrador" in html
    assert not re.search(r'data-url="/w/tales/chat/verify-draft"[^>]*disabled', html, re.S)


# ── Lint and repair ─────────────────────────────────────────────────────────

def test_the_lint_confirmation_offers_the_checkbox_and_passes_it(client, monkeypatch):
    from services import ingest

    html = client.get(f"/w/{WIKI_ID}/maintain").text
    assert re.search(r'id="confirm-lint".*id="verify-lint"', html, re.S)
    assert 'hx-include="#verify-lint"' in html
    seen = {}
    monkeypatch.setattr(ingest, "lint_and_repair", lambda wiki, client, model, *, progress, verifier=None:
                        seen.setdefault("verifier", verifier) and 0)
    monkeypatch.setattr(type(client.app.state.web), "ingest_llm", lambda self: (None, "m", "u"))
    client.post(f"/w/{WIKI_ID}/lint", data={"verify": "true"})
    for _ in range(50):
        if "verifier" in seen:
            break
        import time
        time.sleep(0.05)
    assert seen.get("verifier") is not None
