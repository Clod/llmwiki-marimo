"""The middle block of a pre-retrieval turn, pinned: the calls to the model and the retry
with the source documents, between the input workflow and the output workflow.

Each case runs a whole turn with the retrieval patched and a fake model that returns its
answers in order, and pins three things: the sequence of steps of the path (decisions left
out, `tests.helpers.workflow_path.steps`), the number of model calls, and the start of the
final answer. The cases were recorded on the code before the model calls became a graph
(2026-11-17); a change of structure must leave them as they are."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from domain import tracing
from domain.chat import preretrieval
from domain.chat.refusal import fixed_sentence
from tests.helpers.workflow_path import steps

CFG = SimpleNamespace(blacklist=[], data_aliases={"dolar": ["billete verde"]})
PAGE = "[/wiki/concepts/cenicienta.md]\nCenicienta va al baile con la ayuda del hada madrina."
FRAGMENT = "[sources/La Cenicienta.pdf]\nA medianoche los lacayos volvían a ser lagartos."
REFUSAL = fixed_sentence("es") + " Ninguna página menciona a los lacayos."
LACAYOS = "¿En qué se convertían los lacayos de Cenicienta a la medianoche?"
DOLAR = "¿a cuánto está el billete verde?"
CAUCION = "¿qué es una caución bursátil?"
CAUCION_DOC = "[12 Cauciones.docx]\nLa caución bursátil es un préstamo de corto plazo garantizado con títulos."
GROUNDED = [ModelRequest(parts=[ToolReturnPart(tool_name="query_dataset", content="| MEP | 1180 |",
                                               tool_call_id="c1")])]


class _Result:
    def __init__(self, output, messages=()):
        self.output = output
        self._messages = list(messages)

    def all_messages(self):
        return self._messages


class _NoDatasets:
    def categories(self):
        return []


def _run(monkeypatch, question, outputs, *, wiki=(PAGE,), docs=(FRAGMENT,), vocab=(), concepts=("Cenicienta",),
         collection=(), messages=(), sentence_verifier=False, language="es"):
    monkeypatch.setattr(preretrieval, "build_vocabulary", lambda src: set(vocab))
    monkeypatch.setattr(preretrieval, "retrieve_wiki", lambda db, q, **k: list(wiki))
    monkeypatch.setattr(preretrieval, "retrieve_source_chunks", lambda db, q, **k: list(docs))
    monkeypatch.setattr(preretrieval, "LocalMarkdownSource", lambda p: _NoDatasets())
    monkeypatch.setattr(preretrieval, "concept_page_names", lambda db: list(concepts))
    monkeypatch.setattr(preretrieval, "retrieve_collection_pages", lambda ws: list(collection))
    monkeypatch.setattr(preretrieval, "roster_suggestions", lambda *a, **k: [])
    monkeypatch.setattr(preretrieval, "search_suggestions", lambda *a, **k: [])
    prompts = []

    async def run_agent(prompt, history):
        prompts.append(prompt)
        return _Result(outputs[len(prompts) - 1], messages)

    async def go():
        with tracing.collect() as path:
            answer = await preretrieval.pre_retrieval_answer(
                question, config=CFG, db_path="db", workspace=Path("/tmp/wp"), history=[], language=language,
                run_agent=run_agent, sentence_verifier=sentence_verifier)
        return answer, steps(path)

    answer, nodes = asyncio.run(go())
    return nodes, len(prompts), answer


CASES = {
    "pages_answered": dict(question=LACAYOS, outputs=["Cenicienta fue al baile."]),
    "pages_refused_then_fragments": dict(question=LACAYOS, outputs=[REFUSAL, "A medianoche volvían a ser lagartos."]),
    "pages_refused_then_fragments_verified": dict(
        question=LACAYOS, outputs=[REFUSAL, "A medianoche volvían a ser lagartos."], sentence_verifier=True),
    "pages_refused_no_fragment": dict(question=LACAYOS, outputs=[REFUSAL], docs=()),
    "pages_refused_twice": dict(question=LACAYOS, outputs=[REFUSAL, REFUSAL], sentence_verifier=True),
    "data_answered": dict(question=DOLAR, outputs=["El dólar MEP está a 1180."], wiki=(), docs=(),
                          vocab=("dolar",), concepts=(), messages=GROUNDED),
    "data_refused": dict(question=DOLAR, outputs=[REFUSAL], wiki=(), docs=(FRAGMENT,), vocab=("dolar",), concepts=()),
    "fragments_answered": dict(question=CAUCION, outputs=[CAUCION_DOC.split("\n", 1)[1]], wiki=(),
                               docs=(CAUCION_DOC,), concepts=("caución bursátil",)),
    "collection_answered": dict(
        question="What tales are in this wiki?", outputs=["Cinderella and Snow White. Referencia: wiki/overview.md"],
        wiki=(), docs=(), concepts=(), language="en",
        collection=("[wiki/overview.md]\nCinderella, Snow White, Little Red Riding Hood.",)),
}

EXPECTED = {'collection_answered': (['C_COUNT_HISTORY',
                          'C_WHICH_MODE',
                          'C_MARK_REFUSED',
                          'C_DROP_TABLES',
                          'C_TRIM_TURNS',
                          'C_ORIGIN_LINES',
                          'Q_QUESTION',
                          'Q_NORMALIZE',
                          'Q_IN_BLACKLIST',
                          'Q_STEM',
                          'Q_NAMES_DATA',
                          'Q_IN_ROSTER',
                          'Q_PAGE_OPEN',
                          'Q_COLLECTION_PAGES',
                          'Q_COLLECTION_OPEN_PAGE',
                          'Q_IS_COLLECTION',
                          'C_OPEN_PAGE_NOTE',
                          'Q_SYNONYMS',
                          'Q_PROMPT',
                          'Q_AGENT_COLLECTION',
                          'Q_MODEL_REFUSED',
                          'Q_ADVISORY_TABLE',
                          'Q_OFFER_KEYS',
                          'Q_CITE_TOOLS',
                          'Q_CITE_PAGES',
                          'C_FIGURES',
                          'Q_SHOWN'],
                         1,
                         'Cinderella and Snow White. Referencia: wiki/overview.md'),
 'data_answered': (['C_COUNT_HISTORY',
                    'C_WHICH_MODE',
                    'C_MARK_REFUSED',
                    'C_DROP_TABLES',
                    'C_TRIM_TURNS',
                    'C_ORIGIN_LINES',
                    'Q_QUESTION',
                    'Q_NORMALIZE',
                    'Q_IN_BLACKLIST',
                    'Q_STEM',
                    'Q_NAMES_DATA',
                    'Q_IN_ROSTER',
                    'Q_PAGE_OPEN',
                    'Q_SEARCH_WIKI',
                    'Q_SEARCH_SOURCES',
                    'Q_COLLECTION_PAGES',
                    'Q_COLLECTION_OPEN_PAGE',
                    'Q_PAGE_FOUND',
                    'Q_IS_COLLECTION',
                    'Q_IS_DATA',
                    'Q_NAMES_CATEGORY',
                    'C_OPEN_PAGE_NOTE',
                    'Q_SYNONYMS',
                    'Q_PROMPT',
                    'Q_AGENT_TOOLS',
                    'Q_TOOL_EVIDENCE',
                    'Q_MODEL_REFUSED',
                    'Q_ADVISORY_TABLE',
                    'Q_OFFER_KEYS',
                    'Q_CITE_TOOLS',
                    'Q_CITE_PAGES',
                    'C_FIGURES',
                    'Q_SHOWN'],
                   1,
                   'El dólar MEP está a 1180.'),
 'data_refused': (['C_COUNT_HISTORY',
                   'C_WHICH_MODE',
                   'C_MARK_REFUSED',
                   'C_DROP_TABLES',
                   'C_TRIM_TURNS',
                   'C_ORIGIN_LINES',
                   'Q_QUESTION',
                   'Q_NORMALIZE',
                   'Q_IN_BLACKLIST',
                   'Q_STEM',
                   'Q_NAMES_DATA',
                   'Q_IN_ROSTER',
                   'Q_PAGE_OPEN',
                   'Q_SEARCH_WIKI',
                   'Q_SEARCH_SOURCES',
                   'Q_COLLECTION_PAGES',
                   'Q_COLLECTION_OPEN_PAGE',
                   'Q_PAGE_FOUND',
                   'Q_IS_COLLECTION',
                   'Q_IS_DATA',
                   'Q_NAMES_CATEGORY',
                   'C_OPEN_PAGE_NOTE',
                   'Q_SYNONYMS',
                   'Q_PROMPT',
                   'Q_AGENT_TOOLS',
                   'Q_TOOL_EVIDENCE',
                   'R_NO_EVIDENCE'],
                  1,
                  'No encontré en las páginas ni en los documentos de esta wiki'),
 'fragments_answered': (['C_COUNT_HISTORY',
                         'C_WHICH_MODE',
                         'C_MARK_REFUSED',
                         'C_DROP_TABLES',
                         'C_TRIM_TURNS',
                         'C_ORIGIN_LINES',
                         'Q_QUESTION',
                         'Q_NORMALIZE',
                         'Q_IN_BLACKLIST',
                         'Q_STEM',
                         'Q_NAMES_DATA',
                         'Q_IN_ROSTER',
                         'Q_PAGE_OPEN',
                         'Q_SEARCH_WIKI',
                         'Q_SEARCH_SOURCES',
                         'Q_COLLECTION_PAGES',
                         'Q_COLLECTION_OPEN_PAGE',
                         'Q_PAGE_FOUND',
                         'Q_IS_COLLECTION',
                         'Q_IS_DATA',
                         'Q_FRAGMENT_FOUND',
                         'C_OPEN_PAGE_NOTE',
                         'Q_SYNONYMS',
                         'Q_PROMPT',
                         'Q_AGENT_FRAGMENTS',
                         'Q_IS_GROUNDED',
                         'Q_MODEL_REFUSED',
                         'Q_ADVISORY_TABLE',
                         'Q_OFFER_KEYS',
                         'Q_CITE_TOOLS',
                         'Q_CITE_PAGES',
                         'Q_SOURCE_WARNING',
                         'C_FIGURES',
                         'Q_SHOWN'],
                        1,
                        'La caución bursátil es un préstamo de corto plazo garantizad'),
 'pages_answered': (['C_COUNT_HISTORY',
                     'C_WHICH_MODE',
                     'C_MARK_REFUSED',
                     'C_DROP_TABLES',
                     'C_TRIM_TURNS',
                     'C_ORIGIN_LINES',
                     'Q_QUESTION',
                     'Q_NORMALIZE',
                     'Q_IN_BLACKLIST',
                     'Q_STEM',
                     'Q_NAMES_DATA',
                     'Q_IN_ROSTER',
                     'Q_PAGE_OPEN',
                     'Q_SEARCH_WIKI',
                     'Q_COLLECTION_PAGES',
                     'Q_COLLECTION_OPEN_PAGE',
                     'Q_PAGE_FOUND',
                     'Q_NAMES_CATEGORY',
                     'C_OPEN_PAGE_NOTE',
                     'Q_SYNONYMS',
                     'Q_PROMPT',
                     'Q_AGENT_PAGES',
                     'Q_MODEL_REFUSED',
                     'Q_ADVISORY_TABLE',
                     'Q_OFFER_KEYS',
                     'Q_CITE_TOOLS',
                     'Q_CITE_PAGES',
                     'C_FIGURES',
                     'Q_SHOWN'],
                    1,
                    'Cenicienta fue al baile.\n\nReferencia: el modelo recibió esta'),
 'pages_refused_no_fragment': (['C_COUNT_HISTORY',
                                'C_WHICH_MODE',
                                'C_MARK_REFUSED',
                                'C_DROP_TABLES',
                                'C_TRIM_TURNS',
                                'C_ORIGIN_LINES',
                                'Q_QUESTION',
                                'Q_NORMALIZE',
                                'Q_IN_BLACKLIST',
                                'Q_STEM',
                                'Q_NAMES_DATA',
                                'Q_IN_ROSTER',
                                'Q_PAGE_OPEN',
                                'Q_SEARCH_WIKI',
                                'Q_COLLECTION_PAGES',
                                'Q_COLLECTION_OPEN_PAGE',
                                'Q_PAGE_FOUND',
                                'Q_NAMES_CATEGORY',
                                'C_OPEN_PAGE_NOTE',
                                'Q_SYNONYMS',
                                'Q_PROMPT',
                                'Q_AGENT_PAGES',
                                'Q_RETRY_SOURCES',
                                'Q_MODEL_REFUSED',
                                'R_MODEL'],
                               1,
                               'No encontré la respuesta en el texto que recibí de la wiki. '),
 'pages_refused_then_fragments': (['C_COUNT_HISTORY',
                                   'C_WHICH_MODE',
                                   'C_MARK_REFUSED',
                                   'C_DROP_TABLES',
                                   'C_TRIM_TURNS',
                                   'C_ORIGIN_LINES',
                                   'Q_QUESTION',
                                   'Q_NORMALIZE',
                                   'Q_IN_BLACKLIST',
                                   'Q_STEM',
                                   'Q_NAMES_DATA',
                                   'Q_IN_ROSTER',
                                   'Q_PAGE_OPEN',
                                   'Q_SEARCH_WIKI',
                                   'Q_COLLECTION_PAGES',
                                   'Q_COLLECTION_OPEN_PAGE',
                                   'Q_PAGE_FOUND',
                                   'Q_NAMES_CATEGORY',
                                   'C_OPEN_PAGE_NOTE',
                                   'Q_SYNONYMS',
                                   'Q_PROMPT',
                                   'Q_AGENT_PAGES',
                                   'Q_RETRY_SOURCES',
                                   'Q_AGENT_FRAGMENTS',
                                   'Q_IS_GROUNDED',
                                   'Q_MODEL_REFUSED',
                                   'Q_ADVISORY_TABLE',
                                   'Q_OFFER_KEYS',
                                   'Q_CITE_TOOLS',
                                   'Q_CITE_PAGES',
                                   'Q_SOURCE_WARNING',
                                   'C_FIGURES',
                                   'Q_SHOWN'],
                                  2,
                                  'A medianoche volvían a ser lagartos.\n\n> ⚠️ Esta respuesta pr'),
 'pages_refused_then_fragments_verified': (['C_COUNT_HISTORY',
                                            'C_WHICH_MODE',
                                            'C_MARK_REFUSED',
                                            'C_DROP_TABLES',
                                            'C_TRIM_TURNS',
                                            'C_ORIGIN_LINES',
                                            'Q_QUESTION',
                                            'Q_NORMALIZE',
                                            'Q_IN_BLACKLIST',
                                            'Q_STEM',
                                            'Q_NAMES_DATA',
                                            'Q_IN_ROSTER',
                                            'Q_PAGE_OPEN',
                                            'Q_SEARCH_WIKI',
                                            'Q_COLLECTION_PAGES',
                                            'Q_COLLECTION_OPEN_PAGE',
                                            'Q_PAGE_FOUND',
                                            'Q_NAMES_CATEGORY',
                                            'C_OPEN_PAGE_NOTE',
                                            'Q_SYNONYMS',
                                            'Q_PROMPT',
                                            'Q_AGENT_PAGES',
                                            'Q_RETRY_SOURCES',
                                            'Q_AGENT_FRAGMENTS',
                                            'Q_MODEL_REFUSED',
                                            'Q_ADVISORY_TABLE',
                                            'Q_OFFER_KEYS',
                                            'Q_CITE_TOOLS',
                                            'Q_CITE_PAGES',
                                            'Q_SOURCE_WARNING',
                                            'C_FIGURES',
                                            'Q_SHOWN'],
                                           2,
                                           'A medianoche volvían a ser lagartos.\n\n> ⚠️ Esta respuesta pr'),
 'pages_refused_twice': (['C_COUNT_HISTORY',
                          'C_WHICH_MODE',
                          'C_MARK_REFUSED',
                          'C_DROP_TABLES',
                          'C_TRIM_TURNS',
                          'C_ORIGIN_LINES',
                          'Q_QUESTION',
                          'Q_NORMALIZE',
                          'Q_IN_BLACKLIST',
                          'Q_STEM',
                          'Q_NAMES_DATA',
                          'Q_IN_ROSTER',
                          'Q_PAGE_OPEN',
                          'Q_SEARCH_WIKI',
                          'Q_COLLECTION_PAGES',
                          'Q_COLLECTION_OPEN_PAGE',
                          'Q_PAGE_FOUND',
                          'Q_NAMES_CATEGORY',
                          'C_OPEN_PAGE_NOTE',
                          'Q_SYNONYMS',
                          'Q_PROMPT',
                          'Q_AGENT_PAGES',
                          'Q_RETRY_SOURCES',
                          'Q_AGENT_FRAGMENTS',
                          'Q_MODEL_REFUSED',
                          'R_MODEL'],
                         2,
                         'No encontré la respuesta en el texto que recibí de la wiki. ')}


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_middle_block_keeps_its_path(name, monkeypatch):
    nodes, calls, answer = _run(monkeypatch, **CASES[name])
    expected_nodes, expected_calls, answer_start = EXPECTED[name]
    assert nodes == expected_nodes
    assert calls == expected_calls
    assert answer.startswith(answer_start)
