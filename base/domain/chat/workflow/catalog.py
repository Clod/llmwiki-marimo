"""The catalog of the nodes of the chat workflows: one step function per node.

Each function receives the `StepContext` of a graph, reads and writes the state
of the turn, records its node with `tracing.mark` or `tracing.node`, and returns
the state. The functions carry no `@g.step` decorator. Each workflow registers
in its own graph the functions that it uses, with `register(g, code)`, so the
`node_id` of a step is always its code in `CATALOG`. A node that two workflows
use is the same function in both.

The steps of the input workflow of the pre-retrieval mode read the
`PreRetrievalTurn` of the turn. The nodes that both input workflows use (C1 to
C2d) read the fields that `QuestionState` names, which `PreRetrievalTurn` and
`StrictTurn` (`input_checks.py`) both have. The steps of the output workflows
read the `AnswerChecks` of the turn, defined here so that the annotations of the
steps resolve in this module.

The steps call through the module `preretrieval` the functions that it defines,
and `collection_intent`, so that a patch of a name of that module
(`mock.patch.object(preretrieval, "collection_intent", ...)`, in the workshop, with a
function of the question, the collection list and the language)
reaches the step. `stem_text` and `off_limits_term` come from their own modules.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol

from pydantic_graph import GraphBuilder, Step, StepContext

from domain import tracing
from domain.chat import condense as condensation
from domain.chat import figure_check, history, preretrieval
from domain.chat.config import DEFAULT_NUMBER_TOLERANCE
from domain.chat.figure_check import FigureCheck
from domain.chat.guardrail import enforce_grounding, has_grounding, refusal_for, strip_refused_exchanges
from domain.chat.overlap import coverage
from domain.chat.postprocess import (
    advisory_table, answer_with_table, ensure_citation, injected_pages, mark_injected, mark_uncited,
)
from domain.chat.preretrieval import PreRetrievalTurn
from domain.chat.refusal import Refusal, RefusalCause, fixed_sentence, is_refusal
from domain.chat.scope import off_limits_term
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
    """The turn goes on to the agent; `node` is the node that calls it (Q13 to Q16)."""

    node: str


@dataclass
class AnswerChecks:
    """The state of the output workflows: the answer of the agent and what each
    check of the answer found.

    raw:      the text of the agent.
    messages: the messages of the run (`result.all_messages()`).
    result:   the result of the run, for the `on_trace` callback of the turn.
    turn:     the state of the input workflow in the pre-retrieval mode; None
              in the strict mode.
    answer:   the answer as the steps change it; the final answer at the end.
    tolerance:    `[verify] number_tolerance` of the wiki, for C3.
    conversation: the `(origin, text)` sources of the conversation that authorise
                  figures (`figure_check.conversation_sources`), for C3.
    figures:      what C3 found; None when the path did not reach C3.
    """

    raw: str
    messages: list
    language: str | None
    result: Any = None
    turn: PreRetrievalTurn | None = None
    answer: str = ""
    # Q17 (the answer covers the fragments), Q19 and T2 (a tool returned evidence).
    grounded: bool = False
    # Q20: the answer opens with the fixed refusal sentence of the model.
    model_refused: bool = False
    # T3: the guardrail refusal replaced the answer.
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
    """The fields of the state of an input workflow that the nodes C1 to C2d and
    C4 read and write: `PreRetrievalTurn` and `StrictTurn` both have them.

    prior:      the prior messages of the chat (`.role`, `.content`); None when the
                caller passed `history` already built.
    kept:       the history between C2a and C2d, as chat messages.
    history:    the history that the model receives, as Pydantic AI messages.
    asked:      the question that the model receives, with the open-page note (C4).
    noted_page: the page that the note of C4 names, or None.
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
    as `model_history` builds the history. C2a to C2d record the effect of each
    step."""
    turn = ctx.state
    tracing.mark("C1", R, **history.history_counts(turn.prior or [], turn.language))
    return turn


async def mode(ctx: QCtx) -> QuestionState:
    tracing.mark("C2", R, mode=ctx.state.mode)
    return ctx.state


async def strip_refused(ctx: QCtx) -> QuestionState:
    """A citation-less refusal left in the history primes the model to answer the
    next question without a citation: each refused exchange is removed."""
    turn = ctx.state
    if turn.prior is not None:
        turn.kept = strip_refused_exchanges(list(turn.prior))
    tracing.mark("C2a", R, refused_dropped=len(turn.prior or []) - len(turn.kept))
    return turn


async def compact_tables(ctx: QCtx) -> QuestionState:
    """The advisory table of each answer of the history is replaced by a
    placeholder: the model can call the advisory tool again."""
    turn = ctx.state
    before = turn.kept
    turn.kept = history.compact_tables(before)
    tracing.mark("C2b", R, compacted=sum(1 for old, new in zip(before, turn.kept) if old is not new))
    return turn


async def trim_turns(ctx: QCtx) -> QuestionState:
    turn = ctx.state
    before = len(turn.kept)
    turn.kept = history.cap_turns(turn.kept, history.DEFAULT_MAX_TURNS)
    tracing.mark("C2c", R, trimmed=before - len(turn.kept), max_turns=history.DEFAULT_MAX_TURNS)
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
    tracing.mark("C2d", R, origin_lines=lines, history_messages=len(turn.history))
    return turn


async def open_page_note(ctx: QCtx) -> QuestionState:
    """The question that the model receives: preceded by the note of the page
    that the user is reading, so that a question that names no topic is read as
    a question about that page."""
    turn = ctx.state
    turn.asked = preretrieval.with_open_page(turn.question, turn.noted_page, turn.language)
    tracing.mark("C4", R, answer=turn.asked != turn.question, page=turn.noted_page)
    return turn


# ── The nodes of the question ─────────────────────────────────────────────────

async def question(ctx: InCtx) -> PreRetrievalTurn:
    tracing.mark("Q1", R, question=ctx.state.question)
    return ctx.state


async def normalize(ctx: InCtx) -> PreRetrievalTurn:
    tracing.mark("Q2", R, normalized=preretrieval._normalize(ctx.state.question))
    return ctx.state


async def blacklist(ctx: InCtx) -> PreRetrievalTurn:
    """The blacklist first (design_stemming.md, decision 14): a blacklisted
    question is refused before the datasets are read or the index searched."""
    turn = ctx.state
    preretrieval.blacklist(turn, turn.question)
    tracing.mark("Q3", R, answer=turn.off_limits)
    return turn


async def stem(ctx: InCtx) -> PreRetrievalTurn:
    turn = ctx.state
    tracing.mark("Q4", R, stemmed=stem_text(turn.question, turn.stem_language))
    return turn


async def data_detection(ctx: InCtx) -> PreRetrievalTurn:
    """Whether the question names data: a dataset term, an alias or a request
    for advice. Runs before Q5, as the data detection ran before the roster
    match."""
    turn = ctx.state
    preretrieval.ensure_roster(turn)
    preretrieval.detect_data(turn, turn.question)
    tracing.mark("Q4a", R, answer=turn.has_data)
    return turn


async def roster(ctx: InCtx) -> PreRetrievalTurn:
    turn = ctx.state
    preretrieval.ensure_roster(turn)
    preretrieval.match_terms(turn, turn.question)
    tracing.mark("Q5", R, answer=turn.in_roster,
                 roster_size=len(turn.roster_terms) + len(turn.keys),
                 aliases=len(turn.aliases), matched=turn.matched)
    return turn


async def open_page(ctx: InCtx) -> PreRetrievalTurn:
    """The page the user is reading: injected first, and it makes the question
    covered — the user asks about what is on the screen."""
    turn = ctx.state
    turn.open_block = preretrieval._open_page_block(turn.workspace, turn.open_page)
    tracing.mark("Q5a", R, answer=turn.open_block is not None, page=turn.open_page)
    if turn.open_block is not None:
        turn.in_roster = True
    return turn


async def rewrite(ctx: InCtx) -> PreRetrievalTurn:
    """Query condensation: the model rewrites a follow-up that names no topic
    into a question that stands alone, from the conversation."""
    turn = ctx.state
    turn.rewrite = await turn.condense(turn.question, turn.history)
    tracing.mark("Q5c", R, rewrite=turn.rewrite)
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
    tracing.mark("Q5d", R, answer=rewritten != turn.question, rewritten=rewritten)
    return turn


async def blacklist_rewrite(ctx: InCtx) -> PreRetrievalTurn:
    """The blacklist on the rewrite, so a follow-up cannot bring in a
    blacklisted subject."""
    turn = ctx.state
    preretrieval.blacklist(turn, turn.search)
    tracing.mark("Q5e", R, answer=turn.off_limits)
    return turn


async def data_detection_rewrite(ctx: InCtx) -> PreRetrievalTurn:
    turn = ctx.state
    preretrieval.detect_data(turn, turn.search)
    tracing.mark("Q5f", R, answer=turn.has_data)
    return turn


async def roster_rewrite(ctx: InCtx) -> PreRetrievalTurn:
    turn = ctx.state
    preretrieval.match_terms(turn, turn.search)
    tracing.mark("Q5g", R, answer=turn.in_roster, matched=turn.matched)
    return turn


async def condense(ctx: InCtx) -> PreRetrievalTurn:
    """The result of the condensation (Q5c to Q5g): the text that the gate and
    the search examine, and what the roster found on it. A rewrite that names
    a blacklist term does not reach this step: Q5e sends it to R1."""
    turn = ctx.state
    # `search` is the accepted rewrite, or the question when Q5d rejected it.
    tracing.mark("Q5b", R, rewritten=turn.search, answer=turn.in_roster,
                 off_limits=bool(turn.off_limits), matched=turn.matched)
    return turn


async def wiki_search(ctx: InCtx) -> PreRetrievalTurn:
    """The wiki search runs only for a question in the roster: the plan uses its
    pages only then (decision 14)."""
    turn = ctx.state
    with tracing.node("Q6", R, fts_query=preretrieval._fts_query(turn.search, turn.language)) as q6:
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
    turn = ctx.state
    with tracing.node("Q8", R, fts_query=preretrieval._fts_query(turn.search, turn.language)) as q8:
        turn.doc_hits = preretrieval.retrieve_source_chunks(
            turn.db_path, turn.search, language=turn.stem_language,
        )
        q8.set(fragments=preretrieval._hit_labels(turn.doc_hits))
    return turn


async def collection_pages(ctx: InCtx) -> PreRetrievalTurn:
    """A collection-shaped question ("what tales are in this wiki?") reads the
    overview and the index from disk; node Q9 decides on them.
    Empty when the wiki has neither page."""
    turn = ctx.state
    intent = preretrieval.collection_intent(
        turn.search, preretrieval.intent_lists(turn).collection, preretrieval.intent_lists(turn).language,
    )
    turn.collection_hits = preretrieval.retrieve_collection_pages(turn.workspace) if intent else []
    tracing.mark("Q8a", R, answer=bool(intent), pages=preretrieval._hit_labels(turn.collection_hits))
    return turn


async def collection_with_open_page(ctx: InCtx) -> PreRetrievalTurn:
    """A question that names no topic is in the roster only because of the open
    page (node Q5a), and node Q7 answers it from the wiki pages before node Q9
    runs. So the collection pages are added after the open page: with the
    overview open, "what is in this wiki?" still gets the index."""
    turn = ctx.state
    added: list[str] = []
    if turn.open_block is not None and not turn.matched and turn.collection_hits:
        # The open page is labelled `/wiki/overview.md`, a collection page `wiki/overview.md`.
        labels = {label.lstrip("/") for label in preretrieval._hit_labels(turn.wiki_hits)}
        added = [h for h in turn.collection_hits if preretrieval._hit_labels([h])[0].lstrip("/") not in labels]
        turn.wiki_hits = turn.wiki_hits + added
    tracing.mark("Q8b", R, answer=bool(added), pages=preretrieval._hit_labels(added))
    return turn


async def wiki_pages(ctx: InCtx) -> PreRetrievalTurn:
    """The wiki search returned a page, for a question in the roster: the
    Tier-1 plan from the wiki pages."""
    turn = ctx.state
    tracing.mark("Q7", R, answer=bool(turn.wiki_hits), wiki_fragments=len(turn.wiki_hits))
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
    tracing.mark("Q7a", R, answer=bool(turn.named), categories=[n[0] for n in turn.named])
    return turn


async def dataset_keys(ctx: InCtx) -> PreRetrievalTurn:
    """The next named category: whether the question names keys of it."""
    turn = ctx.state
    turn.category = turn.named.pop(0)
    categoria, _, named_keys, _ = turn.category
    tracing.mark("Q7b", R, category=categoria, answer=bool(named_keys), keys=named_keys)
    return turn


def _inject_block(turn: PreRetrievalTurn, block: str) -> None:
    """Add a dataset block to the injection and to the context of the plan."""
    turn.injection.blocks.append(block)
    turn.plan = replace(
        turn.plan, context=f"{turn.plan.context or ''}\n\n{block}", blocks=(*turn.plan.blocks, block),
    )


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
    tracing.mark("Q7c", R, category=categoria, keys=named_keys, rows=len(origins))
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
    tracing.mark("Q7d", R, category=categoria, key_names=keys)
    return turn


async def collection(ctx: InCtx) -> PreRetrievalTurn:
    """A question about the whole wiki, with an overview or an index to inject:
    the Tier-1 plan from those pages."""
    turn = ctx.state
    tracing.mark("Q9", R, answer=bool(turn.collection_hits), collection_pages=len(turn.collection_hits))
    if turn.collection_hits:
        turn.plan = preretrieval.tier1_plan(turn.collection_hits)
    return turn


async def data_question(ctx: InCtx) -> PreRetrievalTurn:
    """A question that names known data goes to the data tools before any
    source fragment: the plan injects nothing."""
    turn = ctx.state
    tracing.mark("Q10", R, answer=turn.has_data)
    if turn.has_data:
        turn.plan = preretrieval.data_plan()
    return turn


async def source_fragments(ctx: InCtx) -> PreRetrievalTurn:
    """The source search returned a fragment, for a question in the roster: the
    Tier-2 plan from the fragments."""
    turn = ctx.state
    found = bool(turn.doc_hits and turn.in_roster)
    tracing.mark("Q11", R, answer=found, source_fragments=len(turn.doc_hits), in_roster=turn.in_roster)
    if found:
        turn.plan = preretrieval.tier2_plan(turn.doc_hits)
    return turn


async def roster_refusal(ctx: InCtx) -> PreRetrievalTurn:
    """No plan answers the question: the roster chooses the refusal, R2b or R2a."""
    turn = ctx.state
    tracing.mark("Q12", R, answer=turn.in_roster)
    turn.plan = preretrieval._REFUSE
    return turn


async def synonyms(ctx: InCtx) -> PreRetrievalTurn:
    """The line that names each alias of the question with the name it stands for."""
    turn = ctx.state
    turn.synonyms = preretrieval.synonyms_line(turn.matched, turn.config.data_aliases, turn.language)
    tracing.mark("Q12a", R, answer=bool(turn.synonyms), line=turn.synonyms)
    return turn


async def prompt(ctx: InCtx) -> PreRetrievalTurn:
    """The prompt that the model receives: the injected text of the plan, the
    synonyms line and the question with the open-page note."""
    turn = ctx.state
    turn.prompt = preretrieval.build_prompt(turn.plan.context, turn.synonyms, turn.asked, turn.language)
    tracing.mark("Q12b", R, injected=bool(turn.plan.context), prompt_chars=len(turn.prompt))
    return turn


async def refuse_blacklist(ctx: InCtx) -> Refused:
    turn = ctx.state
    return Refused(preretrieval.refuse("R1", turn, Refusal(
        RefusalCause.BLACKLIST, term=off_limits_term(turn.search, turn.config.off_limits),
    )))


async def refuse_not_in_roster(ctx: InCtx) -> Refused:
    turn = ctx.state
    return Refused(preretrieval.refuse("R2a", turn, Refusal(
        RefusalCause.NOT_IN_ROSTER, suggestions=preretrieval.suggestions(turn),
    )))


async def refuse_nothing_found(ctx: InCtx) -> Refused:
    turn = ctx.state
    return Refused(preretrieval.refuse("R2b", turn, Refusal(
        RefusalCause.NOTHING_FOUND, suggestions=preretrieval.suggestions(turn),
    )))


# ── The nodes of the answer, pre-retrieval mode ───────────────────────────────

async def fragment_coverage(ctx: OutCtx) -> AnswerChecks:
    """Tier 2 (raw doc): verify the answer actually draws on the retrieved chunk."""
    checks, turn = ctx.state, ctx.state.turn
    min_coverage = getattr(turn.config, "min_coverage", preretrieval._MIN_COVERAGE)
    score = coverage(checks.raw, turn.plan.context or "", turn.stem_language)
    checks.grounded = score >= min_coverage
    tracing.mark("Q17", R, answer=checks.grounded, coverage=round(score, 3),
                 min_coverage=min_coverage)
    return checks


async def tool_evidence(ctx: OutCtx) -> AnswerChecks:
    checks = ctx.state
    checks.grounded = has_grounding(checks.messages)
    tracing.mark("Q19", R, answer=checks.grounded)
    return checks


async def model_refusal(ctx: OutCtx) -> AnswerChecks:
    """The model's own refusal (cause 5): the answer opens with the fixed sentence."""
    checks = ctx.state
    checks.model_refused = checks.raw.lstrip().startswith(fixed_sentence(checks.language))
    tracing.mark("Q20", R, answer=checks.model_refused)
    return checks


