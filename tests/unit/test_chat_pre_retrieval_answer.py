"""Tests for domain/chat/preretrieval.py::pre_retrieval_answer — the async
orchestration of the hybrid flow, exercised with a fake agent (no LLM, no DB).

Branches covered: off-limits refuse, Tier-1 (curated) answer, Tier-2 verify
fail/pass (+ warning), data question (guardrail applies), and nothing-found
refuse. Retrieval + vocabulary are monkeypatched; the agent run is injected.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from domain.chat import preretrieval
from domain.chat.guardrail import REFUSAL_ES
from domain.chat.preretrieval import pre_retrieval_answer

CFG = SimpleNamespace(
    off_limits=["cedear", "cedears"],
    data_aliases={"dolar": ["billete verde"]},
)


class _FakeResult:
    def __init__(self, output, messages=None):
        self.output = output
        self._messages = messages or []

    def all_messages(self):
        return self._messages


def _fake_agent(output, messages=None):
    calls = []

    async def run_agent(q, history):
        calls.append(q)
        return _FakeResult(output, messages)

    run_agent.calls = calls
    return run_agent


def _run(coro):
    return asyncio.run(coro)


def _answer(question, run_agent, *, wiki=(), docs=(), vocab=frozenset(),
            concepts=(), monkeypatch):
    monkeypatch.setattr(preretrieval, "build_vocabulary", lambda src: set(vocab))
    monkeypatch.setattr(preretrieval, "retrieve_wiki", lambda db, q, **k: list(wiki))
    monkeypatch.setattr(preretrieval, "retrieve_source_chunks", lambda db, q, **k: list(docs))
    monkeypatch.setattr(preretrieval, "LocalMarkdownSource", lambda p: object())
    monkeypatch.setattr(preretrieval, "concept_page_names", lambda db: list(concepts))
    return _run(pre_retrieval_answer(
        question, config=CFG, db_path="db", workspace=Path("/tmp/wp"),
        history=[], language="es", run_agent=run_agent,
    ))


def test_off_limits_refuses_without_calling_agent(monkeypatch):
    agent = _fake_agent("no debería llamarse")
    out = _answer("¿qué son los cedears?", agent, wiki=["algo"], monkeypatch=monkeypatch)
    assert out == REFUSAL_ES
    assert agent.calls == []  # model never invoked


def test_tier1_curated_injects_context_and_returns_answer(monkeypatch):
    # Covered topic (in the roster) with a curated hit -> Tier 1 injects + answers.
    agent = _fake_agent("Una caución es... Referencia: cauciones-bursatiles.md")
    out = _answer("¿qué es una caución?", agent, vocab={"caución"},
                  wiki=["[cauciones-bursatiles.md]\nUna caución es una operación..."],
                  monkeypatch=monkeypatch)
    assert "Una caución es" in out
    assert "cauciones-bursatiles.md" in agent.calls[0]  # context was injected


def test_tier1_curated_hit_not_in_roster_refuses(monkeypatch):
    # A curated hit on an UNcovered topic (empty roster) must NOT inject — the
    # padrón is the authority, not the lexical FTS hit.
    agent = _fake_agent("no debería contestar")
    out = _answer("¿cuál es la capital de Francia?", agent,
                  wiki=["[panel-lider.md]\nEl panel líder concentra el capital..."],
                  monkeypatch=monkeypatch)
    assert out == REFUSAL_ES
    assert agent.calls == []  # model never invoked


def test_tier2_unsupported_answer_is_refused(monkeypatch):
    # Covered CONCEPT (not a data term, no curated page) so Tier 2 fires; the
    # answer is off-source -> refuse. A data-term topic would go to tools instead.
    agent = _fake_agent("Los CEDEARs permiten invertir en Apple, Google y Amazon.")
    out = _answer("¿qué es una caución bursátil?", agent, concepts={"caución bursátil"},
                  wiki=[], docs=["[12 Cauciones.docx]\nLa caución bursátil es un préstamo garantizado."],
                  monkeypatch=monkeypatch)
    assert out == REFUSAL_ES


def test_tier2_supported_answer_gets_warning(monkeypatch):
    src = "[12 Cauciones.docx]\nLa caución bursátil es un préstamo de corto plazo garantizado con títulos."
    agent = _fake_agent("La caución bursátil es un préstamo de corto plazo garantizado con títulos.")
    out = _answer("¿qué es una caución bursátil?", agent, concepts={"caución bursátil"},
                  wiki=[], docs=[src], monkeypatch=monkeypatch)
    assert "documento fuente" in out.lower()  # the Tier-2 warning


def test_uncovered_topic_with_docs_refuses_before_model(monkeypatch):
    # Not in the padrón: even though a raw-doc chunk matches, Tier 2 must not fire
    # and the model is never called (the CEDEARs-leak class, stopped at the gate).
    agent = _fake_agent("no debería llamarse")
    out = _answer("¿qué es la fotosíntesis?", agent, vocab={"dolar"},
                  wiki=[], docs=["[bio.pdf]\nla clorofila capta la luz"],
                  monkeypatch=monkeypatch)
    assert out == REFUSAL_ES
    assert agent.calls == []


def test_nothing_found_refuses(monkeypatch):
    agent = _fake_agent("no debería llamarse")
    out = _answer("¿capital de Francia?", agent, wiki=[], docs=[], vocab={"dolar"},
                  monkeypatch=monkeypatch)
    assert out == REFUSAL_ES
    assert agent.calls == []


def test_data_question_invokes_tools_only_no_context(monkeypatch):
    # "billete verde" is a whitelisted alias for dólar -> data question. No wiki/
    # doc hits -> invoke with tools only (no injected context), guardrail applies.
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    grounded = [ModelRequest(parts=[
        ToolReturnPart(tool_name="query_dataset", content="| MEP | 1180 |", tool_call_id="c1")
    ])]
    agent = _fake_agent("El dólar MEP está a 1180.", messages=grounded)
    out = _answer("¿a cuánto está el billete verde?", agent, wiki=[], docs=[],
                  vocab={"dolar"}, monkeypatch=monkeypatch)
    assert "1180" in out
    assert agent.calls[0] == "¿a cuánto está el billete verde?"  # no context prepended


def test_advisory_intent_without_named_data_invokes_tools(monkeypatch):
    # Case G: a generic advisory question names no instrument (vocab miss) but
    # asks "$1M, 3 meses, ¿qué alternativas?" -> advisory_intent routes it to the
    # tools path (invoke, no injected context) instead of refusing.
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    grounded = [ModelRequest(parts=[
        ToolReturnPart(tool_name="estimar_alternativas",
                       content="| Credicoop 90d | $90.000 |", tool_call_id="c1")
    ])]
    agent = _fake_agent("La mejor alternativa rinde $90.000.", messages=grounded)
    out = _answer(
        "Tengo $1.000.000 que no necesito por 3 meses, ¿qué alternativas tengo y cuánto ganaría?",
        agent, wiki=[], docs=[], vocab=frozenset(), monkeypatch=monkeypatch,
    )
    assert "90.000" in out
    assert agent.calls  # the model WAS invoked (not refused at the gate)
