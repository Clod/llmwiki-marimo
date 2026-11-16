"""Tests for domain/chat/workflow/input_checks.py — the input workflow of the
pre-retrieval mode, run on its own, with no agent and no database.

Each refusal that ends the workflow (R_BLACKLIST at Q_IN_BLACKLIST, R_BLACKLIST at Q_RW_IN_BLACKLIST on the rewrite, R_NOT_IN_ROSTER,
R_NOTHING_FOUND) has a test, and so has each node that calls the agent (Q_AGENT_PAGES, Q_AGENT_COLLECTION, Q_AGENT_TOOLS or Q_AGENT_FRAGMENTS). The
retrieval and the vocabulary are patched on the module `preretrieval`, the
module through which the steps call them.
"""

import asyncio
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from domain import tracing
from domain.chat import preretrieval
from domain.chat.preretrieval import PreRetrievalTurn
from domain.chat.workflow import input_checks
from domain.chat.workflow.input_checks import AgentCall, Refused
from domain.datasets.models import DatasetRow
from tests.helpers.workflow_path import steps

CFG = SimpleNamespace(blacklist=["cedear", "cedears"], data_aliases={"dolar": ["billete verde"]})


# The nodes of the history, first in every path of the input workflow.
HISTORY = ["C_COUNT_HISTORY", "C_WHICH_MODE", "C_MARK_REFUSED", "C_DROP_TABLES", "C_TRIM_TURNS", "C_ORIGIN_LINES"]
# The nodes that build the prompt, last in every path that reaches the agent.
PROMPT = ["C_OPEN_PAGE_NOTE", "Q_SYNONYMS", "Q_PROMPT"]


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
        return out, steps(path)
    return asyncio.run(go())


def test_graph_has_no_instrumentation():
    assert input_checks.graph.auto_instrument is False


def test_blacklist_ends_after_q3(patched):
    out, path = _run(_turn("¿qué son los cedears?"))
    assert isinstance(out, Refused)
    assert out.text.startswith("No puedo responder esta pregunta: menciona «cedears»")
    assert path == HISTORY + ["Q_QUESTION", "Q_NORMALIZE", "Q_IN_BLACKLIST", "R_BLACKLIST"]


def test_each_decision_records_the_branch_it_chose(patched):
    """A decision records a node after the step it follows, with the label of the
    branch it chose; the audit page shows it as a node of the path."""
    async def go():
        with tracing.collect() as path:
            await input_checks.graph.run(state=turn, inputs=turn)
        return path
    turn = _turn("¿qué son los cedears?")
    path = asyncio.run(go())
    nodes = [entry["node"] for entry in path]
    assert nodes[-3:] == ["Q_IN_BLACKLIST", "after_Q_IN_BLACKLIST", "R_BLACKLIST"]
    assert path[-2]["attributes"] == {"branch": "names a blacklisted term"}


def test_blacklisted_rewrite_ends_in_r1(patched):
    """A rewrite that names a blacklisted term is refused at Q_RW_IN_BLACKLIST. The refusal
    names the term that the rewrite matched."""
    async def condense(question, history):
        return "¿Conviene comprar CEDEARs?"

    out, path = _run(_turn("¿y eso conviene?", history=["previous turn"], condense=condense))
    assert isinstance(out, Refused)
    assert out.text == (
        "No puedo responder esta pregunta: menciona «cedears», que está en la lista de temas"
        " sobre los que esta wiki no tiene permitido responder."
    )
    assert path == HISTORY + ["Q_QUESTION", "Q_NORMALIZE", "Q_IN_BLACKLIST", "Q_STEM", "Q_NAMES_DATA", "Q_IN_ROSTER", "Q_PAGE_OPEN", "Q_REWRITE", "Q_RW_ACCEPTED", "Q_RW_IN_BLACKLIST", "R_BLACKLIST"]


def test_not_in_roster_ends_in_r2a(patched):
    out, path = _run(_turn("¿va a llover mañana en Rosario?"))
    assert isinstance(out, Refused)
    assert out.text.startswith("No recibí de la wiki información relacionada")
    assert path == HISTORY + ["Q_QUESTION", "Q_NORMALIZE", "Q_IN_BLACKLIST", "Q_STEM", "Q_NAMES_DATA", "Q_IN_ROSTER", "Q_PAGE_OPEN", "Q_COLLECTION_PAGES", "Q_COLLECTION_OPEN_PAGE", "Q_IS_COLLECTION", "Q_IS_DATA", "Q_FRAGMENT_FOUND", "Q_ROSTER_REFUSAL", "R_NOT_IN_ROSTER"]


