"""The catalog of the nodes of the chat workflows: one step function per node.

Each function receives the `StepContext` of a graph, reads and writes the state
of the turn, records its node with `tracing.mark` or `tracing.node`, and returns
the state. The functions carry no `@g.step` decorator. Each workflow registers
in its own graph the functions that it uses, with `register(g, code)`, so the
`node_id` of a step is always its code in `CATALOG`. A node that two workflows
use is the same function in both.

The steps of the input workflow of the pre-retrieval mode read the
`PreRetrievalTurn` of the turn. The nodes that both input workflows use (C_COUNT_HISTORY to
C_ORIGIN_LINES) read the fields that `QuestionState` names, which `PreRetrievalTurn` and
`AgentTurn` (`input_checks.py`) both have. The steps of the output workflows
read the `AnswerChecks` of the turn, defined here so that the annotations of the
steps resolve in this module.

The steps call through the module `preretrieval` the functions that it defines,
and `collection_intent`, so that a patch of a name of that module
(`mock.patch.object(preretrieval, "collection_intent", ...)`, in the workshop, with a
function of the question, the collection list and the language)
reaches the step. `stem_text` and `blacklist_term` come from their own modules.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Callable
from typing import Any, Protocol

from pydantic_graph import GraphBuilder, Step, StepContext
from pydantic_graph.decision import Decision, DecisionBranch
from pydantic_graph.graph_builder import Graph
from pydantic_graph.paths import LabelMarker

from domain import tracing
from domain.chat import condense as condensation
from domain.chat import figure_check, history, preretrieval
from domain.chat import relevance
from domain.chat.config import DEFAULT_NUMBER_TOLERANCE
from domain.chat.figure_check import FigureCheck
from domain.chat.guardrail import (
    count_refused, has_grounding, mark_refused_exchanges,
)
from domain.chat.overlap import coverage
from domain.chat.postprocess import (
    advisory_table, answer_with_table, ensure_citation, injected_pages, mark_injected, mark_uncited,
)
from domain.chat.preretrieval import PreRetrievalTurn
from domain.chat.refusal import Refusal, RefusalCause, is_refusal
from domain.chat.scope import blacklist_term
from domain.i18n import get_locale
from domain.text.stemming import stem_text
from domain.verify.context import context_from_messages

R = tracing.READING


# ── The outputs and the states ────────────────────────────────────────────────

@dataclass(frozen=True)
class Refused:
    """The turn ends in a refusal before the agent; `text` is the answer of the turn."""

    text: str


@dataclass(frozen=True)
class AgentCall:
    """The turn goes on to the agent; `node` is the node that calls it (Q_AGENT_PAGES, Q_AGENT_COLLECTION, Q_AGENT_TOOLS or Q_AGENT_FRAGMENTS)."""

    node: str


@dataclass
class AnswerChecks:
    """The state of the output workflows: the answer of the agent and what each
    check of the answer found.

    raw:      the text of the agent.
    messages: the messages of the run (`result.all_messages()`).
    result:   the result of the run, for the `on_trace` callback of the turn.
    turn:     the state of the input workflow in the pre-retrieval mode; None
              in the agent mode.
    answer:   the answer as the steps change it; the final answer at the end.
    tolerance:    `[verify] number_tolerance` of the wiki, for C_FIGURES.
    conversation: the `(origin, text)` sources of the conversation that authorise
                  figures (`figure_check.conversation_sources`), for C_FIGURES.
    figures:      what C_FIGURES found; None when the path did not reach C_FIGURES.
    """

    raw: str
    messages: list
    language: str | None
    result: Any = None
    turn: PreRetrievalTurn | None = None
    answer: str = ""
    # Q_IS_GROUNDED (the answer covers the fragments), Q_TOOL_EVIDENCE and T_TOOL_EVIDENCE (a tool returned evidence).
    grounded: bool = False
    # Q_MODEL_REFUSED: the answer opens with the fixed refusal sentence of the model.
    model_refused: bool = False
    # The guardrail refusal replaced the answer. The agent mode no longer replaces it
    # (T_NO_EVIDENCE_WARNING); the field stays False there and is kept for the trace.
    refusal_substituted: bool = False
    # The additions of the pre-retrieval mode: the answer after the table, and
    # after the keys line.
    with_table: str = ""
    tabled: str = ""
    citation_added: bool = False
    tolerance: float = DEFAULT_NUMBER_TOLERANCE
    conversation: tuple = ()
    figures: FigureCheck | None = None


class QuestionState(Protocol):
    """The fields of the state of an input workflow that the nodes C_COUNT_HISTORY, C_WHICH_MODE, C_MARK_REFUSED, C_DROP_TABLES, C_TRIM_TURNS and C_ORIGIN_LINES and
    C_OPEN_PAGE_NOTE read and write: `PreRetrievalTurn` and `AgentTurn` both have them.

    prior:      the prior messages of the chat (`.role`, `.content`); None when the
                caller passed `history` already built.
    kept:       the history between C_MARK_REFUSED and C_ORIGIN_LINES, as chat messages.
    history:    the history that the model receives, as Pydantic AI messages.
    asked:      the question that the model receives, with the open-page note (C_OPEN_PAGE_NOTE).
    noted_page: the page that the note of C_OPEN_PAGE_NOTE names, or None.
    """

    question: str
    language: str | None
    prior: list | None
    mode: str
    kept: list
    history: list
    asked: str

    @property
    def noted_page(self) -> str | None: ...


QCtx = StepContext[QuestionState, None, QuestionState]
InCtx = StepContext[PreRetrievalTurn, None, PreRetrievalTurn]
OutCtx = StepContext[AnswerChecks, None, AnswerChecks]


# ── The nodes of the history ──────────────────────────────────────────────────

async def history_counts(ctx: QCtx) -> QuestionState:
    """The counts of the history of the turn, computed from the prior messages
    as `model_history` builds the history. C_MARK_REFUSED, C_DROP_TABLES, C_TRIM_TURNS and C_ORIGIN_LINES record the effect of each
    step."""
    turn = ctx.state
    tracing.mark("C_COUNT_HISTORY", R, **history.history_counts(turn.prior or [], turn.language))
    return turn


async def mode(ctx: QCtx) -> QuestionState:
    tracing.mark("C_WHICH_MODE", R, mode=ctx.state.mode)
    return ctx.state


async def strip_refused(ctx: QCtx) -> QuestionState:
    """A citation-less refusal left in the history primes the model to answer the
    next question without a citation: each refusal is replaced by a fixed note, and
    its question stays so that a follow-up keeps its topic."""
    turn = ctx.state
    if turn.prior is not None:
        turn.kept = mark_refused_exchanges(list(turn.prior), turn.language)
    tracing.mark("C_MARK_REFUSED", R, refused_marked=count_refused(turn.prior or []))
    return turn


async def compact_tables(ctx: QCtx) -> QuestionState:
    """The advisory table of each answer of the history is replaced by a
    placeholder: the model can call the advisory tool again."""
    turn = ctx.state
    before = turn.kept
    turn.kept = history.compact_tables(before)
    tracing.mark("C_DROP_TABLES", R, compacted=sum(1 for old, new in zip(before, turn.kept) if old is not new))
    return turn


async def trim_turns(ctx: QCtx) -> QuestionState:
    turn = ctx.state
    before = len(turn.kept)
    turn.kept = history.cap_turns(turn.kept, history.DEFAULT_MAX_TURNS)
    tracing.mark("C_TRIM_TURNS", R, trimmed=before - len(turn.kept), max_turns=history.DEFAULT_MAX_TURNS)
    return turn


async def origin_lines(ctx: QCtx) -> QuestionState:
    """The history that the model receives. An answer that read data ends with
    its origin line. Without `prior`, the history the caller passed stays."""
    turn = ctx.state
    lines = 0
    if turn.prior is not None:
        turn.history = history.with_origin_lines(turn.kept, turn.language)
        lines = sum(1 for m in turn.kept if m.role == "assistant"
                    and history.data_origin(getattr(m, "context", ()), turn.language))
    tracing.mark("C_ORIGIN_LINES", R, origin_lines=lines, history_messages=len(turn.history))
    return turn


async def open_page_note(ctx: QCtx) -> QuestionState:
    """The question that the model receives: preceded by the note of the page
    that the user is reading, so that a question that names no topic is read as
    a question about that page."""
    turn = ctx.state
    turn.asked = preretrieval.with_open_page(turn.question, turn.noted_page, turn.language)
    tracing.mark("C_OPEN_PAGE_NOTE", R, answer=turn.asked != turn.question, page=turn.noted_page)
    return turn


# ── The nodes of the question ─────────────────────────────────────────────────

async def question(ctx: InCtx) -> PreRetrievalTurn:
    tracing.mark("Q_QUESTION", R, question=ctx.state.question)
    return ctx.state


async def normalize(ctx: InCtx) -> PreRetrievalTurn:
    tracing.mark("Q_NORMALIZE", R, normalized=preretrieval._normalize(ctx.state.question))
    return ctx.state


async def blacklist(ctx: InCtx) -> PreRetrievalTurn:
    """The blacklist first (design_stemming.md, decision 14): a blacklisted
    question is refused before the datasets are read or the index searched."""
    turn = ctx.state
    preretrieval.blacklist(turn, turn.question)
    tracing.mark("Q_IN_BLACKLIST", R, answer=turn.blacklisted)
    return turn


async def stem(ctx: InCtx) -> PreRetrievalTurn:
    turn = ctx.state
    tracing.mark("Q_STEM", R, stemmed=stem_text(turn.question, turn.stem_language))
    return turn


async def data_detection(ctx: InCtx) -> PreRetrievalTurn:
    """Whether the question names data: a dataset term, an alias or a request
    for advice. Runs before Q_IN_ROSTER, as the data detection ran before the roster
    match."""
    turn = ctx.state
    preretrieval.ensure_roster(turn)
    preretrieval.detect_data(turn, turn.question)
    tracing.mark("Q_NAMES_DATA", R, answer=turn.has_data)
    return turn


async def roster(ctx: InCtx) -> PreRetrievalTurn:
    turn = ctx.state
    preretrieval.ensure_roster(turn)
    preretrieval.match_terms(turn, turn.question)
    tracing.mark("Q_IN_ROSTER", R, answer=turn.in_roster,
                 roster_size=len(turn.roster_terms) + len(turn.keys),
                 aliases=len(turn.aliases), matched=turn.matched)
    return turn


async def open_page(ctx: InCtx) -> PreRetrievalTurn:
    """The page the user is reading: injected first, and it makes the question
    covered — the user asks about what is on the screen."""
    turn = ctx.state
    turn.open_block = preretrieval._open_page_block(turn.workspace, turn.open_page)
    tracing.mark("Q_PAGE_OPEN", R, answer=turn.open_block is not None, page=turn.open_page)
    if turn.open_block is not None:
        turn.in_roster = True
    return turn


async def rewrite(ctx: InCtx) -> PreRetrievalTurn:
    """Query condensation: the model rewrites a follow-up that names no topic
    into a question that stands alone, from the conversation."""
    turn = ctx.state
    turn.rewrite = await turn.condense(turn.question, turn.history)
    tracing.mark("Q_REWRITE", R, rewrite=turn.rewrite)
    return turn


async def accept_rewrite(ctx: InCtx) -> PreRetrievalTurn:
    """The rewrite is accepted when it is one line, not empty and not too long.
    An accepted rewrite that differs from the question is what the gate and the
    search examine."""
    turn = ctx.state
    rewritten = condensation.accepted(turn.rewrite, turn.question)
    turn.matched = []
    if rewritten != turn.question:
        turn.search = rewritten
    tracing.mark("Q_RW_ACCEPTED", R, answer=rewritten != turn.question, rewritten=rewritten)
    return turn


async def blacklist_rewrite(ctx: InCtx) -> PreRetrievalTurn:
    """The blacklist on the rewrite, so a follow-up cannot bring in a
    blacklisted subject."""
    turn = ctx.state
    preretrieval.blacklist(turn, turn.search)
    tracing.mark("Q_RW_IN_BLACKLIST", R, answer=turn.blacklisted)
    return turn


async def data_detection_rewrite(ctx: InCtx) -> PreRetrievalTurn:
    turn = ctx.state
    preretrieval.detect_data(turn, turn.search)
    tracing.mark("Q_RW_NAMES_DATA", R, answer=turn.has_data)
    return turn


async def roster_rewrite(ctx: InCtx) -> PreRetrievalTurn:
    turn = ctx.state
    preretrieval.match_terms(turn, turn.search)
    tracing.mark("Q_RW_IN_ROSTER", R, answer=turn.in_roster, matched=turn.matched)
    return turn


async def condense(ctx: InCtx) -> PreRetrievalTurn:
    """The result of the condensation (Q_REWRITE, Q_RW_ACCEPTED, Q_RW_IN_BLACKLIST, Q_RW_NAMES_DATA and Q_RW_IN_ROSTER): the text that the gate and
    the search examine, and what the roster found on it. A rewrite that names
    a blacklist term does not reach this step: Q_RW_IN_BLACKLIST sends it to R_BLACKLIST."""
    turn = ctx.state
    # `search` is the accepted rewrite, or the question when Q_RW_ACCEPTED rejected it.
    tracing.mark("Q_CONDENSED", R, rewritten=turn.search, answer=turn.in_roster,
                 blacklisted=bool(turn.blacklisted), matched=turn.matched)
    return turn


async def wiki_search(ctx: InCtx) -> PreRetrievalTurn:
    """The wiki search runs only for a question in the roster: the plan uses its
    pages only then (decision 14)."""
    turn = ctx.state
    with tracing.node("Q_SEARCH_WIKI", R, fts_query=preretrieval._fts_query(turn.search, turn.language)) as q6:
        # Dataset keys (MEP, YPFD, a bank) name rows of a data file, not pages.
        # An alias is searched by the name it stands for (`alias_titles`).
        topics = preretrieval.alias_titles(
            [term for term in turn.matched if term not in turn.keys], turn.config.data_aliases)
        turn.wiki_hits = preretrieval.retrieve_wiki(turn.db_path, turn.search, language=turn.stem_language,
                                                    topics=topics)
        if turn.open_block is not None:
            open_label = preretrieval._hit_labels([turn.open_block])[0]
            turn.wiki_hits = [turn.open_block] + [
                h for h in turn.wiki_hits if preretrieval._hit_labels([h])[0] != open_label
            ]
        q6.set(pages=preretrieval._hit_labels(turn.wiki_hits))
    return turn


async def source_search(ctx: InCtx) -> PreRetrievalTurn:
    """The source search, by the question and by the names its aliases stand for, as
    Q_SEARCH_WIKI searches them: «montero» also finds the fragments of «cazador»."""
    turn = ctx.state
    query = preretrieval.source_query(turn)
    with tracing.node("Q_SEARCH_SOURCES", R, fts_query=preretrieval._fts_query(query, turn.language)) as q8:
        turn.doc_hits = preretrieval.retrieve_source_chunks(
            turn.db_path, query, language=turn.stem_language,
        )
        q8.set(fragments=preretrieval._hit_labels(turn.doc_hits))
    return turn


async def collection_pages(ctx: InCtx) -> PreRetrievalTurn:
    """A collection-shaped question ("what tales are in this wiki?") reads the
    overview and the index from disk; node Q_IS_COLLECTION decides on them.
    Empty when the wiki has neither page."""
    turn = ctx.state
    intent = preretrieval.collection_intent(
        turn.search, preretrieval.intent_lists(turn).collection, preretrieval.intent_lists(turn).language,
    )
    turn.collection_hits = preretrieval.retrieve_collection_pages(turn.workspace) if intent else []
    tracing.mark("Q_COLLECTION_PAGES", R, answer=bool(intent), pages=preretrieval._hit_labels(turn.collection_hits))
    return turn


async def collection_with_open_page(ctx: InCtx) -> PreRetrievalTurn:
    """A question that names no topic is in the roster only because of the open
    page (node Q_PAGE_OPEN), and node Q_PAGE_FOUND answers it from the wiki pages before node Q_IS_COLLECTION
    runs. So the collection pages are added after the open page: with the
    overview open, "what is in this wiki?" still gets the index."""
    turn = ctx.state
    added: list[str] = []
    if turn.open_block is not None and not turn.matched and turn.collection_hits:
        # The open page is labelled `/wiki/overview.md`, a collection page `wiki/overview.md`.
        labels = {label.lstrip("/") for label in preretrieval._hit_labels(turn.wiki_hits)}
        added = [h for h in turn.collection_hits if preretrieval._hit_labels([h])[0].lstrip("/") not in labels]
        turn.wiki_hits = turn.wiki_hits + added
    tracing.mark("Q_COLLECTION_OPEN_PAGE", R, answer=bool(added), pages=preretrieval._hit_labels(added))
    return turn


async def wiki_pages(ctx: InCtx) -> PreRetrievalTurn:
    """The wiki search returned a page, for a question in the roster: the
    Tier-1 plan from the wiki pages."""
    turn = ctx.state
    tracing.mark("Q_PAGE_FOUND", R, answer=bool(turn.wiki_hits), wiki_fragments=len(turn.wiki_hits))
    if turn.wiki_hits:
        turn.plan = preretrieval.tier1_plan(turn.wiki_hits)
    return turn


async def dataset_categories(ctx: InCtx) -> PreRetrievalTurn:
    """Tier 1 from wiki pages: the code adds the dataset the question names
    (decision 16) — the pages hold no values, and the model, told to answer from
    the context, does not reliably call query_dataset on its own. This node
    finds the categories the question names."""
    turn = ctx.state
    turn.named = preretrieval.named_categories(
        turn.search, turn.source, turn.config.data_aliases, turn.language,
    )
    turn.injection = preretrieval.DatasetInjection([], [], [])
    tracing.mark("Q_NAMES_CATEGORY", R, answer=bool(turn.named), categories=[n[0] for n in turn.named])
    return turn


async def dataset_keys(ctx: InCtx) -> PreRetrievalTurn:
    """The next named category: whether the question names keys of it."""
    turn = ctx.state
    turn.category = turn.named.pop(0)
    categoria, _, named_keys, _ = turn.category
    tracing.mark("Q_NAMES_KEY", R, category=categoria, answer=bool(named_keys), keys=named_keys)
    return turn


def _inject_block(turn: PreRetrievalTurn, block: str) -> None:
    """Add a dataset block to the injection and to the context of the plan."""
    turn.injection.blocks.append(block)
    context = f"{turn.plan.context}\n\n{block}" if turn.plan.context else block
    turn.plan = replace(turn.plan, context=context, blocks=(*turn.plan.blocks, block))


async def dataset_rows(ctx: InCtx) -> PreRetrievalTurn:
    """The rows of the keys the question names."""
    turn = ctx.state
    categoria, _, named_keys, rows = turn.category
    block, reference, origins = preretrieval.rows_block(categoria, named_keys, rows)
    _inject_block(turn, block)
    turn.injection.references.append(reference)
    for origin in origins:
        if origin not in turn.injection.fuentes:
            turn.injection.fuentes.append(origin)
    # `rows_block` gives one origin per injected row.
    tracing.mark("Q_KEY_ROWS", R, category=categoria, keys=named_keys, rows=len(origins))
    return turn


async def dataset_key_names(ctx: InCtx) -> PreRetrievalTurn:
    """The key names of the category, without values: the model can say which
    keys have data."""
    turn = ctx.state
    categoria, keys, _, _ = turn.category
    block, turn.injection.key_names[categoria] = preretrieval.key_names_block(
        categoria, keys, turn.source, turn.language,
    )
    _inject_block(turn, block)
    tracing.mark("Q_KEY_NAMES", R, category=categoria, key_names=keys)
    return turn


async def collection(ctx: InCtx) -> PreRetrievalTurn:
    """A question about the whole wiki, with an overview or an index to inject:
    the Tier-1 plan from those pages."""
    turn = ctx.state
    tracing.mark("Q_IS_COLLECTION", R, answer=bool(turn.collection_hits), collection_pages=len(turn.collection_hits))
    if turn.collection_hits:
        turn.plan = preretrieval.tier1_plan(turn.collection_hits)
    return turn


async def data_question(ctx: InCtx) -> PreRetrievalTurn:
    """A question that names known data goes to the data tools before any
    source fragment. The plan has no tier; the dataset nodes that follow
    (Q_NAMES_CATEGORY to Q_KEY_NAMES) inject the dataset the question names."""
    turn = ctx.state
    tracing.mark("Q_IS_DATA", R, answer=turn.has_data)
    if turn.has_data:
        turn.plan = preretrieval.data_plan()
    return turn


async def source_fragments(ctx: InCtx) -> PreRetrievalTurn:
    """The source search returned a fragment, for a question in the roster: the
    Tier-2 plan from the fragments."""
    turn = ctx.state
    found = bool(turn.doc_hits and turn.in_roster)
    tracing.mark("Q_FRAGMENT_FOUND", R, answer=found, source_fragments=len(turn.doc_hits), in_roster=turn.in_roster)
    if found:
        turn.plan = preretrieval.tier2_plan(turn.doc_hits)
    return turn


async def roster_refusal(ctx: InCtx) -> PreRetrievalTurn:
    """No plan answers the question: the roster chooses the refusal, R_NOTHING_FOUND or R_NOT_IN_ROSTER."""
    turn = ctx.state
    tracing.mark("Q_ROSTER_REFUSAL", R, answer=turn.in_roster)
    turn.plan = preretrieval._REFUSE
    return turn


async def synonyms(ctx: InCtx) -> PreRetrievalTurn:
    """The line that names each alias of the question with the name it stands for."""
    turn = ctx.state
    turn.synonyms = preretrieval.synonyms_line(turn.matched, turn.config.data_aliases, turn.language)
    tracing.mark("Q_SYNONYMS", R, answer=bool(turn.synonyms), line=turn.synonyms)
    return turn


async def prompt(ctx: InCtx) -> PreRetrievalTurn:
    """The prompt that the model receives: the injected text of the plan, the
    synonyms line and the question with the open-page note."""
    turn = ctx.state
    turn.prompt = preretrieval.build_prompt(turn.plan.context, turn.synonyms, turn.asked, turn.language)
    tracing.mark("Q_PROMPT", R, injected=bool(turn.plan.context), prompt_chars=len(turn.prompt))
    return turn


async def refuse_blacklist(ctx: InCtx) -> Refused:
    turn = ctx.state
    return Refused(preretrieval.refuse("R_BLACKLIST", turn, Refusal(
        RefusalCause.BLACKLIST, term=blacklist_term(turn.search, turn.config.blacklist),
    )))


async def refuse_not_in_roster(ctx: InCtx) -> Refused:
    turn = ctx.state
    return Refused(preretrieval.refuse("R_NOT_IN_ROSTER", turn, Refusal(
        RefusalCause.NOT_IN_ROSTER, suggestions=preretrieval.suggestions(turn),
    )))


async def refuse_nothing_found(ctx: InCtx) -> Refused:
    turn = ctx.state
    return Refused(preretrieval.refuse("R_NOTHING_FOUND", turn, Refusal(
        RefusalCause.NOTHING_FOUND, suggestions=preretrieval.suggestions(turn),
    )))


# ── The nodes of the answer, pre-retrieval mode ───────────────────────────────

async def fragment_coverage(ctx: OutCtx) -> AnswerChecks:
    """Tier 2 (raw doc): verify the answer actually draws on the retrieved chunk."""
    checks, turn = ctx.state, ctx.state.turn
    min_coverage = getattr(turn.config, "min_coverage", preretrieval._MIN_COVERAGE)
    score = coverage(checks.raw, turn.plan.context or "", turn.stem_language)
    checks.grounded = score >= min_coverage
    tracing.mark("Q_IS_GROUNDED", R, answer=checks.grounded, coverage=round(score, 3),
                 min_coverage=min_coverage)
    return checks


async def tool_evidence(ctx: OutCtx) -> AnswerChecks:
    checks = ctx.state
    checks.grounded = has_grounding(checks.messages)
    tracing.mark("Q_TOOL_EVIDENCE", R, answer=checks.grounded)
    return checks


async def model_refusal(ctx: OutCtx) -> AnswerChecks:
    """The model's own refusal (cause 5): the answer opens with the fixed sentence."""
    checks = ctx.state
    checks.model_refused = preretrieval.is_model_refusal(checks.raw, checks.language)
    tracing.mark("Q_MODEL_REFUSED", R, answer=checks.model_refused)
    return checks


