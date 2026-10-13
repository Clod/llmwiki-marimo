"""The audit page of a conversation and the row of buttons that opens it."""

from __future__ import annotations

import re

from tests.unit.test_flow_nodes import _code_calls
from tests.web.conftest import WIKI_ID
from tests.web.test_chat import ask, stream

CITED = "Cinderella lost a glass slipper (Referencia: wiki/concepts/cinderella.md)."


def audit(client, conversation_id="c1"):
    return client.get(f"/w/{WIKI_ID}/audit/{conversation_id}")


def buttons(html):
    """The row of buttons: its HTML, or None when the page has none."""
    found = re.search(r'<nav id="audit-buttons"[^>]*>(.*?)</nav>', html, re.S)
    return found.group(0) if found else None


def test_the_meta_line_has_no_audit_links(client, agents):
    agents.agent_pre_retrieval.output = CITED
    final = stream(client, ask(client))[0][1]
    meta = re.search(r'<div id="meta-\w+" hx-swap-oob="innerHTML">(.*?)</div>', final, re.S).group(1)
    assert "/audit/" not in meta and "Criterio" not in meta
    assert meta.startswith("Recuperación previa · ") and "concepts/cinderella" in meta
    thread = client.get(f"/w/{WIKI_ID}/chat/c1/thread").text
    assert thread[:thread.index('<nav id="audit-buttons"')].count("/audit/") == 0   # only the row of buttons


def test_the_row_of_buttons_sits_between_the_thread_and_the_composer_and_starts_hidden(client):
    html = client.get(f"/w/{WIKI_ID}/pages/concepts/cinderella").text
    assert html.index('id="messages"') < html.index('id="audit-buttons"') < html.index('id="composer"')
    row = buttons(html)
    assert " hidden" in row.split(">")[0] and "<a " not in row


def test_the_row_of_buttons_comes_with_each_answer_and_points_to_the_last_turn(client, agents):
    agents.agent_pre_retrieval.output = CITED
    final = stream(client, ask(client))[0][1]
    row = buttons(final)
    assert 'hx-swap-oob="true"' in row and "hidden" not in row.split(">")[0]
    # Short labels (owner, 2026-10-11); the long description stays in the title.
    for anchor, text, title in (("criterio", "Criterio", "recorrido"), ("wiki", "Fuentes (", "fragmentos"),
                                ("modelo", "Respuesta", "reemplazó")):
        link = re.search(rf'<a [^>]*href="/w/tales/audit/c1#t1-{anchor}"[^>]*>(.*?)</a>', row)
        assert link and link.group(1).startswith(text)
        assert title in re.search(r'title="([^"]*)"', link.group(0)).group(1)
        assert 'target="_blank"' in link.group(0) and 'rel="noopener"' in link.group(0)
    assert row.index("#t1-criterio") < row.index("#t1-wiki") < row.index("#t1-modelo")
    final = stream(client, ask(client, "And the prince?"))[0][1]
    assert "/audit/c1#t2-criterio" in buttons(final) and "#t1-" not in buttons(final)


def test_the_row_counts_the_passages_of_the_last_answer(client, agents):
    final = stream(client, ask(client, "Will it rain tomorrow in Rosario?"))[0][1]
    assert "Fuentes (0)" in buttons(final)
    agents.agent_pre_retrieval.output = CITED
    final = stream(client, ask(client))[0][1]
    assert "Fuentes (0)" not in buttons(final)


def test_the_thread_of_a_loaded_page_brings_the_row_and_clear_hides_it(client, agents):
    assert " hidden" in buttons(client.get(f"/w/{WIKI_ID}/chat/c1/thread").text).split(">")[0]
    stream(client, ask(client))
    assert "/audit/c1#t1-criterio" in buttons(client.get(f"/w/{WIKI_ID}/chat/c1/thread").text)
    cleared = client.post(f"/w/{WIKI_ID}/chat/clear", data={"conversation_id": "c1"}).text
    row = buttons(cleared)
    assert 'hx-swap-oob="true"' in row and " hidden" in row.split(">")[0] and "<a " not in row


def two_turns(client, agents):
    agents.agent_pre_retrieval.output = CITED
    stream(client, ask(client))
    stream(client, ask(client, "And the prince?"))
    return audit(client).text