async def refuse_unsupported(ctx: OutCtx) -> str:
    checks, turn = ctx.state, ctx.state.turn
    document = preretrieval._hit_labels(turn.doc_hits)[0].rsplit("/", 1)[-1] if turn.doc_hits else ""
    checks.answer = preretrieval.refuse("R3", turn, Refusal(
        RefusalCause.UNSUPPORTED, document=document, suggestions=preretrieval.suggestions(turn),
    ), raw=checks.raw, result=checks.result)
    return checks.answer


async def refuse_ungrounded(ctx: OutCtx) -> str:
    checks, turn = ctx.state, ctx.state.turn
    checks.answer = preretrieval.refuse("R4", turn, Refusal(
        RefusalCause.UNGROUNDED, suggestions=preretrieval.suggestions(turn),
    ), raw=checks.raw, result=checks.result)
    return checks.answer


async def refuse_model(ctx: OutCtx) -> str:
    """The code keeps the model's text and adds the cause line and the
    suggestions (decision 8)."""
    checks, turn = ctx.state, ctx.state.turn
    checks.answer = preretrieval.refuse("R5", turn, Refusal(
        RefusalCause.MODEL, model_text=checks.raw, suggestions=preretrieval.suggestions(turn),
    ), raw=checks.raw, result=checks.result)
    return checks.answer