async def refuse_unsupported(ctx: OutCtx) -> str:
    checks, turn = ctx.state, ctx.state.turn
    document = preretrieval._hit_labels(turn.doc_hits)[0].rsplit("/", 1)[-1] if turn.doc_hits else ""
    checks.answer = preretrieval.refuse("R_UNSUPPORTED", turn, Refusal(
        RefusalCause.UNSUPPORTED, document=document, suggestions=preretrieval.suggestions(turn),
    ), raw=checks.raw, result=checks.result)
    return checks.answer


async def refuse_ungrounded(ctx: OutCtx) -> str:
    checks, turn = ctx.state, ctx.state.turn
    checks.answer = preretrieval.refuse("R_NO_EVIDENCE", turn, Refusal(
        RefusalCause.UNGROUNDED, suggestions=preretrieval.suggestions(turn),
    ), raw=checks.raw, result=checks.result)
    return checks.answer


async def refuse_model(ctx: OutCtx) -> str:
    """The code keeps the model's text and adds the cause line and the
    suggestions (decision 8)."""
    checks, turn = ctx.state, ctx.state.turn
    checks.answer = preretrieval.refuse("R_MODEL", turn, Refusal(
        RefusalCause.MODEL, model_text=checks.raw, suggestions=preretrieval.suggestions(turn),
    ), raw=checks.raw, result=checks.result)
    return checks.answer


