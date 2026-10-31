"""The output workflow: the checks of the answer, in the pre-retrieval and the
strict modes.

The graph starts with the answer of the agent and the messages of the run
(`AnswerChecks`), and ends in the final answer of the turn. The first decision
chooses the steps of the mode:

- pre-retrieval: Q17 and R3 (Tier 2 only), Q19 and R4 (no injected context
  only), Q20 and R5, then the additions: `table` (`answer_with_table`), `keys`
  (`offer_available_keys`), `citation` (`ensure_citation`), Q18 (Tier 2 only)
  and Q21. The state carries the `PreRetrievalTurn` that the input workflow
  (`input_checks.py`) filled.
- strict: T2, T3 (no evidence only), T4 and T5. The state carries no turn.

The streaming mode runs no check and does not run this graph
(`chat_turn_stream_with_context`, `services/chat.py`).

The steps of the pre-retrieval mode call through the module `preretrieval` the
functions that it defines (`refuse`, `suggestions`, `offer_available_keys`),
as the steps of the input workflow do. The graph is built with
`auto_instrument=False`: the steps record the same `tracing.mark` calls as
before the graph, and the graph adds no span.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_graph import GraphBuilder, StepContext

from domain import tracing
from domain.chat import preretrieval
from domain.chat.guardrail import enforce_grounding, has_grounding, refusal_for
from domain.chat.overlap import coverage
from domain.chat.postprocess import answer_with_table, ensure_citation, mark_uncited
from domain.chat.preretrieval import PreRetrievalTurn
from domain.chat.refusal import Refusal, RefusalCause, fixed_sentence, is_refusal
from domain.i18n import get_locale

R = tracing.READING


@dataclass
class AnswerChecks:
    """The state of the output workflow: the answer of the agent and what each
    check of the answer found.

    raw:      the text of the agent.
    messages: the messages of the run (`result.all_messages()`).
    result:   the result of the run, for the `on_trace` callback of the turn.
    turn:     the state of the input workflow in the pre-retrieval mode; None
              in the strict mode.
    answer:   the answer as the steps change it; the final answer at the end.
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


Ctx = StepContext[AnswerChecks, None, AnswerChecks]

g = GraphBuilder(
    name="output_checks", state_type=AnswerChecks, input_type=AnswerChecks,
    output_type=str, auto_instrument=False,
)


# ── The conditions of the decisions ───────────────────────────────────────────

def _strict(checks: AnswerChecks) -> bool:
    return checks.turn is None


def _verifies(checks: AnswerChecks) -> bool:
    """A Tier-2 answer (raw source fragments) is verified against the fragments."""
    return checks.turn.plan.verify


def _no_context(checks: AnswerChecks) -> bool:
    """Injected context is the grounding of Tier 1 and Tier 2: the tool-based
    guardrail applies only to a data question, with no injected context."""
    return checks.turn.plan.context is None


def _ungrounded(checks: AnswerChecks) -> bool:
    return not checks.grounded


def _tier2(checks: AnswerChecks) -> bool:
    return checks.turn.plan.tier == "crudo"


# ── The steps of the pre-retrieval mode ───────────────────────────────────────

@g.step(node_id="Q17")
async def fragment_coverage(ctx: Ctx) -> AnswerChecks:
    """Tier 2 (raw doc): verify the answer actually draws on the retrieved chunk."""
    checks, turn = ctx.state, ctx.state.turn
    min_coverage = getattr(turn.config, "min_coverage", preretrieval._MIN_COVERAGE)
    score = coverage(checks.raw, turn.plan.context or "", turn.stem_language)
    checks.grounded = score >= min_coverage
    tracing.mark("Q17", R, answer=checks.grounded, coverage=round(score, 3),
                 min_coverage=min_coverage)
    return checks


@g.step(node_id="Q19")
async def tool_evidence(ctx: Ctx) -> AnswerChecks:
    checks = ctx.state
    checks.grounded = has_grounding(checks.messages)
    tracing.mark("Q19", R, answer=checks.grounded)
    return checks


@g.step(node_id="Q20")
async def model_refusal(ctx: Ctx) -> AnswerChecks:
    """The model's own refusal (cause 5): the answer opens with the fixed sentence."""
    checks = ctx.state
    checks.model_refused = checks.raw.lstrip().startswith(fixed_sentence(checks.language))
    tracing.mark("Q20", R, answer=checks.model_refused)
    return checks


@g.step(node_id="R3")
async def refuse_unsupported(ctx: Ctx) -> str:
    checks, turn = ctx.state, ctx.state.turn
    document = preretrieval._hit_labels(turn.doc_hits)[0].rsplit("/", 1)[-1] if turn.doc_hits else ""
    checks.answer = preretrieval.refuse("R3", turn, Refusal(
        RefusalCause.UNSUPPORTED, document=document, suggestions=preretrieval.suggestions(turn),
    ), raw=checks.raw, result=checks.result)
    return checks.answer


@g.step(node_id="R4")
async def refuse_ungrounded(ctx: Ctx) -> str:
    checks, turn = ctx.state, ctx.state.turn
    checks.answer = preretrieval.refuse("R4", turn, Refusal(
        RefusalCause.UNGROUNDED, suggestions=preretrieval.suggestions(turn),
    ), raw=checks.raw, result=checks.result)
    return checks.answer


@g.step(node_id="R5")
async def refuse_model(ctx: Ctx) -> str:
    """The code keeps the model's text and adds the cause line and the
    suggestions (decision 8)."""
    checks, turn = ctx.state, ctx.state.turn
    checks.answer = preretrieval.refuse("R5", turn, Refusal(
        RefusalCause.MODEL, model_text=checks.raw, suggestions=preretrieval.suggestions(turn),
    ), raw=checks.raw, result=checks.result)
    return checks.answer


