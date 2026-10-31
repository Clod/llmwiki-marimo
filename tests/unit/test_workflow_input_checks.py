"""Tests for domain/chat/workflow/input_checks.py — the input workflow of the
pre-retrieval mode, run on its own, with no agent and no database.

Each refusal that ends the workflow (R1 at Q3, R1 after the rewrite of Q5b, R2a,
R2b) has a test, and so has each node that calls the agent (Q13 to Q16). The
retrieval and the vocabulary are patched on the module `preretrieval`, the
module through which the steps call them.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from domain import tracing
from domain.chat import preretrieval
from domain.chat.preretrieval import PreRetrievalTurn
from domain.chat.workflow import input_checks
from domain.chat.workflow.input_checks import AgentCall, Refused

CFG = SimpleNamespace(off_limits=["cedear", "cedears"], data_aliases={"dolar": ["billete verde"]})


class _NoDatasets:
    def categories(self):
        return []


@pytest.fixture
def patched(monkeypatch):
    """Patch the retrieval of `preretrieval`; return a setter of its results."""
    found = dict(vocab=set(), wiki=[], docs=[], concepts=[], collection=[])
    monkeypatch.setattr(preretrieval, "build_vocabulary", lambda src: set(found["vocab"]))
    monkeypatch.setattr(preretrieval, "retrieve_wiki", lambda db, q, **k: list(found["wiki"]))
    monkeypatch.setattr(preretrieval, "retrieve_source_chunks", lambda db, q, **k: list(found["docs"]))
    monkeypatch.setattr(preretrieval, "concept_page_names", lambda db: list(found["concepts"]))
    monkeypatch.setattr(preretrieval, "retrieve_collection_pages", lambda ws: list(found["collection"]))
    monkeypatch.setattr(preretrieval, "LocalMarkdownSource", lambda p: _NoDatasets())
    monkeypatch.setattr(preretrieval, "roster_suggestions", lambda *a, **k: [])
    monkeypatch.setattr(preretrieval, "search_suggestions", lambda *a, **k: [])
    return found


def _turn(question, *, history=(), condense=None):
    return PreRetrievalTurn(
        question, CFG, "db", Path("/tmp/wp"), list(history), "es",
        stem_language="es", source=_NoDatasets(), condense=condense,
    )


def _run(turn):
    """Run the input workflow; return its output and the nodes it recorded."""
    async def go():
        with tracing.collect() as path:
            out = await input_checks.graph.run(state=turn, inputs=turn)
        return out, [entry["node"] for entry in path]
    return asyncio.run(go())


def test_graph_has_no_instrumentation():
    assert input_checks.graph.auto_instrument is False


def test_blacklist_ends_after_q3(patched):
    out, path = _run(_turn("¿qué son los cedears?"))
    assert isinstance(out, Refused)
    assert out.text.startswith("No puedo responder esta pregunta: menciona «cedears»")
    assert path == ["Q1", "Q2", "Q3", "R1"]


def test_blacklisted_rewrite_ends_in_r1(patched):
    async def condense(question, history):
        return "¿Conviene comprar CEDEARs?"

    out, path = _run(_turn("¿y eso conviene?", history=["previous turn"], condense=condense))
    assert isinstance(out, Refused)
    assert "cedears" in out.text.lower()
    assert path == ["Q1", "Q2", "Q3", "Q4", "Q5", "Q5a", "Q5b", "R1"]


def test_not_in_roster_ends_in_r2a(patched):
    out, path = _run(_turn("¿va a llover mañana en Rosario?"))
    assert isinstance(out, Refused)
    assert out.text.startswith("No recibí de la wiki información relacionada")
    assert path == ["Q1", "Q2", "Q3", "Q4", "Q5", "Q5a", "Q9", "Q10", "Q11", "Q12", "R2a"]


def test_in_roster_nothing_found_ends_in_r2b(patched):
    patched["concepts"] = ["caución bursátil"]
    out, path = _run(_turn("¿qué es una caución bursátil?"))
    assert isinstance(out, Refused)
    assert path == ["Q1", "Q2", "Q3", "Q4", "Q5", "Q5a", "Q6", "Q8", "Q7", "Q9", "Q10", "Q11", "Q12", "R2b"]


def test_wiki_pages_go_to_q13(patched):
    patched["concepts"] = ["caución bursátil"]
    patched["wiki"] = ["[/wiki/concepts/caucion.md]\nUna caución es..."]
    turn = _turn("¿qué es una caución bursátil?")
    out, path = _run(turn)
    assert out == AgentCall("Q13")
    assert path == ["Q1", "Q2", "Q3", "Q4", "Q5", "Q5a", "Q6", "Q7", "Q7a"]
    assert turn.plan.tier == "curado"


def test_collection_question_goes_to_q14(patched):
    patched["collection"] = ["[wiki/overview.md]\nThe wiki has..."]
    out, path = _run(_turn("¿qué temas tiene esta wiki?"))
    assert out == AgentCall("Q14")
    assert path[-1] == "Q9"


def test_data_question_goes_to_q15(patched):
    out, path = _run(_turn("¿a cuánto está el billete verde?"))
    assert out == AgentCall("Q15")
    assert path[-2:] == ["Q9", "Q10"]


def test_source_fragments_go_to_q16(patched):
    patched["concepts"] = ["caución bursátil"]
    patched["docs"] = ["[12 Cauciones.docx]\nLa caución bursátil es un préstamo."]
    turn = _turn("¿qué es una caución bursátil?")
    out, path = _run(turn)
    assert out == AgentCall("Q16")
    assert path[-1] == "Q11"
    assert turn.plan.verify is True


def test_collection_intent_is_read_through_preretrieval(patched):
    """The workshop replaces the predicate of Q9 on the module `preretrieval`;
    the input workflow reads it there, so the path changes."""
    patched["collection"] = ["[wiki/overview.md]\nThe wiki has..."]
    question = "¿va a llover mañana en Rosario?"
    out, path = _run(_turn(question))
    assert isinstance(out, Refused) and path[-1] == "R2a"
    with mock.patch.object(preretrieval, "collection_intent", lambda q, cues, language: True):
        out, path = _run(_turn(question))
    assert out == AgentCall("Q14")
    assert path[-1] == "Q9"


def test_render_names_the_nodes():
    diagram = input_checks.graph.render()
    for node in ("Q1", "Q3", "Q5b", "Q6", "Q8", "R1", "R2a", "R2b", "agent"):
        assert node in diagram


def test_workshop_patch_of_collection_intent_changes_the_path(patched):
    """As `workshop/apoyo.py` does: `pre_retrieval_answer` with a stand-in
    `run_agent`, and the predicate of Q9 replaced on the module `preretrieval`."""
    patched["collection"] = ["[wiki/overview.md]\nThe wiki has..."]
    calls = []

    async def run_agent(prompt, history):
        calls.append(prompt)
        return SimpleNamespace(output="Esta wiki trata de finanzas.", all_messages=lambda: [])

    def answer():
        async def go():
            with tracing.collect() as path:
                out = await preretrieval.pre_retrieval_answer(
                    "¿va a llover mañana en Rosario?", config=CFG, db_path="db",
                    workspace=Path("/tmp/wp"), history=[], language="es", run_agent=run_agent,
                )
            return out, [entry["node"] for entry in path]
        return asyncio.run(go())

    _, before = answer()
    with mock.patch.object(preretrieval, "collection_intent", lambda q, cues, language: False):
        _, kept = answer()
    assert before == kept and before[-1] == "R2a" and calls == []
    with mock.patch.object(preretrieval, "collection_intent", lambda q, cues, language: True):
        out, after = answer()
    assert len(calls) == 1
    assert after == ["Q1", "Q2", "Q3", "Q4", "Q5", "Q5a", "Q9", "Q14", "Q20", "Q21"]
    assert out.startswith("Esta wiki trata de finanzas.")
