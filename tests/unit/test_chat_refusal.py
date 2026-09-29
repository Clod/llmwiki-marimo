"""Tests for refusals that say why (design_refusal_messages.md, section 7).

`render` and `is_refusal` are pure. The suggestion functions run against a copy
of the finance demo's index. No LLM.
"""

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from domain.chat import preretrieval
from domain.chat.guardrail import strip_refused_exchanges
from domain.chat.preretrieval import pre_retrieval_answer
from domain.chat.refusal import (
    Refusal,
    RefusalCause,
    fixed_sentence,
    is_refusal,
    order_suggestions,
    render,
    roster_suggestions,
    search_suggestions,
)

DEMO = Path(__file__).resolve().parents[2] / "examples" / "finanzas-argentinas"
PAGES = [("Plazo fijo tradicional", "/wiki/concepts/plazo-fijo-tradicional.md"),
         ("Plazo fijo UVA", "/wiki/concepts/plazo-fijo-uva.md")]


@pytest.fixture
def demo_db(tmp_path):
    workspace = tmp_path / "demo"
    shutil.copytree(DEMO, workspace, ignore=shutil.ignore_patterns("traces", "chat_trace.jsonl"))
    return str(workspace / ".llmwiki" / "index.db"), workspace


# ── 1. render: one message per cause, in both languages ─────────────────────

@pytest.mark.parametrize("language, cause, opening", [
    ("es", RefusalCause.BLACKLIST, "No puedo responder esta pregunta: menciona «cedears»"),
    ("es", RefusalCause.NOT_IN_ROSTER, "No puedo responder esta pregunta: no nombra ninguno"),
    ("es", RefusalCause.NOTHING_FOUND, "La pregunta nombra un tema de esta wiki"),
    ("es", RefusalCause.UNSUPPORTED, "Encontré el documento «12 Cauciones.docx»"),
    ("es", RefusalCause.UNGROUNDED, "No encontré en las páginas ni en los documentos"),
    ("en", RefusalCause.BLACKLIST, 'I cannot answer this question: it mentions "cedears"'),
    ("en", RefusalCause.NOT_IN_ROSTER, "I cannot answer this question: it names none"),
    ("en", RefusalCause.NOTHING_FOUND, "The question names a topic of this wiki"),
    ("en", RefusalCause.UNSUPPORTED, 'I found the document "12 Cauciones.docx"'),
    ("en", RefusalCause.UNGROUNDED, "I found no information in this wiki's pages"),
])
def test_each_cause_opens_with_its_own_sentence(language, cause, opening):
    text = render(Refusal(cause, term="cedears", document="12 Cauciones.docx"), language)
    assert text.startswith(opening)
    assert is_refusal(text)


def test_blacklist_refusal_carries_no_suggestions():
    text = render(Refusal(RefusalCause.BLACKLIST, term="cedears", suggestions=PAGES), "es")
    assert "Plazo fijo" not in text and "índice" not in text


def test_suggestions_are_titles_without_links():
    text = render(Refusal(RefusalCause.NOTHING_FOUND, suggestions=PAGES), "es")
    assert "- Plazo fijo tradicional\n- Plazo fijo UVA" in text
    assert ".md" not in text and "](" not in text


def test_not_in_roster_asks_to_reword_with_a_title():
    text = render(Refusal(RefusalCause.NOT_IN_ROSTER, suggestions=PAGES), "es")
    assert "reformulá la pregunta usando su título:" in text


def test_no_page_points_to_the_wiki_index():
    assert render(Refusal(RefusalCause.NOT_IN_ROSTER), "es").endswith(
        "Podés ver todos los temas en el índice de la wiki, en la pestaña de lectura.")
    assert render(Refusal(RefusalCause.UNGROUNDED), "en").endswith(
        "You can see every topic in the wiki's index, in the Read tab.")


def test_model_refusal_keeps_the_model_text_and_adds_the_cause():
    model = f"{fixed_sentence('es')} Busqué en las páginas del plazo fijo."
    text = render(Refusal(RefusalCause.MODEL, model_text=model, suggestions=PAGES[:1]), "es")
    assert text.startswith(model)
    assert "El modelo no encontró la respuesta" in text and "- Plazo fijo tradicional" in text


# ── 2. Suggestions ───────────────────────────────────────────────────────────