async def table(ctx: OutCtx) -> AnswerChecks:
    """The advisory table, appended when the advisory tool returned one."""
    checks = ctx.state
    checks.with_table = answer_with_table(checks.raw, checks.messages, checks.language)
    tracing.mark("Q20a", R, table_added=checks.with_table != checks.raw)
    return checks


async def keys(ctx: OutCtx) -> AnswerChecks:
    """The keys with data, offered when the model asked for a key without data."""
    checks = ctx.state
    checks.tabled = preretrieval.offer_available_keys(
        checks.with_table, checks.messages, checks.turn.injection, checks.language,
    )
    tracing.mark("Q20b", R, keys_offered=checks.tabled != checks.with_table)
    return checks


async def citation(ctx: OutCtx) -> AnswerChecks:
    """The citation of the tools that the model used and of the injected data."""
    checks, injection = ctx.state, ctx.state.turn.injection
    checks.answer = ensure_citation(
        checks.tabled, checks.messages,
        extra_references=injection.references, extra_fuentes=injection.fuentes,
    )
    tracing.mark("Q20c", R, citation_added=checks.answer != checks.tabled)
    return checks


async def injected_reference(ctx: OutCtx) -> AnswerChecks:
    """The wiki pages injected into the prompt are not tool returns, so
    `ensure_citation` cannot cite them: the answer says it received them."""
    checks = ctx.state
    cited = checks.answer
    pages = injected_pages(preretrieval.context_passage(b) for b in checks.turn.plan.blocks)
    checks.answer = mark_injected(cited, pages, checks.language)
    # Q21 records what Q20c and Q20d added together.
    checks.citation_added = checks.answer != checks.tabled
    tracing.mark("Q20d", R, injected_marked=checks.answer != cited)
    return checks