async def table(ctx: OutCtx) -> AnswerChecks:
    """The advisory table, appended when the advisory tool returned one."""
    checks = ctx.state
    checks.with_table = answer_with_table(checks.raw, checks.messages, checks.language)
    tracing.mark("Q_ADVISORY_TABLE", R, table_added=checks.with_table != checks.raw)
    return checks


async def keys(ctx: OutCtx) -> AnswerChecks:
    """The keys with data, offered when the model asked for a key without data."""
    checks = ctx.state
    checks.tabled = preretrieval.offer_available_keys(
        checks.with_table, checks.messages, checks.turn.injection, checks.language,
    )
    tracing.mark("Q_OFFER_KEYS", R, keys_offered=checks.tabled != checks.with_table)
    return checks


async def citation(ctx: OutCtx) -> AnswerChecks:
    """The citation of the tools that the model used and of the injected data."""
    checks, injection = ctx.state, ctx.state.turn.injection
    checks.answer = ensure_citation(
        checks.tabled, checks.messages,
        extra_references=injection.references, extra_fuentes=injection.fuentes,
    )
    tracing.mark("Q_CITE_TOOLS", R, citation_added=checks.answer != checks.tabled)
    return checks


async def injected_reference(ctx: OutCtx) -> AnswerChecks:
    """The wiki pages injected into the prompt are not tool returns, so
    `ensure_citation` cannot cite them: the answer says it received them."""
    checks = ctx.state
    cited = checks.answer
    pages = injected_pages(preretrieval.context_passage(b) for b in checks.turn.plan.blocks)
    checks.answer = mark_injected(cited, pages, checks.language)
    # Q_SHOWN records what Q_CITE_TOOLS and Q_CITE_PAGES added together.
    checks.citation_added = checks.answer != checks.tabled
    tracing.mark("Q_CITE_PAGES", R, injected_marked=checks.answer != cited)
    return checks


