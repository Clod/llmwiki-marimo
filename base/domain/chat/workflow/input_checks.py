"""The input workflows: the history and the checks of the question.

`graph`, the input workflow of the pre-retrieval mode, runs from node C1 to the
node that calls the agent (Q13, Q14, Q15 or Q16), or to a refusal (R1, R2a,
R2b). It does not call the agent: the graph ends in an `AgentCall`, and
`_pre_retrieval_turn` (`domain/chat/preretrieval.py`) calls the agent. Its
state is the `PreRetrievalTurn` of the turn.

`strict_graph`, the input workflow of the strict mode, runs the nodes C1 to C2d
and C4: it builds the history and the question that the model receives, and
checks nothing in the question. Its state is a `StrictTurn`. `chat_turn_with_context`
(`services/chat.py`) runs it before node T1.

Each step returns the state, so that each decision reads the field that its
condition names. The steps of the nodes are functions of the catalog
(`catalog.py`), registered here with `register`. The catalog says how the steps
call `preretrieval`.

`PRE_RETRIEVAL_NODES` and `STRICT_NODES` list the catalog nodes of each graph,
and `OWN_STEPS` the steps of this module that are not in the catalog (Q13 to
Q16).

Both graphs are built with `auto_instrument=False`: the steps record the same
`tracing.mark` and `tracing.node` calls as before the graphs, and the graphs add
no span.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_graph import GraphBuilder, StepContext

from domain.chat import preretrieval
from domain.chat.preretrieval import PreRetrievalTurn
# `Refused` and `AgentCall` are the outputs of the graph; `_pre_retrieval_turn`
# and the tests import them from this module.
from domain.chat.workflow.catalog import N_, AgentCall, Refused, register

Ctx = StepContext[PreRetrievalTurn, None, PreRetrievalTurn]


@dataclass
class StrictTurn:
    """The state of the input workflow of the strict mode.

    prior:   the prior messages of the chat (`.role`, `.content`).
    kept:    the history between C2a and C2d, as chat messages.
    history: the history that the model receives (C2d).
    asked:   the question that the model receives, with the open-page note (C4).
    """

    question: str
    prior: list | None
    language: str | None
    open_page: str | None = None
    mode: str = "strict"
    kept: list = field(default_factory=list)
    history: list = field(default_factory=list)
    asked: str = ""

    @property
    def noted_page(self) -> str | None:
        """The page that the note of C4 names: the open page, whether or not the
        page exists."""
        return self.open_page


g = GraphBuilder(
    name="input_checks", state_type=PreRetrievalTurn, input_type=PreRetrievalTurn,
    output_type=Refused | AgentCall, auto_instrument=False,
)


# ── The conditions of the decisions ───────────────────────────────────────────

def _off_limits(turn: PreRetrievalTurn) -> bool:
    return turn.off_limits


def _condenses(turn: PreRetrievalTurn) -> bool:
    """The condensation (Q5c to Q5b) runs only with a condenser and a history,
    for a question that is not in the roster, names no data and has no open
    page."""
    return bool(turn.condense is not None and turn.history
                and not (turn.in_roster or turn.has_data or turn.open_block))


def _rewritten(turn: PreRetrievalTurn) -> bool:
    """Q5d accepted a rewrite that differs from the question."""
    return turn.search != turn.question


def _in_roster(turn: PreRetrievalTurn) -> bool:
    return turn.in_roster


def _no_wiki_hits(turn: PreRetrievalTurn) -> bool:
    return not turn.wiki_hits


def _has_wiki_hits(turn: PreRetrievalTurn) -> bool:
    return bool(turn.wiki_hits)


def _categories_left(turn: PreRetrievalTurn) -> bool:
    """A category that the question names is not yet read (Q7b to Q7d)."""
    return bool(turn.named)


def _keys_named(turn: PreRetrievalTurn) -> bool:
    """The question names keys of the category in course (Q7b)."""
    return bool(turn.category[2])


def _has_collection_hits(turn: PreRetrievalTurn) -> bool:
    return bool(turn.collection_hits)


def _has_data(turn: PreRetrievalTurn) -> bool:
    return turn.has_data


def _fragment_in_roster(turn: PreRetrievalTurn) -> bool:
    return bool(turn.doc_hits and turn.in_roster)


def _calls(node: str):
    """The condition of the branch of the decision `agent` that goes to `node`."""
    return lambda turn: preretrieval._answer_node(turn.plan, turn.wiki_hits, turn.in_roster) == node


# ── The steps ─────────────────────────────────────────────────────────────────

# The nodes of the catalog that each input workflow registers. `OWN_STEPS` are
# the steps of this module that are not in the catalog. The test
# `tests/unit/test_workflow_nodes_doc.py` compares each list with its graph, and
# `docs/workflow_nodes.md` is generated from the lists (`table.py`).
HISTORY_NODES = ("C1", "C2", "C2a", "C2b", "C2c", "C2d")
PRE_RETRIEVAL_NODES = (
    *HISTORY_NODES, "Q1", "Q2", "Q3", "Q4", "Q4a", "Q5", "Q5a", "Q5c", "Q5d", "Q5e", "Q5f", "Q5g", "Q5b",
    "Q6", "Q8", "Q8a", "Q8b", "Q7", "Q7a", "Q7b", "Q7c", "Q7d", "Q9", "Q10", "Q11", "Q12", "C4", "Q12a",
    "Q12b", "R1", "R2a", "R2b",
)
STRICT_NODES = (*HISTORY_NODES, "C4")
AGENT_NODES = ("Q13", "Q14", "Q15", "Q16")
OWN_STEPS = AGENT_NODES

history_steps = [register(g, code) for code in HISTORY_NODES]
question = register(g, "Q1")
normalize = register(g, "Q2")
blacklist = register(g, "Q3")
stem = register(g, "Q4")
data_detection = register(g, "Q4a")
roster = register(g, "Q5")
open_page = register(g, "Q5a")
rewrite = register(g, "Q5c")
accept_rewrite = register(g, "Q5d")
blacklist_rewrite = register(g, "Q5e")
data_detection_rewrite = register(g, "Q5f")
roster_rewrite = register(g, "Q5g")
condense = register(g, "Q5b")
wiki_search = register(g, "Q6")
source_search = register(g, "Q8")
collection_pages = register(g, "Q8a")
collection_with_open_page = register(g, "Q8b")
wiki_pages = register(g, "Q7")
dataset_categories = register(g, "Q7a")
dataset_keys = register(g, "Q7b")
dataset_rows = register(g, "Q7c")
dataset_key_names = register(g, "Q7d")
collection = register(g, "Q9")
data_question = register(g, "Q10")
source_fragments = register(g, "Q11")
roster_refusal = register(g, "Q12")
open_page_note = register(g, "C4")
synonyms = register(g, "Q12a")
prompt = register(g, "Q12b")
refuse_blacklist = register(g, "R1")
refuse_not_in_roster = register(g, "R2a")
refuse_nothing_found = register(g, "R2b")


def _agent_call(node: str):
    """The final step `node` (Q13 to Q16): the turn goes on to the agent.
    `_pre_retrieval_turn` records the node when it calls the agent."""
    async def call(ctx: Ctx) -> AgentCall:
        return AgentCall(node)
    return g.step(call, node_id=node)


agent_calls = {node: _agent_call(node) for node in AGENT_NODES}

# The label of the branch of the decision `agent` that goes to each node of
# `AGENT_NODES`, in the same order.
AGENT_LABELS = (
    N_("wiki pages injected"),
    N_("overview and index injected"),
    N_("nothing injected"),
    N_("fragments of the source documents injected"),
)


# ── The edges ─────────────────────────────────────────────────────────────────

# Each decision carries its question (`note`) and each branch its label, both in
# English and marked with `N_`. The audit page draws them in the interface
# language (`web/audit_diagram.py`).

# The decision that chooses the node that calls the agent. Every branch that
# ends the checks with a plan goes to C4, Q12a and Q12b, which build the prompt,
# and then here.
agent = g.decision(node_id="agent", note=N_("What text was injected into the prompt?"))
for _node, _label in zip(AGENT_NODES, AGENT_LABELS):
    agent = agent.branch(g.match(PreRetrievalTurn, matches=_calls(_node)).label(_label).to(agent_calls[_node]))

g.add(
    g.edge_from(g.start_node).to(history_steps[0]),
    *(g.edge_from(a).to(b) for a, b in zip(history_steps, history_steps[1:])),
    g.edge_from(history_steps[-1]).to(question),
    g.edge_from(question).to(normalize),
    g.edge_from(normalize).to(blacklist),
    g.edge_from(blacklist).to(
        g.decision(node_id="after_Q3", note=N_("Does the question name a blacklisted term?"))
        .branch(g.match(PreRetrievalTurn, matches=_off_limits).label(N_("names a blacklisted term")).to(refuse_blacklist))
        .branch(g.match(PreRetrievalTurn).label(N_("names no blacklisted term")).to(stem))
    ),
    g.edge_from(stem).to(data_detection),
    g.edge_from(data_detection).to(roster),
    g.edge_from(roster).to(open_page),
    g.edge_from(open_page).to(
        g.decision(node_id="after_Q5a", note=N_("Is the question a follow-up to rewrite, does it name a wiki topic, or is a page open?"))
        .branch(g.match(PreRetrievalTurn, matches=_condenses).label(N_("follow-up with no wiki topic, no data and no open page")).to(rewrite))
        .branch(g.match(PreRetrievalTurn, matches=_in_roster)
                .label(N_("names a wiki topic or a page is open")).to(wiki_search))
        .branch(g.match(PreRetrievalTurn).label(N_("names no wiki topic and no page is open")).to(collection_pages))
    ),
    g.edge_from(rewrite).to(accept_rewrite),
    g.edge_from(accept_rewrite).to(
        g.decision(node_id="after_Q5d", note=N_("Was the rewrite accepted?"))
        .branch(g.match(PreRetrievalTurn, matches=_rewritten).label(N_("rewrite accepted")).to(blacklist_rewrite))
        .branch(g.match(PreRetrievalTurn).label(N_("rewrite not accepted")).to(condense))
    ),
    g.edge_from(blacklist_rewrite).to(
        g.decision(node_id="after_Q5e", note=N_("Does the rewrite name a blacklisted term?"))
        .branch(g.match(PreRetrievalTurn, matches=_off_limits).label(N_("rewrite names a blacklisted term")).to(refuse_blacklist))
        .branch(g.match(PreRetrievalTurn).label(N_("rewrite names no blacklisted term")).to(data_detection_rewrite))
    ),
    g.edge_from(data_detection_rewrite).to(roster_rewrite),
    g.edge_from(roster_rewrite).to(condense),
    g.edge_from(condense).to(
        g.decision(node_id="after_Q5b", note=N_("Does the examined text name a wiki topic?"))
        .branch(g.match(PreRetrievalTurn, matches=_in_roster)
                .label(N_("the examined text names a wiki topic")).to(wiki_search))
        .branch(g.match(PreRetrievalTurn)
                .label(N_("the examined text names no wiki topic")).to(collection_pages))
    ),
    g.edge_from(wiki_search).to(
        g.decision(node_id="after_Q6", note=N_("Did the wiki search find a page?"))
        .branch(g.match(PreRetrievalTurn, matches=_no_wiki_hits).label(N_("found no wiki page")).to(source_search))
        .branch(g.match(PreRetrievalTurn).label(N_("found a wiki page")).to(collection_pages))
    ),
    g.edge_from(source_search).to(collection_pages),
    g.edge_from(collection_pages).to(collection_with_open_page),
    g.edge_from(collection_with_open_page).to(
        g.decision(node_id="after_Q8b", note=N_("Does the question name a wiki topic, or is a page open?"))
        .branch(g.match(PreRetrievalTurn, matches=_in_roster)
                .label(N_("names a wiki topic or a page is open")).to(wiki_pages))
        .branch(g.match(PreRetrievalTurn).label(N_("names no wiki topic and no page is open")).to(collection))
    ),
    g.edge_from(wiki_pages).to(
        g.decision(node_id="after_Q7", note=N_("Did the wiki search find a page?"))
        .branch(g.match(PreRetrievalTurn, matches=_has_wiki_hits).label(N_("found a wiki page")).to(dataset_categories))
        .branch(g.match(PreRetrievalTurn).label(N_("found no wiki page")).to(collection))
    ),
    g.edge_from(collection).to(
        g.decision(node_id="after_Q9", note=N_("Was the overview or the index read?"))
        .branch(g.match(PreRetrievalTurn, matches=_has_collection_hits).label(N_("overview or index read")).to(open_page_note))
        .branch(g.match(PreRetrievalTurn).label(N_("no overview or index read")).to(data_question))
    ),
    g.edge_from(data_question).to(
        g.decision(node_id="after_Q10", note=N_("Does the question name data?"))
        .branch(g.match(PreRetrievalTurn, matches=_has_data).label(N_("names data")).to(open_page_note))
        .branch(g.match(PreRetrievalTurn).label(N_("names no data")).to(source_fragments))
    ),
    g.edge_from(source_fragments).to(
        g.decision(node_id="after_Q11", note=N_("Did the source search find a fragment, and does the question name a wiki topic?"))
        .branch(g.match(PreRetrievalTurn, matches=_fragment_in_roster).label(N_("found a source fragment and names a wiki topic")).to(open_page_note))
        .branch(g.match(PreRetrievalTurn)
                .label(N_("found no source fragment or names no wiki topic")).to(roster_refusal))
    ),
    g.edge_from(roster_refusal).to(
        g.decision(node_id="after_Q12", note=N_("Does the question name a wiki topic, or is a page open?"))
        .branch(g.match(PreRetrievalTurn, matches=_in_roster)
                .label(N_("names a wiki topic or a page is open")).to(refuse_nothing_found))
        .branch(g.match(PreRetrievalTurn)
                .label(N_("names no wiki topic and no page is open")).to(refuse_not_in_roster))
    ),
    g.edge_from(dataset_categories).to(
        g.decision(node_id="after_Q7a", note=N_("Does the question name a dataset category?"))
        .branch(g.match(PreRetrievalTurn, matches=_categories_left).label(N_("names a dataset category")).to(dataset_keys))
        .branch(g.match(PreRetrievalTurn).label(N_("names no dataset category")).to(open_page_note))
    ),
    g.edge_from(dataset_keys).to(
        g.decision(node_id="after_Q7b", note=N_("Does the question name a key of that category?"))
        .branch(g.match(PreRetrievalTurn, matches=_keys_named).label(N_("names a key of the category")).to(dataset_rows))
        .branch(g.match(PreRetrievalTurn).label(N_("names no key of the category")).to(dataset_key_names))
    ),
    g.edge_from(dataset_rows, dataset_key_names).to(
        g.decision(node_id="after_Q7cd", note=N_("Is a named category left?"))
        .branch(g.match(PreRetrievalTurn, matches=_categories_left).label(N_("a named category is left")).to(dataset_keys))
        .branch(g.match(PreRetrievalTurn).label(N_("no named category is left")).to(open_page_note))
    ),
    g.edge_from(open_page_note).to(synonyms),
    g.edge_from(synonyms).to(prompt),
    g.edge_from(prompt).to(agent),
    g.edge_from(*agent_calls.values(), refuse_blacklist, refuse_not_in_roster, refuse_nothing_found)
    .to(g.end_node),
)

graph = g.build()


# ── The input workflow of the strict mode ─────────────────────────────────────

sg = GraphBuilder(
    name="strict_input_checks", state_type=StrictTurn, input_type=StrictTurn,
    output_type=StrictTurn, auto_instrument=False,
)
strict_steps = [register(sg, code) for code in STRICT_NODES]
sg.add(
    sg.edge_from(sg.start_node).to(strict_steps[0]),
    *(sg.edge_from(a).to(b) for a, b in zip(strict_steps, strict_steps[1:])),
    sg.edge_from(strict_steps[-1]).to(sg.end_node),
)

strict_graph = sg.build()