async def tier2_warning(ctx: OutCtx) -> AnswerChecks:
    checks = ctx.state
    checks.answer = f"{checks.answer}\n\n{get_locale(checks.turn.stem_language).tier2_warning}"
    tracing.mark("Q18", R)
    return checks


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
    tracing.mark("C3", R, **figure_check.counts(check.report), note_added=bool(check.note))
    return checks


async def shown(ctx: OutCtx) -> str:
    checks, turn = ctx.state, ctx.state.turn
    tracing.mark("Q21", R, final_answer=checks.answer, citation_added=checks.citation_added,
                 keys_offered=checks.tabled != checks.with_table)
    if turn.on_trace:
        turn.on_trace(raw=checks.raw, final=checks.answer, result=checks.result,
                      refusal_substituted=False)
    return checks.answer


# ── The nodes of the answer, strict mode ──────────────────────────────────────

async def evidence(ctx: OutCtx) -> AnswerChecks:
    checks = ctx.state
    checks.grounded = has_grounding(checks.messages)
    tracing.mark("T2", R, answer=checks.grounded)
    checks.answer = checks.raw
    return checks


async def guardrail(ctx: OutCtx) -> AnswerChecks:
    checks = ctx.state
    checks.answer = enforce_grounding(checks.raw, checks.messages, refusal=refusal_for(checks.language))
    checks.refusal_substituted = checks.answer != checks.raw
    if checks.refusal_substituted:
        tracing.mark("T3", R, final_answer=checks.answer)
    return checks


