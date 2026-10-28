"""The input workflow: the checks of the question in the pre-retrieval mode.

The graph runs from node Q1 to the node that calls the agent (Q13, Q14, Q15 or
Q16), or to a refusal (R1, R2a, R2b). It does not call the agent: the graph ends
in an `AgentCall`, and `_pre_retrieval_turn` (`domain/chat/preretrieval.py`)
calls the agent. Nodes C1 and C2 run before the graph, in
`chat_turn_with_context` (`services/chat.py`).

The state of the graph is the `PreRetrievalTurn` of the turn. Each step returns
the same object, so that each decision reads the field that its condition names.
The steps call through the module `preretrieval` the functions that it defines,
and `collection_intent`, so that a patch of a name of that module
(`mock.patch.object(preretrieval, "collection_intent", ...)`, in the workshop)
reaches the step. `stem_text` and `off_limits_term` come from their own modules.

The graph is built with `auto_instrument=False`: the steps record the same
`tracing.mark` and `tracing.node` calls as before the graph, and the graph adds
no span.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from pydantic_graph import GraphBuilder, StepContext

from domain import tracing
from domain.chat import condense as condensation
from domain.chat import preretrieval
from domain.chat.preretrieval import PreRetrievalTurn
from domain.chat.refusal import Refusal, RefusalCause
from domain.chat.scope import off_limits_term
from domain.text.stemming import stem_text

R = tracing.READING


@dataclass(frozen=True)
class Refused:
    """The turn ends in a refusal before the agent; `text` is the answer of the turn."""

    text: str


@dataclass(frozen=True)
class AgentCall:
    """The turn goes on to the agent; `node` is the node that calls it (Q13 to Q16)."""

    node: str


Ctx = StepContext[PreRetrievalTurn, None, PreRetrievalTurn]

g = GraphBuilder(
    name="input_checks", state_type=PreRetrievalTurn, input_type=PreRetrievalTurn,
    output_type=Refused | AgentCall, auto_instrument=False,
)


# ── The conditions of the decisions ───────────────────────────────────────────

def _off_limits(turn: PreRetrievalTurn) -> bool:
    return turn.off_limits


def _condenses(turn: PreRetrievalTurn) -> bool:
    """Node Q5b runs only with a condenser and a history, for a question that
    is not in the roster, names no data and has no open page."""
    return bool(turn.condense is not None and turn.history
                and not (turn.in_roster or turn.has_data or turn.open_block))


def _in_roster(turn: PreRetrievalTurn) -> bool:
    return turn.in_roster


def _no_wiki_hits(turn: PreRetrievalTurn) -> bool:
    return not turn.wiki_hits


def _refused(turn: PreRetrievalTurn) -> bool:
    return turn.plan.action == "refuse"


def _injects_dataset(turn: PreRetrievalTurn) -> bool:
    """The Tier-1 answer from wiki pages (Q13) adds the dataset the question names."""
    return preretrieval._answer_node(turn.plan, turn.wiki_hits, turn.in_roster) == "Q13"


# ── The steps ─────────────────────────────────────────────────────────────────

@g.step(node_id="Q1")
async def question(ctx: Ctx) -> PreRetrievalTurn:
    tracing.mark("Q1", R, question=ctx.state.question)
    return ctx.state


@g.step(node_id="Q2")
async def normalize(ctx: Ctx) -> PreRetrievalTurn:
    tracing.mark("Q2", R, normalized=preretrieval._normalize(ctx.state.question))
    return ctx.state


@g.step(node_id="Q3")
async def blacklist(ctx: Ctx) -> PreRetrievalTurn:
    """The blacklist first (design_stemming.md, decision 14): a blacklisted
    question is refused before the datasets are read or the index searched."""
    turn = ctx.state
    preretrieval.blacklist(turn, turn.question)
    tracing.mark("Q3", R, answer=turn.off_limits)
    return turn


@g.step(node_id="Q4")
async def stem(ctx: Ctx) -> PreRetrievalTurn:
    turn = ctx.state
    tracing.mark("Q4", R, stemmed=stem_text(turn.question, turn.stem_language))
    return turn


@g.step(node_id="Q5")
async def roster(ctx: Ctx) -> PreRetrievalTurn:
    turn = ctx.state
    preretrieval.build_roster(turn)
    preretrieval.match_roster(turn, turn.question)
    tracing.mark("Q5", R, answer=turn.in_roster,
                 roster_size=len(turn.roster_terms) + len(turn.keys),
                 aliases=len(turn.aliases), matched=turn.matched)
    return turn


@g.step(node_id="Q5a")
async def open_page(ctx: Ctx) -> PreRetrievalTurn:
    """The page the user is reading: injected first, and it makes the question
    covered — the user asks about what is on the screen."""
    turn = ctx.state
    turn.open_block = preretrieval._open_page_block(turn.workspace, turn.open_page)
    tracing.mark("Q5a", R, answer=turn.open_block is not None, page=turn.open_page)
    if turn.open_block is not None:
        turn.in_roster = True
    return turn


@g.step(node_id="Q5b")
async def condense(ctx: Ctx) -> PreRetrievalTurn:
    """Query condensation: a follow-up that names no topic is rewritten from the
    conversation, and `screen` runs the blacklist, the data detection and the
    roster again on the rewrite, so a follow-up cannot bring in a blacklisted
    subject."""
    turn = ctx.state
    rewritten = condensation.accepted(await turn.condense(turn.question, turn.history), turn.question)
    turn.matched = []
    if rewritten != turn.question:
        turn.search = rewritten
        preretrieval.screen(turn, rewritten)
    tracing.mark("Q5b", R, rewritten=rewritten, answer=turn.in_roster,
                 off_limits=bool(turn.off_limits), matched=turn.matched)
    return turn


@g.step(node_id="Q6")
async def wiki_search(ctx: Ctx) -> PreRetrievalTurn:
    """The wiki search runs only for a question in the roster: the plan uses its
    pages only then (decision 14)."""
    turn = ctx.state
    with tracing.node("Q6", R, fts_query=preretrieval._fts_query(turn.search, turn.language)) as q6:
        # Dataset keys (MEP, YPFD, a bank) name rows of a data file, not pages.
        topics = [term for term in turn.matched if term not in turn.keys]
        turn.wiki_hits = preretrieval.retrieve_wiki(turn.db_path, turn.search, language=turn.stem_language,
                                                    topics=topics)
        if turn.open_block is not None:
            open_label = preretrieval._hit_labels([turn.open_block])[0]
            turn.wiki_hits = [turn.open_block] + [
                h for h in turn.wiki_hits if preretrieval._hit_labels([h])[0] != open_label
            ]
        q6.set(pages=preretrieval._hit_labels(turn.wiki_hits))
    return turn


@g.step(node_id="Q8")
async def source_search(ctx: Ctx) -> PreRetrievalTurn:
    turn = ctx.state
    with tracing.node("Q8", R, fts_query=preretrieval._fts_query(turn.search, turn.language)) as q8:
        turn.doc_hits = preretrieval.retrieve_source_chunks(
            turn.db_path, turn.search, language=turn.stem_language,
        )
        q8.set(fragments=preretrieval._hit_labels(turn.doc_hits))
    return turn


@g.step(node_id="collection_pages")
async def collection_pages(ctx: Ctx) -> PreRetrievalTurn:
    """A collection-shaped question ("what tales are in this wiki?") reads the
    overview and the index from disk; node Q9 of `plan_retrieval` decides on them.
    Empty when the wiki has neither page."""
    turn = ctx.state
    turn.collection_hits = (
        preretrieval.retrieve_collection_pages(turn.workspace)
        if preretrieval.collection_intent(turn.search) else []
    )
    return turn


@g.step(node_id="plan_retrieval")
async def plan(ctx: Ctx) -> PreRetrievalTurn:
    """Nodes Q7 and Q9 to Q12, in `plan_retrieval`."""
    turn = ctx.state
    turn.plan = preretrieval.plan_retrieval(
        turn.search, off_limits=turn.config.off_limits,
        wiki_hits=turn.wiki_hits, doc_hits=turn.doc_hits, has_data=turn.has_data,
        in_roster=turn.in_roster, collection_hits=turn.collection_hits,
    )
    return turn


@g.step(node_id="dataset_injection")
async def dataset(ctx: Ctx) -> PreRetrievalTurn:
    """Nodes Q7a to Q7d, in `dataset_injection`. Tier 1 from wiki pages: the code
    adds the dataset the question names (decision 16) — the pages hold no
    values, and the model, told to answer from the context, does not reliably
    call query_dataset on its own."""
    turn = ctx.state
    turn.injection = preretrieval.dataset_injection(
        turn.search, turn.source, turn.config.data_aliases, turn.language,
    )
    if turn.injection.blocks:
        turn.plan = replace(
            turn.plan, context="\n\n".join([turn.plan.context or "", *turn.injection.blocks]),
            blocks=(*turn.plan.blocks, *turn.injection.blocks),
        )
    return turn


@g.step(node_id="agent")
async def agent(ctx: Ctx) -> AgentCall:
    turn = ctx.state
    return AgentCall(preretrieval._answer_node(turn.plan, turn.wiki_hits, turn.in_roster))


@g.step(node_id="R1")
async def refuse_blacklist(ctx: Ctx) -> Refused:
    turn = ctx.state
    return Refused(preretrieval.refuse("R1", turn, Refusal(
        RefusalCause.BLACKLIST, term=off_limits_term(turn.search, turn.config.off_limits),
    )))


@g.step(node_id="R2a")
async def refuse_not_in_roster(ctx: Ctx) -> Refused:
    turn = ctx.state
    return Refused(preretrieval.refuse("R2a", turn, Refusal(
        RefusalCause.NOT_IN_ROSTER, suggestions=preretrieval.suggestions(turn),
    )))


@g.step(node_id="R2b")
async def refuse_nothing_found(ctx: Ctx) -> Refused:
    turn = ctx.state
    return Refused(preretrieval.refuse("R2b", turn, Refusal(
        RefusalCause.NOTHING_FOUND, suggestions=preretrieval.suggestions(turn),
    )))


# ── The edges ─────────────────────────────────────────────────────────────────

g.add(
    g.edge_from(g.start_node).to(question),
    g.edge_from(question).to(normalize),
    g.edge_from(normalize).to(blacklist),
    g.edge_from(blacklist).to(
        g.decision(node_id="after_Q3")
        .branch(g.match(PreRetrievalTurn, matches=_off_limits).label("blacklisted").to(refuse_blacklist))
        .branch(g.match(PreRetrievalTurn).to(stem))
    ),
    g.edge_from(stem).to(roster),
    g.edge_from(roster).to(open_page),
    g.edge_from(open_page).to(
        g.decision(node_id="after_Q5a")
        .branch(g.match(PreRetrievalTurn, matches=_condenses).label("follow-up, not covered").to(condense))
        .branch(g.match(PreRetrievalTurn, matches=_in_roster).label("in the roster").to(wiki_search))
        .branch(g.match(PreRetrievalTurn).to(collection_pages))
    ),
    g.edge_from(condense).to(
        g.decision(node_id="after_Q5b")
        .branch(g.match(PreRetrievalTurn, matches=_in_roster).label("in the roster").to(wiki_search))
        .branch(g.match(PreRetrievalTurn).to(collection_pages))
    ),
    g.edge_from(wiki_search).to(
        g.decision(node_id="after_Q6")
        .branch(g.match(PreRetrievalTurn, matches=_no_wiki_hits).label("no page").to(source_search))
        .branch(g.match(PreRetrievalTurn).to(collection_pages))
    ),
    g.edge_from(source_search).to(collection_pages),
    g.edge_from(collection_pages).to(plan),
    g.edge_from(plan).to(
        g.decision(node_id="after_plan")
        .branch(g.match(PreRetrievalTurn, matches=lambda t: _refused(t) and _off_limits(t))
                .label("refuse, blacklisted").to(refuse_blacklist))
        .branch(g.match(PreRetrievalTurn, matches=lambda t: _refused(t) and _in_roster(t))
                .label("refuse, in the roster").to(refuse_nothing_found))
        .branch(g.match(PreRetrievalTurn, matches=_refused).label("refuse").to(refuse_not_in_roster))
        .branch(g.match(PreRetrievalTurn, matches=_injects_dataset).label("Q13").to(dataset))
        .branch(g.match(PreRetrievalTurn).to(agent))
    ),
    g.edge_from(dataset).to(agent),
    g.edge_from(agent, refuse_blacklist, refuse_not_in_roster, refuse_nothing_found).to(g.end_node),
)

graph = g.build()