def test_the_page_has_the_header_the_index_and_the_sections_of_each_turn(client, agents):
    html = two_turns(client, agents)
    assert "Auditoría de la conversación" in html and "tales" in html and "Recuperación previa" in html
    for anchor in ("resumen", "t1", "t1-criterio", "t1-wiki", "t1-modelo", "t2", "t2-criterio", "t2-wiki", "t2-modelo"):
        assert f'id="{anchor}"' in html


def test_the_index_comes_first_summary_then_each_turn_with_its_three_links(client, agents):
    html = two_turns(client, agents)
    body = html[html.index("<main"):]
    assert body.index('class="audit-index"') < body.index('id="resumen"') < body.index('id="t1"')
    assert body.index('class="audit-index"') < body.index("What happened to Cinderella")
    nav = re.search(r'<nav class="audit-index"[^>]*>(.*?)</nav>', html, re.S).group(1)
    assert "<ul" in nav and nav.index("#resumen") < nav.index("#t1-criterio") < nav.index("#t2-criterio")
    assert "Resumen de la conversación" in nav and "Turno 1 · What happened to Cinderella" in nav
    assert "Turno 2 · And the prince?" in nav
    for n in (1, 2):
        assert nav.index(f'href="#t{n}-criterio"') < nav.index(f'href="#t{n}-wiki"') < nav.index(f'href="#t{n}-modelo"')
    assert re.search(r'href="#t1-wiki"[^>]*>Lo que encontró la wiki \(\d+\)', nav)


def test_the_summary_lists_every_question_and_answer_linked_to_its_turn(client, agents):
    html = two_turns(client, agents)
    summary = re.search(r'id="resumen".*?(?=id="t1")', html, re.S).group(0)
    assert summary.index("What happened to Cinderella") < summary.index("And the prince?")
    assert 'href="#t1-criterio"' in summary and 'href="#t2-criterio"' in summary
    assert "Cinderella lost a glass slipper" in summary


def test_each_turn_has_its_question_and_its_own_fragment_ids(client, agents):
    html = two_turns(client, agents)
    t1 = re.search(r'id="t1"(.*?)(?=id="t2")', html, re.S).group(1)
    t2 = html[html.index('id="t2"'):]
    assert "What happened to Cinderella" in t1 and "And the prince?" in t2
    assert 'id="t1-frag-1"' in t1 and 'href="#t1-frag-1"' in t1
    assert 'id="t2-frag-1"' in t2 and 'id="t1-frag-1"' not in t2
    assert 'id="frag-1"' not in html
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids))


def test_the_old_route_redirects_to_the_turn(client, agents):
    stream(client, ask(client))
    response = client.get(f"/w/{WIKI_ID}/audit/c1/1", follow_redirects=False)
    assert response.status_code in (303, 307)
    assert response.headers["location"] == f"/w/{WIKI_ID}/audit/c1#t1-criterio"
    assert client.get(f"/w/{WIKI_ID}/audit/c1/2", follow_redirects=False).status_code == 404
    assert client.get(f"/w/{WIKI_ID}/audit/c1/0", follow_redirects=False).status_code == 404


def test_criterio_lists_the_path_and_each_node_with_its_text(client, agents):
    agents.agent_pre_retrieval.output = CITED
    stream(client, ask(client))
    html = audit(client).text
    assert "C1 → C2 → Q1" in html
    assert "Q5" in html and "¿La pregunta nombra un tema de la wiki (padrón de cobertura)?" in html
    assert "Cinderella" in html     # the matched term, in the definition list of Q5


def test_criterio_of_q5_shows_the_roster_when_nothing_matched(client, agents):
    stream(client, ask(client, "Will it rain tomorrow in Rosario?"))
    html = audit(client).text
    section = re.search(r'id="t1-criterio".*?(?=id="t1-wiki")', html, re.S).group(0)
    assert "Prince" in section and "Cinderella" in section      # the titles of the concept pages
    assert "rain tomorrow in rosario" in section.lower()        # the stemmed question starts from it


def test_wiki_section_lists_the_passages_and_their_full_text(client, agents):
    agents.agent_pre_retrieval.output = CITED
    stream(client, ask(client))
    html = audit(client).text
    section = re.search(r'id="t1-wiki".*?(?=id="t1-modelo")', html, re.S).group(0)
    assert 'href="#t1-frag-1"' in section and 'id="t1-frag-1"' in section
    assert 'href="/w/tales/pages/concepts/cinderella"' in section
    assert "<pre" in section and "royal ball" in section