async def strict_table(ctx: OutCtx) -> AnswerChecks:
    """Deterministic post-processing (domain/chat/postprocess.py): the advisory
    table is guaranteed whether or not the model reproduced it under history
    priming. No-op on a refusal."""
    checks = ctx.state
    tabled = answer_with_table(checks.answer, checks.messages, checks.language)
    tracing.mark("T4a", R, table_added=tabled != checks.answer)
    checks.answer = tabled
    return checks


async def strict_citation(ctx: OutCtx) -> AnswerChecks:
    """The source citation is guaranteed whether or not the model wrote it. No-op
    on a refusal."""
    checks = ctx.state
    cited = ensure_citation(checks.answer, checks.messages)
    tracing.mark("T4b", R, citation_added=cited != checks.answer)
    checks.answer = cited
    return checks


async def uncited_mark(ctx: OutCtx) -> AnswerChecks:
    """An answer that still cites nothing is delivered with a note, not refused. A
    refusal, of T3 or of the model, is left as it is."""
    checks = ctx.state
    cited = checks.answer
    refused = checks.refusal_substituted or is_refusal(cited)
    marked = cited if refused else mark_uncited(cited, checks.messages, checks.language)
    tracing.mark("T4c", R, uncited_marked=marked != cited)
    checks.answer = marked
    return checks