@g.step(node_id="table")
async def table(ctx: Ctx) -> AnswerChecks:
    checks = ctx.state
    checks.with_table = answer_with_table(checks.raw, checks.messages)
    return checks


@g.step(node_id="keys")
async def keys(ctx: Ctx) -> AnswerChecks:
    checks = ctx.state
    checks.tabled = preretrieval.offer_available_keys(
        checks.with_table, checks.messages, checks.turn.injection, checks.language,
    )
    return checks


@g.step(node_id="citation")
async def citation(ctx: Ctx) -> AnswerChecks:
    checks, injection = ctx.state, ctx.state.turn.injection
    checks.answer = ensure_citation(
        checks.tabled, checks.messages,
        extra_references=injection.references, extra_fuentes=injection.fuentes,
    )
    checks.citation_added = checks.answer != checks.tabled
    return checks


@g.step(node_id="Q18")
async def tier2_warning(ctx: Ctx) -> AnswerChecks:
    checks = ctx.state
    checks.answer = f"{checks.answer}\n\n{get_locale(checks.turn.stem_language).tier2_warning}"
    tracing.mark("Q18", R)
    return checks


@g.step(node_id="Q21")
async def shown(ctx: Ctx) -> str:
    checks, turn = ctx.state, ctx.state.turn
    tracing.mark("Q21", R, final_answer=checks.answer, citation_added=checks.citation_added,
                 keys_offered=checks.tabled != checks.with_table)
    if turn.on_trace:
        turn.on_trace(raw=checks.raw, final=checks.answer, result=checks.result,
                      refusal_substituted=False)
    return checks.answer


# ── The steps of the strict mode ──────────────────────────────────────────────

@g.step(node_id="T2")
async def evidence(ctx: Ctx) -> AnswerChecks:
    checks = ctx.state
    checks.grounded = has_grounding(checks.messages)
    tracing.mark("T2", R, answer=checks.grounded)
    checks.answer = checks.raw
    return checks


@g.step(node_id="T3")
async def guardrail(ctx: Ctx) -> AnswerChecks:
    checks = ctx.state
    checks.answer = enforce_grounding(checks.raw, checks.messages, refusal=refusal_for(checks.language))
    checks.refusal_substituted = checks.answer != checks.raw
    if checks.refusal_substituted:
        tracing.mark("T3", R, final_answer=checks.answer)
    return checks


@g.step(node_id="T4")
async def additions(ctx: Ctx) -> AnswerChecks:
    """Deterministic post-processing (domain/chat/postprocess.py): guarantee the
    advisory table and a source citation regardless of whether the model
    reproduced them under history priming. Both no-op on a refusal."""
    checks = ctx.state
    tabled = answer_with_table(checks.answer, checks.messages)
    cited = ensure_citation(tabled, checks.messages)
    # An answer that still cites nothing is delivered with a note, not refused. A
    # refusal, of T3 or of the model, is left as it is.
    refused = checks.refusal_substituted or is_refusal(cited)
    marked = cited if refused else mark_uncited(cited, checks.messages, checks.language)
    tracing.mark("T4", R, table_added=tabled != checks.answer, citation_added=cited != tabled,
                 uncited_marked=marked != cited)
    checks.answer = marked
    return checks


@g.step(node_id="T5")
async def strict_answer(ctx: Ctx) -> str:
    checks = ctx.state
    tracing.mark("T5", R, final_answer=checks.answer, refusal_substituted=checks.refusal_substituted)
    return checks.answer


# ── The edges ─────────────────────────────────────────────────────────────────

g.add(
    g.edge_from(g.start_node).to(
        g.decision(node_id="mode")
        .branch(g.match(AnswerChecks, matches=_strict).label("strict").to(evidence))
        .branch(g.match(AnswerChecks, matches=_verifies).label("Tier 2").to(fragment_coverage))
        .branch(g.match(AnswerChecks, matches=_no_context).label("no context").to(tool_evidence))
        .branch(g.match(AnswerChecks).to(model_refusal))
    ),
    g.edge_from(fragment_coverage).to(
        g.decision(node_id="after_Q17")
        .branch(g.match(AnswerChecks, matches=_ungrounded).label("not grounded").to(refuse_unsupported))
        .branch(g.match(AnswerChecks).to(model_refusal))
    ),
    g.edge_from(tool_evidence).to(
        g.decision(node_id="after_Q19")
        .branch(g.match(AnswerChecks, matches=_ungrounded).label("no evidence").to(refuse_ungrounded))
        .branch(g.match(AnswerChecks).to(model_refusal))
    ),
    g.edge_from(model_refusal).to(
        g.decision(node_id="after_Q20")
        .branch(g.match(AnswerChecks, matches=lambda c: c.model_refused)
                .label("fixed sentence").to(refuse_model))
        .branch(g.match(AnswerChecks).to(table))
    ),
    g.edge_from(table).to(keys),
    g.edge_from(keys).to(citation),
    g.edge_from(citation).to(
        g.decision(node_id="after_citation")
        .branch(g.match(AnswerChecks, matches=_tier2).label("Tier 2").to(tier2_warning))
        .branch(g.match(AnswerChecks).to(shown))
    ),
    g.edge_from(tier2_warning).to(shown),
    g.edge_from(evidence).to(
        g.decision(node_id="after_T2")
        .branch(g.match(AnswerChecks, matches=_ungrounded).label("no evidence").to(guardrail))
        .branch(g.match(AnswerChecks).to(additions))
    ),
    g.edge_from(guardrail).to(additions),
    g.edge_from(additions).to(strict_answer),
    g.edge_from(refuse_unsupported, refuse_ungrounded, refuse_model, shown, strict_answer).to(g.end_node),
)

graph = g.build()