def test_wiki_section_says_when_nothing_was_retrieved(client, agents):
    stream(client, ask(client, "Will it rain tomorrow in Rosario?"))
    assert "No se recuperó ningún fragmento: el rechazo se decidió antes de consultar al modelo." in audit(client).text


def test_modelo_section_says_the_code_did_not_change_the_answer(client, agents):
    agents.agent_pre_retrieval.output = CITED
    stream(client, ask(client))
    assert "El código no modificó la respuesta del modelo." in audit(client).text


def test_modelo_section_shows_the_replaced_text_with_its_warning(client, agents):
    agents.agent.output = "The deposit lasts 90 days."
    agents.agent.grounded = False
    stream(client, ask(client, "capital of France?", mode="strict"))
    html = audit(client).text
    assert "Este texto no está verificado: el código lo reemplazó porque no pudo respaldarlo con la wiki." in html
    assert "The deposit lasts 90 days." in html
    assert "El código no modificó la respuesta del modelo." not in html


class StreamingAgent:
    """Streams its output as the events of `run_stream_events`, as the streaming mode reads them."""

    def __init__(self, output):
        self.output = output

    def run_stream_events(self, prompt, deps, message_history):
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        from pydantic_ai import AgentRunResultEvent
        from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta

        @asynccontextmanager
        async def events():
            async def gen():
                yield PartStartEvent(index=0, part=TextPart(self.output[:3]))
                yield PartDeltaEvent(index=0, delta=TextPartDelta(self.output[3:]))
                yield AgentRunResultEvent(SimpleNamespace(output=self.output, all_messages=lambda: []))
            yield gen()

        return events()


def test_the_streaming_answer_has_a_page_too(client, agents):
    object.__setattr__(agents, "agent", StreamingAgent("A plain answer."))   # ChatAgents is frozen
    stream(client, ask(client, mode="streaming"))
    html = audit(client).text
    assert "F1 → F2" in html and "A plain answer." in html


def test_a_node_without_a_path_says_so(client, agents, app):
    from web.state import Message

    conversation = app.state.web.conversation(WIKI_ID, "x")
    conversation.extend([Message("user", "q"), Message("assistant", "a", "strict")])
    assert "No hay recorrido registrado para esta respuesta." in audit(client, conversation_id="x").text


def test_an_absent_conversation_is_a_404(client, agents, app):
    assert audit(client).status_code == 404                 # no conversation
    assert client.get("/w/nowhere/audit/c1").status_code == 404
    from web.state import Message
    app.state.web.conversation(WIKI_ID, "q").append(Message("user", "no answer yet"))
    assert audit(client, "q").status_code == 404            # a question without answer


def test_the_page_is_in_english_with_the_english_interface(en_client, agents):
    stream(en_client, ask(en_client, "Will it rain tomorrow in Rosario?"))
    html = en_client.get(f"/w/{WIKI_ID}/audit/c1").text
    assert "Audit of the conversation" in html and "Does the question name a topic of the wiki (coverage roster)?" in html
    assert "Turn 1 · Will it rain" in html and "Summary of the conversation" in html


def test_the_criterion_of_each_turn_shows_its_tokens(client, agents):
    agents.agent_pre_retrieval.output = CITED
    agents.agent_pre_retrieval.tokens = (3877, 40)
    stream(client, ask(client))
    agents.agent_pre_retrieval.tokens = (500, 12)
    stream(client, ask(client, "And the prince?"))
    html = audit(client).text
    assert "entrada 3.877 · salida 40 · total 3.917" in html
    assert "entrada 500 · salida 12 · total 512" in html


def test_every_recorded_reading_node_has_a_short_text():
    """Every reading node the code records has a short text on the audit page."""
    from web.audit_nodes import NODE_TEXTS

    recorded = {i for i, d, _ in _code_calls() if d == "reading"}
    assert not sorted(recorded - set(NODE_TEXTS)), "nodes without a short text"


# The diagrams of docs/query_nodes.md beside the turns (owner, 2026-10-12).

def diagrams(html):
    """The Mermaid texts of the right column, in order."""
    return re.findall(r'<pre class="mermaid">(.*?)</pre>', html, re.S)