async def strict_answer(ctx: OutCtx) -> str:
    checks = ctx.state
    tracing.mark("T5", R, final_answer=checks.answer, refusal_substituted=checks.refusal_substituted)
    return checks.answer


# ── The catalog ───────────────────────────────────────────────────────────────

# Code of node → step function, in the order of `docs/query_nodes.md`.
CATALOG = {
    "C1": history_counts,
    "C2": mode,
    "C2a": strip_refused,
    "C2b": compact_tables,
    "C2c": trim_turns,
    "C2d": origin_lines,
    "C3": figures,
    "C4": open_page_note,
    "Q1": question,
    "Q2": normalize,
    "Q3": blacklist,
    "Q4": stem,
    "Q4a": data_detection,
    "Q5": roster,
    "Q5a": open_page,
    "Q5c": rewrite,
    "Q5d": accept_rewrite,
    "Q5e": blacklist_rewrite,
    "Q5f": data_detection_rewrite,
    "Q5g": roster_rewrite,
    "Q5b": condense,
    "Q6": wiki_search,
    "Q8": source_search,
    "Q8a": collection_pages,
    "Q8b": collection_with_open_page,
    "Q7": wiki_pages,
    "Q7a": dataset_categories,
    "Q7b": dataset_keys,
    "Q7c": dataset_rows,
    "Q7d": dataset_key_names,
    "Q9": collection,
    "Q10": data_question,
    "Q11": source_fragments,
    "Q12": roster_refusal,
    "Q12a": synonyms,
    "Q12b": prompt,
    "Q17": fragment_coverage,
    "Q18": tier2_warning,
    "Q19": tool_evidence,
    "Q20": model_refusal,
    "Q20a": table,
    "Q20b": keys,
    "Q20c": citation,
    "Q20d": injected_reference,
    "Q21": shown,
    "R1": refuse_blacklist,
    "R2a": refuse_not_in_roster,
    "R2b": refuse_nothing_found,
    "R3": refuse_unsupported,
    "R4": refuse_ungrounded,
    "R5": refuse_model,
    "T2": evidence,
    "T3": guardrail,
    "T4a": strict_table,
    "T4b": strict_citation,
    "T4c": uncited_mark,
    "T5": strict_answer,
}


def register(g: GraphBuilder, code: str) -> Step:
    """Register in the graph `g` the step function of `code`, with `code` as its
    node id."""
    return g.step(CATALOG[code], node_id=code)


def N_(label: str) -> str:
    """Mark a text of a diagram for translation, and return it unchanged: the
    label of a branch, the question of a decision, or the short text of a node
    (`node_texts.py`). The text is in English. `web/i18n_extract.py` extracts
    the marked texts into the catalogs of the web interface, and the audit page
    translates each text when it draws a diagram (`web/audit_diagram.py`)."""
    return label
