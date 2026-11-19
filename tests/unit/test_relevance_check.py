"""The check that the wiki pages answer the question, in the model workflow of the
pre-retrieval mode (`domain/chat/relevance.py`, `domain/chat/workflow/model_calls.py`).

The retrieval is patched; the model and the judge are fakes that return their answers in
order. «¿En qué se convertían los lacayos?» gets the page of Cenicienta, which does not
say it; the lizards are only in the source document."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from domain import tracing
from domain.chat import preretrieval, relevance
from tests.helpers.workflow_path import steps

CFG = SimpleNamespace(blacklist=[], data_aliases={})
PAGE = "[/wiki/concepts/cenicienta.md]\nCenicienta va al baile con la ayuda del hada madrina y pierde un zapato de cristal."
FRAGMENT = "[sources/La Cenicienta.pdf]\nA medianoche los lacayos volvían a ser lagartos."
QUESTION = "¿En qué se convertían los lacayos de Cenicienta a la medianoche?"
QUOTED = "Evidencia: «Cenicienta va al baile con la ayuda del hada madrina»\n\nVa al baile."
INVENTED = "Evidencia: «los lacayos se convertían en ratones»\n\nEn ratones."
NO_QUOTE = "Evidencia: ninguna\n\nNo alcanza."
SECOND = "A medianoche volvían a ser lagartos."


class _Result:
    def __init__(self, output):
        self.output = output

    def all_messages(self):
        return []


class _NoDatasets:
    def categories(self):
        return []


def _turn(monkeypatch, outputs, *, check, docs=(FRAGMENT,), verdicts=(), open_page=None):
    monkeypatch.setattr(preretrieval, "build_vocabulary", lambda src: set())
    monkeypatch.setattr(preretrieval, "retrieve_wiki", lambda db, q, **k: [PAGE])
    monkeypatch.setattr(preretrieval, "retrieve_source_chunks", lambda db, q, **k: list(docs))
    monkeypatch.setattr(preretrieval, "LocalMarkdownSource", lambda p: _NoDatasets())
    monkeypatch.setattr(preretrieval, "concept_page_names", lambda db: ["Cenicienta"])
    monkeypatch.setattr(preretrieval, "retrieve_collection_pages", lambda ws: [])
    monkeypatch.setattr(preretrieval, "roster_suggestions", lambda *a, **k: [])
    monkeypatch.setattr(preretrieval, "search_suggestions", lambda *a, **k: [])
    prompts, judged = [], []

    async def run_agent(prompt, history):
        prompts.append(prompt)
        return _Result(outputs[len(prompts) - 1])

    async def judge(question, context):
        judged.append((question, context))
        return verdicts[len(judged) - 1]

    async def go():
        with tracing.collect() as path:
            answer = await preretrieval.pre_retrieval_answer(
                QUESTION, config=CFG, db_path="db", workspace=Path("/tmp/wp"), history=[], language="es",
                run_agent=run_agent, sentence_verifier=True, relevance=check, judge=judge,
                open_page=open_page)
        return answer, path

    answer, path = asyncio.run(go())
    return SimpleNamespace(answer=answer, nodes=steps(path), path=path, prompts=prompts, judged=judged)


def _mark(path, node):
    return next(entry for entry in path if entry["node"] == node)["attributes"]


# ── The pure functions ────────────────────────────────────────────────────────

def test_a_quote_is_found_ignoring_case_spaces_quotes_and_markdown():
    quote = relevance.evidence_quote("**Evidencia:** «CENICIENTA  va al baile con la ayuda»\n\nTexto.")
    assert quote == "cenicienta va al baile con la ayuda"
    assert relevance.quote_found(quote, PAGE)


@pytest.mark.parametrize("answer", [NO_QUOTE, "Sin línea de evidencia.", "Evidencia: «la reina»"])
def test_none_no_line_or_a_short_quote_is_not_found(answer):
    assert not relevance.quote_found(relevance.evidence_quote(answer), PAGE)


@pytest.mark.parametrize(("output", "yes"), [("no", False), ("No.", False), ("sí", True), ("Sí, responde.", True),
                                             ("quizás", True), ("", True)])
def test_only_a_no_of_the_judge_fails(output, yes):
    assert relevance.judge_says_yes(output) is yes


def test_an_unknown_choice_is_no_check():
    assert relevance.choice("jev") == relevance.NONE and relevance.choice("judge") == relevance.JUDGE


# ── The evidence quote ───────────────────────────────────────────────────────

def test_the_evidence_check_asks_for_the_quote_in_the_prompt(monkeypatch):
    run = _turn(monkeypatch, [QUOTED], check="evidence")
    assert "Evidencia: «frase copiada del contexto»" in run.prompts[0]


def test_a_quote_found_in_the_pages_keeps_the_answer(monkeypatch):
    run = _turn(monkeypatch, [QUOTED], check="evidence")
    assert len(run.prompts) == 1 and run.answer.startswith(QUOTED)
    assert "Q_EVIDENCE_QUOTE" in run.nodes and "Q_RETRY_SOURCES" not in run.nodes
    assert _mark(run.path, "Q_EVIDENCE_QUOTE")["found"] is True


@pytest.mark.parametrize("output", [INVENTED, NO_QUOTE])
def test_a_quote_not_in_the_pages_retries_with_the_source_documents(monkeypatch, output):
    run = _turn(monkeypatch, [output, SECOND], check="evidence")
    start = run.nodes.index("Q_AGENT_PAGES")
    assert run.nodes[start:start + 4] == ["Q_AGENT_PAGES", "Q_EVIDENCE_QUOTE", "Q_RETRY_SOURCES", "Q_AGENT_FRAGMENTS"]
    assert run.answer.startswith(SECOND) and "lagartos" in run.prompts[1]


def test_a_failed_check_with_no_fragment_shows_the_answer_with_a_warning(monkeypatch):
    run = _turn(monkeypatch, [INVENTED], check="evidence", docs=())
    assert run.answer.startswith(INVENTED)
    assert "La comprobación (cita de evidencia) indica que las páginas de la wiki no responden esta pregunta." in run.answer
    assert "Q_RELEVANCE_WARNING" in run.nodes and "R_MODEL" not in run.nodes


# ── The judge ────────────────────────────────────────────────────────────────

def test_a_yes_of_the_judge_keeps_the_answer(monkeypatch):
    run = _turn(monkeypatch, ["Va al baile."], check="judge", verdicts=["sí"])
    assert len(run.prompts) == 1 and "Q_JUDGE_PAGES" in run.nodes and "Q_RETRY_SOURCES" not in run.nodes
    question, context = run.judged[0]
    assert question == QUESTION and "hada madrina" in context
    assert "Evidencia" not in run.prompts[0]


def test_the_judge_is_told_which_page_is_open(monkeypatch):
    """cue-16 of the battery: with concepts/espejo-magico open, «¿Qué papel cumple en la
    historia?» names no subject; the judge answered no because it did not know the page."""
    monkeypatch.setattr(preretrieval, "_open_page_block", lambda ws, page: PAGE if page else None)
    run = _turn(monkeypatch, ["Va al baile."], check="judge", verdicts=["sí"], open_page="concepts/cenicienta")
    question, _ = run.judged[0]
    assert question == preretrieval.with_open_page(QUESTION, "concepts/cenicienta", "es")
    assert question.startswith("(El usuario está leyendo la página /wiki/concepts/cenicienta.md.")


def test_a_no_of_the_judge_retries_with_the_source_documents(monkeypatch):
    run = _turn(monkeypatch, ["Va al baile.", SECOND], check="judge", verdicts=["no"])
    start = run.nodes.index("Q_AGENT_PAGES")
    assert run.nodes[start:start + 4] == ["Q_AGENT_PAGES", "Q_JUDGE_PAGES", "Q_RETRY_SOURCES", "Q_AGENT_FRAGMENTS"]
    assert _mark(run.path, "Q_JUDGE_PAGES") == {"verdict": "no", "passed": False}


def test_the_warning_names_the_judge(monkeypatch):
    run = _turn(monkeypatch, ["Va al baile."], check="judge", verdicts=["no"], docs=())
    assert "La comprobación (juez) indica" in run.answer


# ── No check ─────────────────────────────────────────────────────────────────

def test_no_check_runs_neither_check_nor_the_judge(monkeypatch):
    run = _turn(monkeypatch, ["Va al baile."], check="none")
    assert not {"Q_EVIDENCE_QUOTE", "Q_JUDGE_PAGES", "Q_RELEVANCE_WARNING"} & set(run.nodes)
    assert run.judged == [] and "Evidencia" not in run.prompts[0]


def test_a_refusal_is_retried_without_the_check(monkeypatch):
    from domain.chat.refusal import fixed_sentence
    run = _turn(monkeypatch, [fixed_sentence("es") + " No está.", SECOND], check="judge", verdicts=[])
    assert "Q_JUDGE_PAGES" not in run.nodes and "Q_RETRY_SOURCES" in run.nodes