def visited(text):
    """The Mermaid ids the `class ... visited` line of a diagram names."""
    found = re.search(r"^\s*class ([\w,]+) visited$", text, re.M)
    return set(found.group(1).split(",")) if found else set()


def test_the_page_shows_the_overview_and_the_diagram_of_the_mode_of_the_turn(client, agents):
    agents.agent_pre_retrieval.output = CITED
    stream(client, ask(client))
    html = audit(client).text
    shown = diagrams(html)
    assert len(shown) == 2
    assert "C2 · the mode of the turn" in shown[0] and "Q13 · Tier 1" in shown[1]
    assert '/static/js/mermaid.min.js' in html and "cdn" not in html.split("<body")[1].lower()


def test_the_nodes_of_the_path_are_marked_visited_and_only_ids_of_the_block(client, agents):
    agents.agent_pre_retrieval.output = CITED
    stream(client, ask(client))
    overview, detail = diagrams(audit(client).text)
    assert "classDef visited" in overview and "classDef visited" in detail
    assert visited(overview) == {"C1", "C2", "P"}               # C1 → C2 → Q1; Q1 is the id P in the overview
    assert {"Q", "N", "BL", "ST"} <= visited(detail) and "C1" not in visited(detail)


def test_the_strict_mode_selects_the_block_of_section_3(client, agents):
    agents.agent.output = "The deposit lasts 90 days."
    agents.agent.grounded = False
    stream(client, ask(client, "capital of France?", mode="strict"))
    shown = diagrams(audit(client).text)
    assert len(shown) == 2 and "T3 · the answer is replaced" in shown[1] and "Q13" not in shown[1]
    assert {"T1", "T2", "T3"} <= visited(shown[1])


def test_a_streaming_turn_shows_only_the_overview(client, agents):
    object.__setattr__(agents, "agent", StreamingAgent("A plain answer."))
    stream(client, ask(client, mode="streaming"))
    shown = diagrams(audit(client).text)
    assert len(shown) == 1 and "C2 · the mode of the turn" in shown[0]
    assert visited(shown[0]) >= {"C1", "C2", "F"}


def test_selecting_another_turn_returns_its_diagrams(client, agents):
    agents.agent_pre_retrieval.output = CITED
    agents.agent.output = "The deposit lasts 90 days."
    agents.agent.grounded = False
    stream(client, ask(client))
    stream(client, ask(client, "capital of France?", mode="strict"))
    first = client.get(f"/w/{WIKI_ID}/audit/c1/diagram/1").text
    second = client.get(f"/w/{WIKI_ID}/audit/c1/diagram/2").text
    assert "Q13 · Tier 1" in first and "T3 · the answer is replaced" not in first
    assert "T3 · the answer is replaced" in second and "Q13" not in second
    assert client.get(f"/w/{WIKI_ID}/audit/c1/diagram/3").status_code == 404
    page = audit(client).text
    assert 'hx-get="/w/tales/audit/c1/diagram/1"' in page and 'hx-get="/w/tales/audit/c1/diagram/2"' in page


def test_a_change_in_the_markdown_source_changes_the_page(client, agents, tmp_path, monkeypatch):
    from web import audit_diagram

    source = tmp_path / "query_nodes.md"
    source.write_text(
        "```mermaid\nflowchart TD\n    C1[\"C1 · edited overview\"]\n```\n"
        "```mermaid\nflowchart TD\n    Q[\"Q1 · edited pre-retrieval\"]\n```\n"
        "```mermaid\nflowchart TD\n    T[\"T1 · edited strict\"]\n```\n", encoding="utf-8")
    monkeypatch.setattr(audit_diagram, "SOURCE", source)
    agents.agent_pre_retrieval.output = CITED
    stream(client, ask(client))
    overview, detail = diagrams(audit(client).text)
    assert "edited overview" in overview and "edited pre-retrieval" in detail
    assert visited(overview) == {"C1"} and visited(detail) == {"Q"}   # the ids missing from a block are skipped


def test_a_missing_source_leaves_the_page_without_diagrams(client, agents, tmp_path, monkeypatch):
    from web import audit_diagram

    monkeypatch.setattr(audit_diagram, "SOURCE", tmp_path / "absent.md")
    stream(client, ask(client))
    response = audit(client)
    assert response.status_code == 200 and diagrams(response.text) == []