async def tier2_warning(ctx: OutCtx) -> AnswerChecks:
    checks = ctx.state
    checks.answer = f"{checks.answer}\n\n{get_locale(checks.turn.stem_language).tier2_warning}"
    tracing.mark("Q_SOURCE_WARNING", R)
    return checks


async def relevance_warning(ctx: OutCtx) -> AnswerChecks:
    """The check of the model workflow said that the wiki pages do not answer the question,
    and the retry found no fragment of the source documents: the answer is shown with a
    warning that names the check. Mark, don't block."""
    checks = ctx.state
    checks.answer = f"{checks.answer}\n\n{relevance.warning(checks.turn.relevance, checks.language)}"
    tracing.mark("Q_RELEVANCE_WARNING", R, check=checks.turn.relevance)
    return checks


async def evidence_check(ctx) -> Any:
    """Q_EVIDENCE_QUOTE, a step of the model workflow: the sentence of the evidence line of
    the answer is in the injected context (`relevance.quote_found`)."""
    calls = ctx.state
    quote = relevance.evidence_quote(calls.result.output)
    calls.passed = relevance.quote_found(quote, calls.turn.plan.context or "")
    tracing.mark("Q_EVIDENCE_QUOTE", R, quote=quote, found=calls.passed)
    return calls


async def judge_check(ctx) -> Any:
    """Q_JUDGE_PAGES, a step of the model workflow: a separate call to the model says whether
    the injected pages answer the question (`relevance.judge_says_yes`). With no judge, the
    check passes. The judge gets the open-page note of C_OPEN_PAGE_NOTE, like the model:
    without it, «¿Qué papel cumple en la historia?» names no subject among the pages."""
    calls = ctx.state
    turn = calls.turn
    asked = preretrieval.with_open_page(turn.search or turn.question, turn.noted_page, turn.language)
    verdict = await calls.judge(asked, turn.plan.context or "") if calls.judge else ""
    calls.passed = relevance.judge_says_yes(verdict)
    tracing.mark("Q_JUDGE_PAGES", R, verdict=verdict, passed=calls.passed)
    return calls


