"""The two output workflows: the checks of the answer, one workflow per mode.

Each graph starts with the answer of the agent and the messages of the run
(`AnswerChecks`), and ends in the final answer of the turn.

- `graph`, the pre-retrieval mode: Q17 and R3 (Tier 2 only), Q19 and R4 (no
  injected context only), Q20 and R5, then the additions: Q20a
  (`answer_with_table`), Q20b (`offer_available_keys`), Q20c
  (`ensure_citation`), Q20d (`mark_injected`), Q18 (Tier 2 only), C3 and Q21. The first decision,
  `tier`, chooses between Q17, Q19 and Q20. The state carries the
  `PreRetrievalTurn` that the input workflow (`input_checks.py`) filled.
- `strict_graph`, the strict mode: T2, T3 (no evidence only), then the
  additions: T4a (`answer_with_table`), T4b (`ensure_citation`), T4c
  (`mark_uncited`), and C3 and T5.
  The state carries no turn.

C3 is the figure check (`domain/chat/figure_check.py`), the last step before the
answer in both graphs. The two graphs register the same function of the catalog
for C3. A refusal (R3, R4, R5, T3) does not reach C3: the code wrote it.

The streaming mode runs neither graph (`chat_turn_stream_with_context`,
`services/chat.py`). The streaming mode runs no check, C3 included.

The steps of the nodes are functions of the catalog (`catalog.py`), registered
here with `register`. `PRE_RETRIEVAL_NODES` and `STRICT_NODES` list the nodes of
each graph. The steps of the pre-retrieval mode call through the
module `preretrieval` the functions that it defines (`refuse`, `suggestions`,
`offer_available_keys`), as the steps of the input workflow do. Both graphs are
built with `auto_instrument=False`: the steps record the same `tracing.mark`
calls as before the graphs, and the graphs add no span.
"""

from __future__ import annotations

from pydantic_graph import GraphBuilder

# `AnswerChecks` is the state of the graph; `_pre_retrieval_turn`, the service
# and the tests import it from this module.
from domain.chat.workflow.catalog import N_, AnswerChecks, register

# The nodes of the catalog that each output workflow registers. Both workflows
# have no step of their own. The test `tests/unit/test_workflow_nodes_doc.py`
# compares each list with its graph, and `docs/workflow_nodes.md` is generated
# from the lists (`table.py`).
PRE_RETRIEVAL_NODES = (
    "Q17", "Q19", "Q20", "R3", "R4", "R5", "Q20a", "Q20b", "Q20c", "Q20d", "Q18", "C3", "Q21",
)
STRICT_NODES = ("T2", "T3", "T4a", "T4b", "T4c", "C3", "T5")
OWN_STEPS: tuple[str, ...] = ()

g = GraphBuilder(
    name="output_checks", state_type=AnswerChecks, input_type=AnswerChecks,
    output_type=str, auto_instrument=False,
)
strict = GraphBuilder(
    name="strict_output_checks", state_type=AnswerChecks, input_type=AnswerChecks,
    output_type=str, auto_instrument=False,
)


# ── The conditions of the decisions ───────────────────────────────────────────

def _verifies(checks: AnswerChecks) -> bool:
    """A Tier-2 answer (raw source fragments) is verified against the fragments
    with the containment coefficient (Q17), unless the sentence verifier checks it."""
    return checks.turn.plan.verify and not checks.turn.sentence_verifier


def _verified_by_sentences(checks: AnswerChecks) -> bool:
    """A Tier-2 answer that the sentence verifier checks after it is shown: it
    skips Q17, and the verifier is its only comparison against the fragments."""
    return checks.turn.plan.verify and checks.turn.sentence_verifier


def _no_context(checks: AnswerChecks) -> bool:
    """Injected context is the grounding of Tier 1 and Tier 2: the tool-based
    guardrail applies only to a data question, with no injected context."""
    return checks.turn.plan.context is None


def _ungrounded(checks: AnswerChecks) -> bool:
    return not checks.grounded


def _tier2(checks: AnswerChecks) -> bool:
    return checks.turn.plan.tier == "crudo"


# ── The output workflow of the pre-retrieval mode ─────────────────────────────

fragment_coverage = register(g, "Q17")
tool_evidence = register(g, "Q19")
model_refusal = register(g, "Q20")
refuse_unsupported = register(g, "R3")
refuse_ungrounded = register(g, "R4")
refuse_model = register(g, "R5")
table = register(g, "Q20a")
keys = register(g, "Q20b")
citation = register(g, "Q20c")
injected_reference = register(g, "Q20d")
tier2_warning = register(g, "Q18")
figures = register(g, "C3")
shown = register(g, "Q21")

