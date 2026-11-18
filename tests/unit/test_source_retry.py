"""The retry Q_RETRY_SOURCES of the pre-retrieval mode: the model refused with the wiki
pages, the code searches the source documents and calls the model again with the fragments
(`domain/chat/workflow/model_calls.py`, step Q_RETRY_SOURCES). The retrieval is patched; the model
is a fake that returns its answers in order."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from domain import tracing
from domain.chat import preretrieval
from domain.chat.refusal import fixed_sentence
from tests.helpers.workflow_path import steps

CFG = SimpleNamespace(blacklist=[], data_aliases={"dolar": ["billete verde"]})
PAGE = "[/wiki/concepts/cenicienta.md]\nCenicienta va al baile con la ayuda del hada madrina."
FRAGMENT = "[sources/La Cenicienta.pdf]\nA medianoche los lacayos volvían a ser lagartos."
REFUSAL = fixed_sentence("es") + " Ninguna página menciona a los lacayos."
QUESTION = "¿En qué se convertían los lacayos de Cenicienta a la medianoche?"


class _Result:
    def __init__(self, output):
        self.output = output

    def all_messages(self):
        return []


class _NoDatasets:
    def categories(self):
        return []


def _agent(*outputs):
    prompts = []

    async def run_agent(prompt, history):
        prompts.append(prompt)
        return _Result(outputs[len(prompts) - 1])

    run_agent.prompts = prompts
    return run_agent


def _answer(run_agent, monkeypatch, *, docs=(FRAGMENT,), vocab=frozenset(), concepts=("Cenicienta",),
            sentence_verifier=False):
    monkeypatch.setattr(preretrieval, "build_vocabulary", lambda src: set(vocab))
    monkeypatch.setattr(preretrieval, "retrieve_wiki", lambda db, q, **k: [PAGE])
    monkeypatch.setattr(preretrieval, "retrieve_source_chunks", lambda db, q, **k: list(docs))
    monkeypatch.setattr(preretrieval, "LocalMarkdownSource", lambda p: _NoDatasets())
    monkeypatch.setattr(preretrieval, "concept_page_names", lambda db: list(concepts))
    monkeypatch.setattr(preretrieval, "retrieve_collection_pages", lambda ws: [])
    monkeypatch.setattr(preretrieval, "roster_suggestions", lambda *a, **k: [])
    monkeypatch.setattr(preretrieval, "search_suggestions", lambda *a, **k: [])
    passages = []

    async def go():
        with tracing.collect() as path:
            answer = await preretrieval.pre_retrieval_answer(
                QUESTION, config=CFG, db_path="db", workspace=Path("/tmp/wp"), history=[], language="es",
                run_agent=run_agent, on_context=passages.extend, sentence_verifier=sentence_verifier)
        return answer, steps(path), path

    answer, nodes, path = asyncio.run(go())
    return answer, nodes, path, passages


def test_a_refusal_with_the_wiki_pages_is_retried_with_the_source_fragments(monkeypatch):
    agent = _agent(REFUSAL, "A medianoche volvían a ser lagartos.")
    answer, nodes, _, passages = _answer(agent, monkeypatch, sentence_verifier=True)
    assert answer.startswith("A medianoche volvían a ser lagartos.")
    assert "documento fuente" in answer                           # Q_SOURCE_WARNING of a Tier-2 answer
    assert "lagartos" not in agent.prompts[0] and "lagartos" in agent.prompts[1]
    start = nodes.index("Q_AGENT_PAGES")
    assert nodes[start:start + 3] == ["Q_AGENT_PAGES", "Q_RETRY_SOURCES", "Q_AGENT_FRAGMENTS"]
    assert "R_MODEL" not in nodes and "Q_SOURCE_WARNING" in nodes
    assert [p.page for p in passages] == ["/wiki/concepts/cenicienta.md", "La Cenicienta.pdf"]


def test_the_retry_records_the_refused_answer_and_the_fragments(monkeypatch):
    _, _, path, _ = _answer(_agent(REFUSAL, "Lagartos."), monkeypatch, sentence_verifier=True)
    retry = next(entry for entry in path if entry["node"] == "Q_RETRY_SOURCES")
    assert retry["attributes"]["refused_output"] == REFUSAL
    assert retry["attributes"]["fragments"] == ["sources/La Cenicienta.pdf"]


def test_with_no_fragment_the_refusal_stays(monkeypatch):
    agent = _agent(REFUSAL)
    answer, nodes, _, _ = _answer(agent, monkeypatch, docs=())
    assert len(agent.prompts) == 1 and nodes[-1] == "R_MODEL"
    assert nodes.index("Q_RETRY_SOURCES") < nodes.index("R_MODEL")


def test_a_second_refusal_ends_in_r_model(monkeypatch):
    agent = _agent(REFUSAL, REFUSAL)
    _, nodes, _, _ = _answer(agent, monkeypatch, sentence_verifier=True)
    assert len(agent.prompts) == 2 and nodes[-1] == "R_MODEL"


def test_an_answer_with_the_wiki_pages_is_not_retried(monkeypatch):
    agent = _agent("Cenicienta fue al baile.")
    _, nodes, _, _ = _answer(agent, monkeypatch)
    assert len(agent.prompts) == 1 and "Q_RETRY_SOURCES" not in nodes


def test_a_refusal_of_a_data_question_is_not_retried(monkeypatch):
    """Only an answer with the wiki pages retries: a data question (Q_AGENT_TOOLS) does not."""
    monkeypatch.setattr(preretrieval, "retrieve_wiki", lambda db, q, **k: [])
    agent = _agent(REFUSAL)

    async def go():
        monkeypatch.setattr(preretrieval, "build_vocabulary", lambda src: {"dolar"})
        monkeypatch.setattr(preretrieval, "retrieve_source_chunks", lambda db, q, **k: [FRAGMENT])
        monkeypatch.setattr(preretrieval, "LocalMarkdownSource", lambda p: _NoDatasets())
        monkeypatch.setattr(preretrieval, "concept_page_names", lambda db: [])
        monkeypatch.setattr(preretrieval, "retrieve_collection_pages", lambda ws: [])
        monkeypatch.setattr(preretrieval, "roster_suggestions", lambda *a, **k: [])
        monkeypatch.setattr(preretrieval, "search_suggestions", lambda *a, **k: [])
        with tracing.collect() as path:
            await preretrieval.pre_retrieval_answer(
                "¿a cuánto está el billete verde?", config=CFG, db_path="db", workspace=Path("/tmp/wp"),
                history=[], language="es", run_agent=agent)
        return steps(path)

    nodes = asyncio.run(go())
    assert "Q_AGENT_TOOLS" in nodes and "Q_RETRY_SOURCES" not in nodes and len(agent.prompts) == 1