def test_order_dedupes_groups_and_caps_at_five():
    pages = [
        ("10 Plazos Fijos", "/wiki/summaries/10-plazos-fijos.md"),
        ("Riesgo de liquidez", "/wiki/concepts/riesgo-de-liquidez.md"),
        ("Plazo fijo UVA", "/wiki/concepts/plazo-fijo-uva.md"),
        ("Plazo fijo UVA", "/wiki/concepts/plazo-fijo-uva.md"),
    ] + [(f"Otro {i}", f"/wiki/concepts/otro-{i}.md") for i in range(5)]
    out = order_suggestions(pages, "¿Cuánto rinden los plazos fijos?", "es")
    assert out[0] == ("Plazo fijo UVA", "/wiki/concepts/plazo-fijo-uva.md")   # title shares a stem
    assert out[1][0] == "Riesgo de liquidez"                                  # other concept pages
    assert len(out) == 5 and all("/summaries/" not in p for _, p in out)     # summary pushed out


def test_roster_suggestions_come_from_the_named_category(demo_db):
    db_path, _ = demo_db
    out = roster_suggestions(db_path, "¿Cuánto rinden los plazos fijos?", {"plazo_fijo"}, "es")
    titles = [t for t, _ in out]
    assert titles and all("plazo fijo" in t.lower() for t in titles)


def test_search_suggestions_are_wiki_pages(demo_db):
    db_path, _ = demo_db
    out = search_suggestions(db_path, "¿las cauciones son riesgosas?", "es")
    assert out and all(p.startswith("/wiki/") for _, p in out)


def test_question_with_no_shared_word_gets_no_suggestions(demo_db):
    db_path, _ = demo_db
    assert search_suggestions(db_path, "¿Va a llover mañana en Rosario?", "es") == []


# ── 3. The history filter recognises every cause ────────────────────────────

def _msg(role, content):
    return SimpleNamespace(role=role, content=content)


@pytest.mark.parametrize("cause", [c for c in RefusalCause if c is not RefusalCause.MODEL])
def test_strip_removes_a_refusal_of_every_cause_with_its_question(cause):
    refusal = render(Refusal(cause, term="cedears", document="x.pdf", suggestions=PAGES), "es")
    history = [_msg("user", "q1"), _msg("assistant", refusal), _msg("user", "q2")]
    assert [m.content for m in strip_refused_exchanges(history)] == ["q2"]


def test_strip_removes_the_model_refusal_and_keeps_real_answers():
    model = render(Refusal(RefusalCause.MODEL, model_text=fixed_sentence("es")), "es")
    history = [_msg("user", "q1"), _msg("assistant", model),
               _msg("user", "q2"), _msg("assistant", "Una caución es un préstamo.")]
    assert [m.content for m in strip_refused_exchanges(history)] == [
        "q2", "Una caución es un préstamo."]


# ── 4. pre_retrieval_answer: causes 4 and 5 ─────────────────────────────────

class _Result:
    def __init__(self, output):
        self.output = output

    def all_messages(self):
        return []


def _turn(question, output, demo_db):
    db_path, workspace = demo_db

    async def run_agent(prompt, history):
        return _Result(output)

    cfg = SimpleNamespace(off_limits=["cedear", "cedears"], data_aliases={})
    return asyncio.run(pre_retrieval_answer(
        question, config=cfg, db_path=db_path, workspace=workspace,
        history=[], language="es", run_agent=run_agent,
    ))


def test_data_answer_without_tool_evidence_is_cause_4(demo_db, monkeypatch):
    # Force the data path: the question names data, and no wiki page is found.
    monkeypatch.setattr(preretrieval, "retrieve_wiki", lambda *a, **k: [])
    out = _turn("¿A cuánto está el dólar MEP?", "El MEP está a 1200.", demo_db)
    assert out.startswith("No encontré en las páginas ni en los documentos")


def test_model_fixed_sentence_is_cause_5(demo_db):
    raw = f"{fixed_sentence('es')} Las páginas no dan la tasa."
    out = _turn("¿Cuánto rinden los plazos fijos?", raw, demo_db)
    assert out.startswith(raw)
    assert "El modelo no encontró la respuesta" in out
    assert "- Plazo fijo" in out                          # suggestions from the roster


def test_answer_not_opening_with_the_sentence_is_left_alone(demo_db):
    out = _turn("¿Cuánto rinden los plazos fijos?", "Un plazo fijo rinde una tasa fija.", demo_db)
    assert "El modelo no encontró" not in out