g.add(
    g.edge_from(g.start_node).to(
        g.decision(node_id="tier", note=N_("What was injected, and does the sentence verifier check the answer?"))
        .branch(g.match(AnswerChecks, matches=_verifies)
                .label(N_("fragments of the source documents, without the sentence verifier"))
                .to(fragment_coverage))
        .branch(g.match(AnswerChecks, matches=_verified_by_sentences)
                .label(N_("fragments of the source documents, with the sentence verifier later"))
                .to(model_refusal))
        .branch(g.match(AnswerChecks, matches=_no_context).label(N_("nothing injected")).to(tool_evidence))
        .branch(g.match(AnswerChecks).label(N_("wiki pages or data injected")).to(model_refusal))
    ),
    g.edge_from(fragment_coverage).to(
        g.decision(node_id="after_Q17", note=N_("Is the answer grounded in the fragments?"))
        .branch(g.match(AnswerChecks, matches=_ungrounded).label(N_("answer not grounded in the fragments")).to(refuse_unsupported))
        .branch(g.match(AnswerChecks).label(N_("answer grounded in the fragments")).to(model_refusal))
    ),
    g.edge_from(tool_evidence).to(
        g.decision(node_id="after_Q19", note=N_("Did a data tool return evidence?"))
        .branch(g.match(AnswerChecks, matches=_ungrounded).label(N_("no data tool returned evidence")).to(refuse_ungrounded))
        .branch(g.match(AnswerChecks).label(N_("a data tool returned evidence")).to(model_refusal))
    ),
    g.edge_from(model_refusal).to(
        g.decision(node_id="after_Q20", note=N_("Does the answer open with the fixed refusal sentence?"))
        .branch(g.match(AnswerChecks, matches=lambda c: c.model_refused)
                .label(N_("opens with the fixed refusal sentence")).to(refuse_model))
        .branch(g.match(AnswerChecks).label(N_("does not open with the fixed refusal sentence")).to(table))
    ),
    g.edge_from(table).to(keys),
    g.edge_from(keys).to(citation),
    g.edge_from(citation).to(injected_reference),
    g.edge_from(injected_reference).to(
        g.decision(node_id="after_Q20d", note=N_("Were fragments of the source documents injected?"))
        .branch(g.match(AnswerChecks, matches=_tier2).label(N_("fragments of the source documents injected")).to(tier2_warning))
        .branch(g.match(AnswerChecks).label(N_("no fragments of the source documents injected")).to(figures))
    ),
    g.edge_from(tier2_warning).to(figures),
    g.edge_from(figures).to(shown),
    g.edge_from(refuse_unsupported, refuse_ungrounded, refuse_model, shown).to(g.end_node),
)

graph = g.build()


# ── The output workflow of the strict mode ────────────────────────────────────

evidence = register(strict, "T2")
guardrail = register(strict, "T3")
strict_table = register(strict, "T4a")
strict_citation = register(strict, "T4b")
uncited_mark = register(strict, "T4c")
strict_figures = register(strict, "C3")
strict_answer = register(strict, "T5")

strict.add(
    strict.edge_from(strict.start_node).to(evidence),
    strict.edge_from(evidence).to(
        strict.decision(node_id="after_T2", note=N_("Did a tool return evidence?"))
        .branch(strict.match(AnswerChecks, matches=_ungrounded).label(N_("no tool returned evidence")).to(guardrail))
        .branch(strict.match(AnswerChecks).label(N_("a tool returned evidence")).to(strict_table))
    ),
    strict.edge_from(guardrail).to(strict_table),
    strict.edge_from(strict_table).to(strict_citation),
    strict.edge_from(strict_citation).to(uncited_mark),
    strict.edge_from(uncited_mark).to(
        strict.decision(node_id="after_T4c", note=N_("Did the guardrail refusal replace the answer?"))
        .branch(strict.match(AnswerChecks, matches=lambda c: c.refusal_substituted)
                .label(N_("answer replaced by the guardrail refusal")).to(strict_answer))
        .branch(strict.match(AnswerChecks)
                .label(N_("answer not replaced by the guardrail refusal")).to(strict_figures))
    ),
    strict.edge_from(strict_figures).to(strict_answer),
    strict.edge_from(strict_answer).to(strict.end_node),
)

strict_graph = strict.build()