async def figures(ctx: OutCtx) -> AnswerChecks:
    """The figure check of the prose (`domain/chat/figure_check.py`). The text of the
    model is not edited; a note is appended when the prose has rounded figures."""
    checks, turn = ctx.state, ctx.state.turn
    injected = [preretrieval.context_passage(b) for b in turn.plan.blocks] if turn else []
    table = advisory_table(checks.messages)
    check = figure_check.check_answer(
        checks.answer, [*injected, *context_from_messages(checks.messages)], (table,) if table else (),
        checks.conversation, tolerance=checks.tolerance, language=checks.language,
    )
    checks.answer = figure_check.with_note(checks.answer, check)
    checks.figures = check
    tracing.mark("C_FIGURES", R, **figure_check.counts(check.report), note_added=bool(check.note))
    return checks


async def shown(ctx: OutCtx) -> str:
    checks, turn = ctx.state, ctx.state.turn
    tracing.mark("Q_SHOWN", R, final_answer=checks.answer, citation_added=checks.citation_added,
                 keys_offered=checks.tabled != checks.with_table)
    if turn.on_trace:
        turn.on_trace(raw=checks.raw, final=checks.answer, result=checks.result,
                      refusal_substituted=False)
    return checks.answer


# ── The nodes of the answer, agent mode ──────────────────────────────────────