def test_in_roster_nothing_found_ends_in_r2b(patched):
    patched["concepts"] = ["caución bursátil"]
    out, path = _run(_turn("¿qué es una caución bursátil?"))
    assert isinstance(out, Refused)
    assert path == HISTORY + ["Q_QUESTION", "Q_NORMALIZE", "Q_IN_BLACKLIST", "Q_STEM", "Q_NAMES_DATA", "Q_IN_ROSTER", "Q_PAGE_OPEN", "Q_SEARCH_WIKI", "Q_SEARCH_SOURCES", "Q_COLLECTION_PAGES", "Q_COLLECTION_OPEN_PAGE", "Q_PAGE_FOUND", "Q_IS_COLLECTION", "Q_IS_DATA", "Q_FRAGMENT_FOUND", "Q_ROSTER_REFUSAL", "R_NOTHING_FOUND"]


def test_wiki_pages_go_to_q13(patched):
    patched["concepts"] = ["caución bursátil"]
    patched["wiki"] = ["[/wiki/concepts/caucion.md]\nUna caución es..."]
    turn = _turn("¿qué es una caución bursátil?")
    out, path = _run(turn)
    assert out == AgentCall("Q_AGENT_PAGES")
    assert path == HISTORY + ["Q_QUESTION", "Q_NORMALIZE", "Q_IN_BLACKLIST", "Q_STEM", "Q_NAMES_DATA", "Q_IN_ROSTER", "Q_PAGE_OPEN", "Q_SEARCH_WIKI", "Q_COLLECTION_PAGES", "Q_COLLECTION_OPEN_PAGE", "Q_PAGE_FOUND", "Q_NAMES_CATEGORY"] + PROMPT
    assert turn.plan.tier == "curado"


class _TwoCategories:
    """A dataset source with two categories: `plazo_fijo`, keyed by bank, and `dolar`."""

    ROWS = {
        "plazo_fijo": [
            DatasetRow("plazo_fijo", "Banco Galicia", "tna", 30.0, "%", date(2026, 1, 2), "bcra.gob.ar"),
            DatasetRow("plazo_fijo", "Banco Nación", "tna", 29.0, "%", date(2026, 1, 2), "bcra.gob.ar"),
        ],
        "dolar": [
            DatasetRow("dolar", "MEP", "venta", 1200.0, "ARS", date(2026, 1, 2), "ambito.com"),
        ],
    }

    def categories(self):
        return list(self.ROWS)

    def query(self, categoria):
        return list(self.ROWS[categoria])

    def attributes(self, categoria):
        return {"clave": "entidad"}


def test_two_named_categories_loop_over_q7b(patched):
    """A question that names a key of `plazo_fijo` and the category `dolar`:
    Q_NAMES_KEY runs once per category, Q_KEY_ROWS injects the rows, Q_KEY_NAMES the key names."""
    page = "[/wiki/concepts/plazo-fijo.md]\nUn plazo fijo es..."
    patched["concepts"] = ["plazo fijo"]
    patched["wiki"] = [page]
    question = "¿Qué tasa de plazo fijo da el Banco Galicia y a cuánto está el dólar?"
    source = _TwoCategories()
    turn = PreRetrievalTurn(question, CFG, "db", Path("/tmp/wp"), [], "es", stem_language="es", source=source)
    out, path = _run(turn)
    assert out == AgentCall("Q_AGENT_PAGES")
    assert path[path.index("Q_NAMES_CATEGORY"):] == ["Q_NAMES_CATEGORY", "Q_NAMES_KEY", "Q_KEY_ROWS", "Q_NAMES_KEY", "Q_KEY_NAMES"] + PROMPT
    injection = preretrieval.dataset_injection(question, source, CFG.data_aliases, "es")
    assert turn.plan.context == "\n\n".join([page, *injection.blocks])
    assert turn.injection == injection and len(injection.blocks) == 2


def test_collection_question_goes_to_q14(patched):
    patched["collection"] = ["[wiki/overview.md]\nThe wiki has..."]
    out, path = _run(_turn("¿qué temas tiene esta wiki?"))
    assert out == AgentCall("Q_AGENT_COLLECTION")
    assert path[-4:] == ["Q_IS_COLLECTION"] + PROMPT


def test_data_question_goes_to_q15(patched):
    patched["vocab"] = {"dolar"}                  # «billete verde» is an alias of a data term
    out, path = _run(_turn("¿a cuánto está el billete verde?"))
    assert out == AgentCall("Q_AGENT_TOOLS")
    assert path[-5:] == ["Q_IS_COLLECTION", "Q_IS_DATA"] + PROMPT


