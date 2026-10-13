"""Query condensation (domain/chat/condense.py and node Q5b of pre_retrieval_answer):
a follow-up that names no topic is rewritten from the conversation and the coverage
gate runs again on the rewrite. No model: the rewrite and the answer are injected."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from domain.chat import condense, preretrieval
from domain.chat.preretrieval import pre_retrieval_answer

CFG = SimpleNamespace(off_limits=["cedear", "cedears"], data_aliases={})
CONCEPTS = ["Caución bursátil"]
WIKI_HIT = "[/wiki/concepts/caucion-bursatil.md]\nLa caución tiene riesgo de crédito casi nulo."
HISTORY = [
    ModelRequest(parts=[UserPromptPart(content="¿Qué es una caución bursátil?")]),
    ModelResponse(parts=[TextPart(content="Una operación de corto plazo en el mercado de capitales.")]),
]


class _Result:
    def __init__(self, output):
        self.output = output

    def all_messages(self):
        return []


class _NoDatasets:
    def categories(self):
        return []


def _turn(question, monkeypatch, *, rewrite=None, history=HISTORY):
    """Run one pre-retrieval turn; return (answer, prompts the model answered, rewrites asked,
    questions the search saw)."""
    monkeypatch.setattr(preretrieval, "build_vocabulary", lambda src: set())
    searched = []
    monkeypatch.setattr(preretrieval, "retrieve_wiki", lambda db, q, **k: searched.append(q) or [WIKI_HIT])
    monkeypatch.setattr(preretrieval, "retrieve_source_chunks", lambda db, q, **k: [])
    monkeypatch.setattr(preretrieval, "LocalMarkdownSource", lambda p: _NoDatasets())
    monkeypatch.setattr(preretrieval, "concept_page_names", lambda db: list(CONCEPTS))
    monkeypatch.setattr(preretrieval, "retrieve_collection_pages", lambda ws: [])
    monkeypatch.setattr(preretrieval, "roster_suggestions", lambda *a, **k: [])
    monkeypatch.setattr(preretrieval, "search_suggestions", lambda *a, **k: [])
    answered, asked = [], []

    async def run_agent(prompt, prior):
        answered.append(prompt)
        return _Result("Tiene riesgo de crédito casi nulo. Referencia: wiki/concepts/caucion-bursatil.md")

    async def condense_call(q, h):
        asked.append(q)
        return rewrite

    answer = asyncio.run(pre_retrieval_answer(
        question, config=CFG, db_path="db", workspace=Path("/tmp/wp"), history=history,
        language="es", run_agent=run_agent, condense=condense_call if rewrite is not None else None,
    ))
    return answer, answered, asked, searched


def test_an_elliptic_follow_up_is_rewritten_gated_and_searched_with_the_rewrite(monkeypatch):
    answer, answered, asked, searched = _turn(
        "¿Y qué riesgos tiene?", monkeypatch, rewrite="¿Qué riesgos tiene la caución bursátil?")
    assert asked == ["¿Y qué riesgos tiene?"]
    assert searched == ["¿Qué riesgos tiene la caución bursátil?"]
    assert "riesgo de crédito" in answer
    assert "¿Y qué riesgos tiene?" in answered[0]                 # the model answers the original question
    assert "¿Qué riesgos tiene la caución bursátil?" not in answered[0]


def test_without_condensation_the_same_follow_up_is_refused_by_the_code(monkeypatch):
    answer, answered, asked, _ = _turn("¿Y qué riesgos tiene?", monkeypatch, rewrite=None)
    assert answer.startswith("No recibí de la wiki información relacionada con esta pregunta.")
    assert answered == [] and asked == []


def test_a_question_about_another_subject_is_rewritten_unchanged_and_still_refused(monkeypatch):
    answer, answered, asked, _ = _turn(
        "¿Cuál es la capital de Francia?", monkeypatch, rewrite="¿Cuál es la capital de Francia?")
    assert asked == ["¿Cuál es la capital de Francia?"]
    assert answer.startswith("No recibí de la wiki información relacionada con esta pregunta.")
    assert answered == []


def test_a_question_that_names_its_topic_is_not_rewritten(monkeypatch):
    _, _, asked, searched = _turn("¿Qué es una caución bursátil?", monkeypatch, rewrite="otra cosa")
    assert asked == [] and searched == ["¿Qué es una caución bursátil?"]


def test_the_first_question_of_a_conversation_is_not_rewritten(monkeypatch):
    answer, _, asked, _ = _turn("¿Y qué riesgos tiene?", monkeypatch, rewrite="x", history=[])
    assert asked == [] and answer.startswith("No recibí de la wiki información relacionada")


def test_a_rewrite_that_brings_a_blacklisted_subject_is_refused_by_the_blacklist(monkeypatch):
    answer, answered, _, _ = _turn(
        "¿Y conviene comprarlos?", monkeypatch, rewrite="¿Conviene comprar cedears?")
    assert answer.startswith("No puedo responder esta pregunta: menciona «cedears»")
    assert answered == []


# ── The pure helpers ─────────────────────────────────────────────────────────

def test_the_prompt_holds_the_last_exchanges_and_the_question():
    text = condense.prompt("¿Y qué riesgos tiene?", HISTORY, "es")
    assert text.splitlines()[0] == "Usuario: ¿Qué es una caución bursátil?"
    assert "Asistente: Una operación de corto plazo" in text
    assert text.endswith("Última pregunta: ¿Y qué riesgos tiene?")


def test_the_prompt_keeps_at_most_three_exchanges_and_truncates_long_answers():
    long = [m for i in range(5) for m in (
        ModelRequest(parts=[UserPromptPart(content=f"pregunta {i}")]),
        ModelResponse(parts=[TextPart(content="x" * 2000)]))]
    text = condense.prompt("¿Y?", long, "es")
    assert "pregunta 1" not in text and "pregunta 2" in text and "pregunta 4" in text
    assert "x" * (condense.MAX_ANSWER_CHARS + 1) not in text


def test_an_empty_long_or_multiline_rewrite_is_replaced_by_the_question():
    q = "¿Y qué riesgos tiene?"
    assert condense.accepted("", q) == q
    assert condense.accepted(None, q) == q
    assert condense.accepted("a" * (condense.MAX_REWRITE_CHARS + 1), q) == q
    assert condense.accepted("Línea uno\nLínea dos", q) == q
    assert condense.accepted('"¿Qué riesgos tiene la caución?"', q) == "¿Qué riesgos tiene la caución?"


def test_the_switch_of_wiki_config_turns_condensation_off(tmp_path):
    from domain.chat.config import load_config

    assert load_config(tmp_path).condense_question is True
    (tmp_path / "wiki_config.toml").write_text("[pre_retrieval]\ncondense_question = false\n", encoding="utf-8")
    assert load_config(tmp_path).condense_question is False