async def evidence(ctx: OutCtx) -> AnswerChecks:
    checks = ctx.state
    checks.grounded = has_grounding(checks.messages)
    tracing.mark("T_TOOL_EVIDENCE", R, answer=checks.grounded)
    checks.answer = checks.raw
    return checks


async def no_evidence_warning(ctx: OutCtx) -> AnswerChecks:
    """No tool return backs the answer, after the retry (T_RETRY, `services/chat.py`):
    the answer is shown with a warning, never replaced. The warning says what the code
    knows: no consultation of the wiki backs the answer."""
    checks = ctx.state
    checks.answer = f"{checks.answer}\n\n{get_locale(checks.language).no_evidence_warning}"
    tracing.mark("T_NO_EVIDENCE_WARNING", R)
    return checks


async def agent_table(ctx: OutCtx) -> AnswerChecks:
    """Deterministic post-processing (domain/chat/postprocess.py): the advisory
    table is guaranteed whether or not the model reproduced it under history
    priming. No-op on a refusal."""
    checks = ctx.state
    tabled = answer_with_table(checks.answer, checks.messages, checks.language)
    tracing.mark("T_ADVISORY_TABLE", R, table_added=tabled != checks.answer)
    checks.answer = tabled
    return checks


async def agent_citation(ctx: OutCtx) -> AnswerChecks:
    """The source citation is guaranteed whether or not the model wrote it. No-op
    on a refusal."""
    checks = ctx.state
    cited = ensure_citation(checks.answer, checks.messages)
    tracing.mark("T_CITE_TOOLS", R, citation_added=cited != checks.answer)
    checks.answer = cited
    return checks