def test_a_data_question_with_a_collection_expression_goes_to_the_data_tools(patched):
    """«cada» is a collection expression, and the overview was read; the question names
    data, so it goes to Q_IS_DATA and the data tools, not to the overview and the index."""
    patched["collection"] = ["[wiki/overview.md]\nThe wiki has..."]
    patched["vocab"] = {"dolar"}
    turn = _turn("¿cuánto vale el billete verde en cada banco?")
    out, path = _run(turn)
    assert out == AgentCall("Q_AGENT_TOOLS")
    assert path[-5:] == ["Q_IS_COLLECTION", "Q_IS_DATA"] + PROMPT
    assert turn.plan.tier is None


def test_source_fragments_go_to_q16(patched):
    patched["concepts"] = ["caución bursátil"]
    patched["docs"] = ["[12 Cauciones.docx]\nLa caución bursátil es un préstamo."]
    turn = _turn("¿qué es una caución bursátil?")
    out, path = _run(turn)
    assert out == AgentCall("Q_AGENT_FRAGMENTS")
    assert path[-4:] == ["Q_FRAGMENT_FOUND"] + PROMPT
    assert turn.plan.verify is True


def test_a_page_alias_with_no_page_goes_to_the_fragments_of_the_name_it_stands_for(patched, monkeypatch):
    """«montero» is an alias of the page «Cazador», not of a data term: the question names
    no data, and the source search also searches «Cazador». It went to Q_AGENT_TOOLS."""
    queries = []

    def chunks(db, query, **_kwargs):
        queries.append(query)
        return ["[Blancanieves.pdf]\nEl cazador llevó a Blancanieves al bosque."]

    monkeypatch.setattr(preretrieval, "retrieve_source_chunks", chunks)
    turn = _turn("¿qué hizo el montero?")
    turn.config = SimpleNamespace(blacklist=[], data_aliases={"Cazador": ["montero"], "dolar": ["billete verde"]})
    patched["vocab"] = {"dolar"}
    out, path = _run(turn)
    assert turn.has_data is False and out == AgentCall("Q_AGENT_FRAGMENTS")
    assert path[-4:] == ["Q_FRAGMENT_FOUND"] + PROMPT
    assert queries == ["¿qué hizo el montero? Cazador"]


def test_collection_intent_is_read_through_preretrieval(patched):
    """The workshop replaces the predicate of Q_IS_COLLECTION on the module `preretrieval`;
    the input workflow reads it there, so the path changes."""
    patched["collection"] = ["[wiki/overview.md]\nThe wiki has..."]
    question = "¿va a llover mañana en Rosario?"
    out, path = _run(_turn(question))
    assert isinstance(out, Refused) and path[-1] == "R_NOT_IN_ROSTER"
    with mock.patch.object(preretrieval, "collection_intent", lambda q, cues, language: True):
        out, path = _run(_turn(question))
    assert out == AgentCall("Q_AGENT_COLLECTION")
    assert path[-4:] == ["Q_IS_COLLECTION"] + PROMPT


def test_render_names_the_nodes():
    diagram = input_checks.graph.render()
    for node in ("Q_QUESTION", "Q_IN_BLACKLIST", "Q_CONDENSED", "Q_SEARCH_WIKI", "Q_SEARCH_SOURCES", "R_BLACKLIST", "R_NOT_IN_ROSTER", "R_NOTHING_FOUND", "agent"):
        assert node in diagram


def test_workshop_patch_of_collection_intent_changes_the_path(patched):
    """As `workshop/apoyo.py` does: `pre_retrieval_answer` with a stand-in
    `run_agent`, and the predicate of Q_IS_COLLECTION replaced on the module `preretrieval`."""
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
            return out, steps(path)
        return asyncio.run(go())

    _, before = answer()
    with mock.patch.object(preretrieval, "collection_intent", lambda q, cues, language: False):
        _, kept = answer()
    assert before == kept and before[-1] == "R_NOT_IN_ROSTER" and calls == []
    with mock.patch.object(preretrieval, "collection_intent", lambda q, cues, language: True):
        out, after = answer()
    assert len(calls) == 1
    assert after == HISTORY + ["Q_QUESTION", "Q_NORMALIZE", "Q_IN_BLACKLIST", "Q_STEM", "Q_NAMES_DATA", "Q_IN_ROSTER", "Q_PAGE_OPEN", "Q_COLLECTION_PAGES", "Q_COLLECTION_OPEN_PAGE", "Q_IS_COLLECTION"] + PROMPT + ["Q_AGENT_COLLECTION", "Q_MODEL_REFUSED", "Q_ADVISORY_TABLE", "Q_OFFER_KEYS", "Q_CITE_TOOLS", "Q_CITE_PAGES", "C_FIGURES", "Q_SHOWN"]
    assert out.startswith("Esta wiki trata de finanzas.")


