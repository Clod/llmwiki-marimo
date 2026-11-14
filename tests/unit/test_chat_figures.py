"""The figure check in the chat service: `check_turn_figures`, the pre-retrieval and strict
modes, and its absence in the streaming mode.
The agents are stand-ins; no model is called."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic_ai import AgentRunResultEvent
from pydantic_ai.messages import (
    ModelRequest, PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta, ToolReturnPart,
)

from domain.i18n import get_locale
from domain.tools.wiki_fs import create_page
from domain.verify.models import ContextPassage
from services import chat
from services.wiki import open_wiki, save_page

BODY = "Cinderella lost a glass slipper at the royal ball and the prince searched the kingdom. " * 3
DATASET = (
    "**1 dataset row(s)** for categoria='tasas':\n\n"
    "| clave | metrica | valor | unidad | dims | as_of | fuente |\n"
    "|---|---|---|---|---|---|---|\n"
    "| peso | tna | 1187.5 | ARS | plazo=30 | 2026-10-01 | bcra.gob.ar |\n"
)
ADVISORY = (
    "Para $1,000,000 a ~30 días (ARS), si la tasa actual se mantiene:\n\n"
    "| opción | clave | plazo | TEA | ganancia est. | al fecha | fuente |\n"
    "|---|---|---|---|---|---|---|\n"
    "| plazo_fijo | banco | 30 | 12.34% | $9,500 | 2026-10-01 | bcra.gob.ar |\n"
)


def msg(role, content, context=()):
    return SimpleNamespace(role=role, content=content, context=context)


def config(language="en", tolerance=0.05):
    return SimpleNamespace(language=language, number_tolerance=tolerance, off_limits=[], data_aliases={},
                           system_prompt="", suggested_prompts=[])


class Agent:
    def __init__(self, output, messages=()):
        self.output, self.messages = output, list(messages)

    async def run(self, prompt, deps, message_history):
        return SimpleNamespace(output=self.output, all_messages=lambda: self.messages)

    @asynccontextmanager
    async def run_stream_events(self, prompt, deps, message_history):
        async def events():
            yield PartStartEvent(index=0, part=TextPart(self.output[:5]))
            yield PartDeltaEvent(index=0, delta=TextPartDelta(self.output[5:]))
            yield AgentRunResultEvent(SimpleNamespace(output=self.output, all_messages=lambda: self.messages))

        yield events()


def agents(output, messages=(), language="en"):
    return chat.ChatAgents(config=config(language), agent=Agent(output, messages),
                           agent_pre_retrieval=Agent(output, messages))


def returns(*pairs):
    parts = [ToolReturnPart(tool_name=name, tool_call_id=f"c{i}", content=content)
             for i, (name, content) in enumerate(pairs)]
    return [ModelRequest(parts=parts)]


def run(coro):
    return asyncio.run(coro)


def stream_result(wiki, ag, messages):
    async def last():
        result = None
        async for item in chat.chat_turn_stream_with_context(wiki, ag, messages):
            result = item
        return result

    return run(last())


@pytest.fixture
def wiki(tmp_path):
    wiki = open_wiki(tmp_path / "tales")
    create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "cinderella", "Cinderella", BODY, [])
    save_page(wiki, "concepts/cinderella", BODY, None)
    return wiki


# ── check_turn_figures ───────────────────────────────────────────────────────

def test_an_exact_figure_leaves_the_answer_unchanged_and_adds_no_note():
    result = chat.ChatResult("Rinde 1.187,5.", (ContextPassage("datasets/tasas.md", DATASET),))
    checked = chat.check_turn_figures(result, [msg("user", "¿Cuánto rinde?")], config())
    assert checked.answer == result.answer and checked.generated == ()
    assert [f.status for f in checked.figures.findings] == ["exact"]


def test_an_unsupported_figure_is_reported_and_the_text_is_not_edited():
    result = chat.ChatResult("En dólares rinde menos del 1% anual.", (ContextPassage("datasets/tasas.md", DATASET),))
    checked = chat.check_turn_figures(result, [msg("user", "¿Y en dólares?")], config("es"))
    assert checked.answer == result.answer
    assert [f.figure.text for f in checked.figures.unsupported()] == ["1%"]


def test_a_rounded_figure_appends_the_note_of_the_language_after_every_other_text():
    table = "| opción | TEA |\n|---|---|\n| x | 12.34% |"
    result = chat.ChatResult(f"Rinde cerca de 12,3 %.\n\n{table}\n\nFuente: bcra.gob.ar", generated=(table,))
    for language, tail in (("es", "hasta un 5 %."), ("en", "by up to 5%.")):
        checked = chat.check_turn_figures(result, [msg("user", "q")], config(language))
        note = get_locale(language).rounded_figures_note.replace("%(pct)s", "5")
        assert checked.answer == f"{result.answer}\n\n{note}" and checked.answer.endswith(tail)
        assert checked.generated == (table, note)
        assert checked.answer.count(note) == 1


def test_the_note_names_the_data_consulted_when_the_answer_has_no_table():
    result = chat.ChatResult("Son unos 1.200.", ())
    checked = chat.check_turn_figures(result, [msg("user", "¿Son 1187,5?")], config("es"))
    assert "los datos consultados" in checked.answer and "de la tabla" not in checked.answer
    with_table = chat.check_turn_figures(
        chat.ChatResult("Son unos 1.200.", (ContextPassage("datasets/tasas.md", DATASET),)),
        [msg("user", "q")], config("es"))
    assert "de la tabla" in with_table.answer


def test_the_percentage_of_the_note_follows_the_tolerance():
    result = chat.ChatResult("Son unos 1.200.", (ContextPassage("datasets/tasas.md", DATASET),))
    assert "hasta un 7,5 %" in chat.check_turn_figures(result, [msg("user", "q")], config("es", 0.075)).answer


def test_the_generated_text_the_citation_lines_and_the_warning_are_not_prose():
    warning = get_locale("es").tier2_warning
    generated = "Para $9,999 a 77 días: rinde 55,5%"
    answer = f"Texto sin cifras.\n\n{generated}\n\nFuente: informe de 4321 páginas\nReferencia: x 8.888\n\n{warning}"
    checked = chat.check_turn_figures(chat.ChatResult(answer, (), (generated,)), [msg("user", "q")], config("es"))
    assert checked.figures.findings == ()


def test_the_sources_are_the_context_the_generated_texts_the_user_and_the_earlier_contexts():
    earlier = msg("assistant", "Respuesta con 99,9% inventado.", (ContextPassage("datasets/tasas.md", DATASET),))
    messages = [msg("user", "Tengo $100.000"), earlier, msg("user", "¿Y ahora?")]
    answer = "Con tus $100.000 a 1.187,5, antes dije 99,9%."
    statuses = {f.figure.text: f.status
                for f in chat.check_turn_figures(chat.ChatResult(answer), messages, config()).figures.findings}
    assert statuses == {"$100.000": "exact", "1.187,5": "exact", "99,9%": "unsupported"}


def test_a_figure_of_the_advisory_table_of_this_turn_is_exact_even_when_the_prose_repeats_it():
    result = chat.ChatResult("Ganás $9,500 con 12,34% de TEA.", (), (ADVISORY,))
    checked = chat.check_turn_figures(result, [msg("user", "q")], config())
    assert [f.status for f in checked.figures.findings] == ["exact", "exact"]


def test_a_refusal_of_the_code_has_an_empty_report():
    result = chat.ChatResult("No tengo información (cifra 7.777).")
    checked = chat.check_turn_figures(result, [msg("user", "q")], config("es"), refused=True)
    assert checked.figures.findings == () and checked.answer == result.answer


def test_check_turn_figures_leaves_the_path_as_it_is():
    # The counts are the attributes of the node C3, which the caller records.
    path = ({"node": "C1", "diagram": "reading", "attributes": {}},
            {"node": "T5", "diagram": "reading", "attributes": {"final_answer": "x"}})
    result = chat.ChatResult("Rinde 1.200 y 7.777.", (ContextPassage("datasets/tasas.md", DATASET),), path=path)
    checked = chat.check_turn_figures(result, [msg("user", "q")], config())
    assert checked.path == path
    assert chat.figure_counts(checked.figures) == dict(figures_checked=2, figures_unsupported=1, figures_rounded=1)


def c3(result):
    """The attributes of the node C3 in the path of a turn."""
    (node,) = [n for n in result.path if n["node"] == "C3"]
    return node["attributes"]


# ── the three modes ──────────────────────────────────────────────────────────

def test_strict_returns_the_report_and_appends_the_note_once_after_the_table_and_the_citation(wiki):
    run_messages = returns(("query_dataset", DATASET), ("estimar_alternativas", ADVISORY))
    result = run(chat.chat_turn_with_context(
        wiki, agents("La tasa ronda 1.200 y no 7.777.", run_messages, "es"),
        [msg("user", "¿Cuánto rinde el plazo fijo?")], mode=chat.STRICT))
    note = get_locale("es").rounded_figures_note.replace("%(pct)s", "5")
    assert result.answer.count(note) == 1 and result.answer.endswith(note)
    assert result.answer.index(ADVISORY.strip()) < result.answer.index("Referencia:") < result.answer.index(note)
    assert [f.figure.text for f in result.figures.unsupported()] == ["7.777"]
    assert [f.figure.text for f in result.figures.rounded()] == ["1.200"]
    assert [n["node"] for n in result.path][-2:] == ["C3", "T5"] and c3(result)["figures_unsupported"] == 1
    assert chat.figure_counts(result.figures) == dict(figures_checked=2, figures_unsupported=1, figures_rounded=1)


def test_strict_without_rounded_figures_adds_no_note(wiki):
    result = run(chat.chat_turn_with_context(
        wiki, agents("La tasa es 1.187,5.", returns(("query_dataset", DATASET))),
        [msg("user", "rate?")], mode=chat.STRICT))
    assert result.figures.rounded() == () and "approximations" not in result.answer


def test_strict_refusal_has_an_empty_report(wiki):
    result = run(chat.chat_turn_with_context(
        wiki, agents("Paris has 2.100.000 people."), [msg("user", "capital of France?")], mode=chat.STRICT))
    assert result.raw_answer is not None
    assert result.figures.findings == ()


def test_pre_retrieval_returns_the_report(wiki):
    result = run(chat.chat_turn_with_context(
        wiki, agents("Cinderella lost 7.777 slippers, about 1.200 of 1187,5."),
        [msg("user", "How many slippers did Cinderella lose, 1187,5?")], mode=chat.PRE_RETRIEVAL))
    assert [f.figure.text for f in result.figures.unsupported()] == ["7.777"]
    assert [f.figure.text for f in result.figures.rounded()] == ["1.200"]
    assert result.answer.endswith(get_locale("en").rounded_figures_note_data.replace("%(pct)s", "5"))
    assert [n["node"] for n in result.path][-2:] == ["C3", "Q21"] and c3(result)["figures_checked"] == 3


def test_pre_retrieval_refusal_has_an_empty_report(wiki):
    result = run(chat.chat_turn_with_context(
        wiki, agents("x"), [msg("user", "Will it rain 7.777 days in Rosario?")], mode=chat.PRE_RETRIEVAL))
    assert result.figures.findings == ()


def test_streaming_returns_the_text_of_the_agent_with_no_report_and_no_note(wiki):
    written = "Cinderella lost 7.777 slippers, about 1.200."
    result = stream_result(wiki, agents(written), [msg("user", "How many slippers, 1187,5?")])
    assert result.answer == written
    assert result.figures is None and result.generated == ()
    assert "C3" not in [n["node"] for n in result.path]


def test_chat_turn_returns_the_answer_with_the_note(wiki):
    answer = run(chat.chat_turn(
        wiki, agents("About 1.200.", returns(("query_dataset", DATASET))), [msg("user", "rate?")],
        mode=chat.STRICT))
    assert answer.endswith(get_locale("en").rounded_figures_note.replace("%(pct)s", "5"))


# ── the note above the advisory table (postprocess.TABLE_NOTE) ──────────────

def test_an_answer_with_the_table_note_marks_no_figure_of_the_note_nor_of_the_table(wiki):
    from domain.chat.postprocess import TABLE_NOTE

    run_messages = returns(("query_dataset", DATASET), ("estimar_alternativas", ADVISORY))
    result = run(chat.chat_turn_with_context(
        wiki, agents("La TEA del plazo fijo es 12.34% y la ganancia ronda $9,500.", run_messages, "es"),
        [msg("user", "¿Cuánto rinde el plazo fijo?")], mode=chat.STRICT))
    unchecked, computed = TABLE_NOTE["es"]
    note_at, table_at = result.answer.index(f"{unchecked} {computed}"), result.answer.index(ADVISORY.strip())
    assert result.answer.index("La TEA") < note_at < table_at         # prose, note, table
    assert result.figures.unsupported() == ()
    assert all(f.figure.end <= note_at for f in result.figures.findings)  # only the prose is checked
    assert {f.figure.text for f in result.figures.findings} == {"12.34%", "$9,500"}


def test_the_figures_of_the_table_note_are_not_checked(monkeypatch):
    from domain.chat import postprocess

    monkeypatch.setitem(postprocess.TABLE_NOTE, "es", ("El modelo escribió 7.777 cifras.", "El código calculó 4.242."))
    note = " ".join(postprocess.TABLE_NOTE["es"])
    answer = f"Rinde 1.187,5.\n\n{note}\n\n| a |\n|---|\n| 1.187,5 |"
    result = chat.ChatResult(answer, (ContextPassage("datasets/tasas.md", DATASET),))
    checked = chat.check_turn_figures(result, [msg("user", "q")], config("es"))
    assert [f.figure.text for f in checked.figures.findings] == ["1.187,5", "1.187,5"]
    assert checked.figures.unsupported() == ()