async def uncited_mark(ctx: OutCtx) -> AnswerChecks:
    """An answer that still cites nothing is delivered with a note, not refused. A
    refusal of the model is left as it is."""
    checks = ctx.state
    cited = checks.answer
    refused = checks.refusal_substituted or is_refusal(cited)
    marked = cited if refused else mark_uncited(cited, checks.messages, checks.language)
    tracing.mark("T_MARK_UNCITED", R, uncited_marked=marked != cited)
    checks.answer = marked
    return checks


async def agent_answer(ctx: OutCtx) -> str:
    checks = ctx.state
    tracing.mark("T_SHOWN", R, final_answer=checks.answer, refusal_substituted=checks.refusal_substituted)
    return checks.answer


# ── The catalog ───────────────────────────────────────────────────────────────

# Code of node → step function, in the order of `docs/query_nodes.md`.
CATALOG = {
    "C_COUNT_HISTORY": history_counts,
    "C_WHICH_MODE": mode,
    "C_MARK_REFUSED": strip_refused,
    "C_DROP_TABLES": compact_tables,
    "C_TRIM_TURNS": trim_turns,
    "C_ORIGIN_LINES": origin_lines,
    "C_FIGURES": figures,
    "C_OPEN_PAGE_NOTE": open_page_note,
    "Q_QUESTION": question,
    "Q_NORMALIZE": normalize,
    "Q_IN_BLACKLIST": blacklist,
    "Q_STEM": stem,
    "Q_NAMES_DATA": data_detection,
    "Q_IN_ROSTER": roster,
    "Q_PAGE_OPEN": open_page,
    "Q_REWRITE": rewrite,
    "Q_RW_ACCEPTED": accept_rewrite,
    "Q_RW_IN_BLACKLIST": blacklist_rewrite,
    "Q_RW_NAMES_DATA": data_detection_rewrite,
    "Q_RW_IN_ROSTER": roster_rewrite,
    "Q_CONDENSED": condense,
    "Q_SEARCH_WIKI": wiki_search,
    "Q_SEARCH_SOURCES": source_search,
    "Q_COLLECTION_PAGES": collection_pages,
    "Q_COLLECTION_OPEN_PAGE": collection_with_open_page,
    "Q_PAGE_FOUND": wiki_pages,
    "Q_NAMES_CATEGORY": dataset_categories,
    "Q_NAMES_KEY": dataset_keys,
    "Q_KEY_ROWS": dataset_rows,
    "Q_KEY_NAMES": dataset_key_names,
    "Q_IS_COLLECTION": collection,
    "Q_IS_DATA": data_question,
    "Q_FRAGMENT_FOUND": source_fragments,
    "Q_ROSTER_REFUSAL": roster_refusal,
    "Q_SYNONYMS": synonyms,
    "Q_PROMPT": prompt,
    "Q_IS_GROUNDED": fragment_coverage,
    "Q_SOURCE_WARNING": tier2_warning,
    "Q_TOOL_EVIDENCE": tool_evidence,
    "Q_MODEL_REFUSED": model_refusal,
    "Q_ADVISORY_TABLE": table,
    "Q_OFFER_KEYS": keys,
    "Q_CITE_TOOLS": citation,
    "Q_CITE_PAGES": injected_reference,
    "Q_SHOWN": shown,
    "Q_RELEVANCE_WARNING": relevance_warning,
    "Q_EVIDENCE_QUOTE": evidence_check,
    "Q_JUDGE_PAGES": judge_check,
    "R_BLACKLIST": refuse_blacklist,
    "R_NOT_IN_ROSTER": refuse_not_in_roster,
    "R_NOTHING_FOUND": refuse_nothing_found,
    "R_UNSUPPORTED": refuse_unsupported,
    "R_NO_EVIDENCE": refuse_ungrounded,
    "R_MODEL": refuse_model,
    "T_TOOL_EVIDENCE": evidence,
    "T_NO_EVIDENCE_WARNING": no_evidence_warning,
    "T_ADVISORY_TABLE": agent_table,
    "T_CITE_TOOLS": agent_citation,
    "T_MARK_UNCITED": uncited_mark,
    "T_SHOWN": agent_answer,
}