def test_agent_input_builds_the_history_of_model_history():
    """The nodes C_MARK_REFUSED, C_DROP_TABLES, C_TRIM_TURNS and C_ORIGIN_LINES build the history that `model_history` builds: the
    refusal replaced by a note, the origin line on the answer that read data."""
    from domain.chat.history import model_history
    from domain.chat.refusal import fixed_sentence
    from domain.verify.models import ContextPassage

    def msg(role, content, context=()):
        return SimpleNamespace(role=role, content=content, context=context)

    prior = [
        msg("user", "¿qué son los cedears?"), msg("assistant", fixed_sentence("es") + " Nada más."),
        msg("user", "¿qué tasa da el Banco Galicia?"),
        msg("assistant", "30 %.", (ContextPassage("datasets/plazo_fijo.md", "| clave |"),)),
    ]
    turn = input_checks.AgentTurn("¿y el Nación?", prior, "es", config=CFG)

    async def go():
        with tracing.collect() as path:
            await input_checks.agent_graph.run(state=turn, inputs=turn)
        return path
    path = asyncio.run(go())

    assert steps(path) == HISTORY + ["Q_IN_BLACKLIST", "C_OPEN_PAGE_NOTE"]
    assert [m.parts[0].content for m in turn.history] == [m.parts[0].content for m in model_history(prior, "es")]
    by_node = {entry["node"]: entry["attributes"] for entry in path}
    assert by_node["C_MARK_REFUSED"]["refused_marked"] == 1
    assert by_node["C_ORIGIN_LINES"] == {"origin_lines": 1, "history_messages": 4}
    assert by_node["C_WHICH_MODE"]["mode"] == "agent"


def test_the_rewrite_of_a_follow_up_sees_the_question_that_got_a_refusal(patched):
    """After a refusal, the condenser still receives the refused question, so «¿Y la
    carroza?» can be rewritten with its topic. The refusal is replaced by a note."""
    from domain.chat.refusal import fixed_sentence

    seen = []

    async def condense(question, history):
        seen.extend(m.parts[0].content for m in history)
        return "¿Cómo era la carroza de Cenicienta?"

    prior = [SimpleNamespace(role="user", content="¿Cómo era la carroza de Cenicienta?", context=()),
             SimpleNamespace(role="assistant", content=fixed_sentence("es") + " Nada más.", context=())]
    turn = PreRetrievalTurn(
        "¿Y la carroza?", CFG, "db", Path("/tmp/wp"), [], "es",
        stem_language="es", source=_NoDatasets(), condense=condense, prior=prior,
    )
    _run(turn)
    assert seen == ["¿Cómo era la carroza de Cenicienta?", "[pregunta anterior sin respuesta]"]


def _run_agent(turn):
    async def go():
        with tracing.collect() as path:
            out = await input_checks.agent_graph.run(state=turn, inputs=turn)
        return out, path
    return asyncio.run(go())


def test_the_agent_mode_refuses_a_blacklisted_question_before_the_model():
    """The agent input workflow runs the nodes Q_IN_BLACKLIST and R_BLACKLIST of the
    pre-retrieval mode: a blacklisted question ends in the refusal, with no prompt."""
    turn = input_checks.AgentTurn("¿qué son los cedears?", [], "es", config=CFG)
    out, path = _run_agent(turn)
    assert isinstance(out, Refused)
    assert out.text.startswith("No puedo responder esta pregunta: menciona «cedears»")
    assert [e["node"] for e in path][-3:] == ["Q_IN_BLACKLIST", "after_Q_IN_BLACKLIST", "R_BLACKLIST"]
    assert turn.asked == ""


def test_the_agent_mode_passes_a_question_with_no_blacklisted_term():
    turn = input_checks.AgentTurn("¿qué es un plazo fijo?", [], "es", config=CFG)
    out, path = _run_agent(turn)
    assert out is turn and turn.asked == "¿qué es un plazo fijo?"
    assert steps(path)[-2:] == ["Q_IN_BLACKLIST", "C_OPEN_PAGE_NOTE"]
