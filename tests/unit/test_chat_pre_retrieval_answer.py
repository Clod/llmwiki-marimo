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


def _answer(question, run_agent, *, wiki=(), docs=(), vocab=frozenset(), monkeypatch):
    monkeypatch.setattr(preretrieval, "build_vocabulary", lambda src: set(vocab))
    monkeypatch.setattr(preretrieval, "retrieve_wiki", lambda db, q, **k: list(wiki))
    monkeypatch.setattr(preretrieval, "retrieve_source_chunks", lambda db, q, **k: list(docs))
    monkeypatch.setattr(preretrieval, "LocalMarkdownSource", lambda p: object())
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
    agent = _fake_agent("Una caución es... Referencia: cauciones-bursatiles.md")
    out = _answer("¿qué es una caución?", agent,
                  wiki=["[cauciones-bursatiles.md]\nUna caución es una operación..."],
                  monkeypatch=monkeypatch)
    assert "Una caución es" in out
    assert "cauciones-bursatiles.md" in agent.calls[0]  # context was injected


def test_tier2_unsupported_answer_is_refused(monkeypatch):
    # Raw-doc context is about bonds; the answer is off-source -> refuse.
    agent = _fake_agent("Los CEDEARs permiten invertir en Apple, Google y Amazon.")
    out = _answer("contame de algo", agent,
                  wiki=[], docs=["[05 Bonos.docx]\nExposición al dólar oficial mayorista."],
                  monkeypatch=monkeypatch)
    assert out == REFUSAL_ES


def test_tier2_supported_answer_gets_warning(monkeypatch):
    src = "[05 Bonos.docx]\nLos bonos dólar linked ajustan por el tipo de cambio oficial mayorista."
    agent = _fake_agent("Los bonos dólar linked ajustan por el tipo de cambio oficial.")
    out = _answer("¿qué son los bonos dólar linked?", agent, wiki=[], docs=[src],
                  monkeypatch=monkeypatch)
    assert "documento fuente" in out.lower()  # the Tier-2 warning


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