def register(g: GraphBuilder, code: str) -> Step:
    """Register in the graph `g` the step function of `code`, with `code` as its
    node id."""
    return g.step(CATALOG[code], node_id=code)


def _recorded(decision: str, branch: DecisionBranch) -> Callable[[Any], bool]:
    """The condition of `branch`, which records the decision in the trace when the
    branch is chosen. A branch without a condition is chosen when the input has
    the type of the branch, as `pydantic_graph` decides it."""
    label = next((item.label for item in branch.path.items if isinstance(item, LabelMarker)), "")
    condition = branch.matches

    def matches(value: Any) -> bool:
        chosen = condition(value) if condition else isinstance(value, branch.source)
        if chosen:
            tracing.mark(decision, R, branch=label)
        return chosen

    return matches


def trace_decisions(graph: Graph) -> Graph:
    """Make each decision of `graph` record a node in the trace, with the label of the
    branch it chose. `pydantic_graph` calls the conditions of a decision directly,
    so without this a decision leaves no node in the trace. Returns `graph`."""
    for node in graph.nodes.values():
        if isinstance(node, Decision):
            for branch in node.branches:
                branch.matches = _recorded(node.id, branch)
    return graph


def N_(label: str) -> str:
    """Mark a text of a diagram for translation, and return it unchanged: the
    label of a branch, the question of a decision, or the short text of a node
    (`node_texts.py`). The text is in English. `web/i18n_extract.py` extracts
    the marked texts into the catalogs of the web interface, and the audit page
    translates each text when it draws a diagram (`web/audit_diagram.py`)."""
    return label
