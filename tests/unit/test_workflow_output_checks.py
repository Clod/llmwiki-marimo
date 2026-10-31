"""Tests for domain/chat/workflow/output_checks.py — the output workflow, run on
its own with answers written by hand, with no agent and no database.

Pre-retrieval: each refusal of the answer (R3, R4, R5) and each answer that is
shown (Tier 1, Tier 2 with the warning, a data question with evidence). Strict:
an answer with evidence and an answer without.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from domain import tracing
from domain.chat import preretrieval
from domain.chat.guardrail import REFUSAL_ES
from domain.chat.preretrieval import PreRetrievalTurn, RetrievalPlan
from domain.chat.refusal import fixed_sentence
from domain.chat.workflow import output_checks
from domain.chat.workflow.output_checks import AnswerChecks

CFG = SimpleNamespace(off_limits=[], data_aliases={})
FRAGMENT = "[12 Cauciones.docx]\nLa caución bursátil es un préstamo de corto plazo garantizado con títulos."


@pytest.fixture(autouse=True)
def _no_suggestions(monkeypatch):
    monkeypatch.setattr(preretrieval, "roster_suggestions", lambda *a, **k: [])
    monkeypatch.setattr(preretrieval, "search_suggestions", lambda *a, **k: [])


def _evidence():
    call = ToolCallPart(tool_name="query_dataset", args={"categoria": "dolar"}, tool_call_id="c")
    ret = ToolReturnPart(tool_name="query_dataset", tool_call_id="c",
                         content="| clave | valor |\n|---|---|\n| blue | 1200 |")
    return [ModelResponse(parts=[call]), ModelRequest(parts=[ret])]


def _turn(plan, *, doc_hits=(), traced=None):
    turn = PreRetrievalTurn(
        "¿pregunta?", CFG, "db", Path("/tmp/wp"), [], "es", stem_language="es",
        source=None, on_trace=(lambda **kw: traced.append(kw)) if traced is not None else None,
    )
    turn.plan, turn.doc_hits = plan, list(doc_hits)
    return turn


TIER1 = RetrievalPlan("invoke", "curado", "[/wiki/concepts/x.md]\ntexto", False, ())
TIER2 = RetrievalPlan("invoke", "crudo", FRAGMENT, True, (FRAGMENT,))
DATA = RetrievalPlan("invoke", None, None, False)


def _run(checks):
    """Run the output workflow; return the final answer and the nodes it recorded."""
    async def go():
        with tracing.collect() as path:
            out = await output_checks.graph.run(state=checks, inputs=checks)
        return out, [entry["node"] for entry in path]
    return asyncio.run(go())


def test_graph_has_no_instrumentation():
    assert output_checks.graph.auto_instrument is False


def test_tier1_answer_is_shown():
    traced = []
    out, path = _run(AnswerChecks("Respuesta.", [], "es", turn=_turn(TIER1, traced=traced)))
    assert out == "Respuesta."
    assert path == ["Q20", "Q21"]
    assert traced[0]["refusal_substituted"] is False and traced[0]["final"] == out


def test_tier2_unsupported_ends_in_r3():
    traced = []
    checks = AnswerChecks("Los CEDEARs permiten invertir en Apple.", [], "es",
                          turn=_turn(TIER2, doc_hits=[FRAGMENT], traced=traced))
    out, path = _run(checks)
    assert out.startswith("Encontré el documento «12 Cauciones.docx»")
    assert path == ["Q17", "R3"]
    assert traced[0]["refusal_substituted"] is True


def test_tier2_supported_gets_the_warning():
    checks = AnswerChecks("La caución bursátil es un préstamo de corto plazo garantizado con títulos.",
                          [], "es", turn=_turn(TIER2, doc_hits=[FRAGMENT]))
    out, path = _run(checks)
    assert "documento fuente" in out.lower()
    assert path == ["Q17", "Q20", "Q18", "Q21"]


def test_data_answer_without_evidence_ends_in_r4():
    out, path = _run(AnswerChecks("El dólar está a 1200.", [], "es", turn=_turn(DATA)))
    assert path == ["Q19", "R4"]
    assert out != "El dólar está a 1200."


def test_data_answer_with_evidence_is_shown():
    out, path = _run(AnswerChecks("El dólar blue está a 1200.", _evidence(), "es", turn=_turn(DATA)))
    assert path == ["Q19", "Q20", "Q21"]
    assert out.startswith("El dólar blue está a 1200.")


def test_model_refusal_ends_in_r5():
    raw = f"{fixed_sentence('es')} No hay datos."
    out, path = _run(AnswerChecks(raw, [], "es", turn=_turn(TIER1)))
    assert path == ["Q20", "R5"]
    assert raw in out


def test_strict_answer_with_evidence():
    checks = AnswerChecks("El dólar blue está a 1200.", _evidence(), "es")
    out, path = _run(checks)
    assert path == ["T2", "T4", "T5"]
    assert checks.grounded is True and checks.refusal_substituted is False
    assert out.startswith("El dólar blue está a 1200.")


def test_strict_answer_without_evidence_is_replaced():
    checks = AnswerChecks("Inventado.", [], "es")
    out, path = _run(checks)
    assert path == ["T2", "T3", "T4", "T5"]
    assert out == REFUSAL_ES
    assert checks.grounded is False and checks.refusal_substituted is True


def test_render_names_the_nodes():
    diagram = output_checks.graph.render()
    for node in ("Q17", "Q18", "Q19", "Q20", "Q21", "R3", "R4", "R5", "T2", "T3", "T4", "T5"):
        assert node in diagram


def _returns(tool_name, content):
    call = ToolCallPart(tool_name=tool_name, args={}, tool_call_id="s")
    ret = ToolReturnPart(tool_name=tool_name, tool_call_id="s", content=content)
    return [ModelResponse(parts=[call]), ModelRequest(parts=[ret])]


SEARCH = "[wiki/concepts/cauciones-bursatiles.md] Cauciones\nUna caución es un préstamo."
READ = "[wiki page: wiki/concepts/cauciones-bursatiles.md]\n\n# Cauciones\nUna caución es un préstamo."


def test_strict_answer_from_a_search_only_is_marked_uncited():
    """A search result counts as evidence (T2) but is not cited (buscar ≠ usar):
    the answer is delivered with the note, not refused."""
    checks = AnswerChecks("Una caución es un préstamo.", _returns("search_wiki_fts", SEARCH), "es")
    out, path = _run(checks)
    assert path == ["T2", "T4", "T5"]
    assert out.startswith("Una caución es un préstamo.")
    assert out.endswith("Referencia: ninguna (la respuesta no se apoya en ninguna página "
                        "leída ni en ningún dato consultado)")


def test_strict_answer_from_a_read_page_is_cited_not_marked():
    checks = AnswerChecks("Una caución es un préstamo.", _returns("read_wiki_page", READ), "es")
    out, _ = _run(checks)
    assert out.endswith("Referencia: cauciones-bursatiles.md")
    assert "Referencia: ninguna" not in out


def test_strict_refusal_is_not_marked():
    checks = AnswerChecks("Inventado.", [], "es")
    out, _ = _run(checks)
    assert out == REFUSAL_ES
