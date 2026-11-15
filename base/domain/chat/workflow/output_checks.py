"""The two output workflows: the checks of the answer, one workflow per mode.

Each graph starts with the answer of the agent and the messages of the run
(`AnswerChecks`), and ends in the final answer of the turn.

- `graph`, the pre-retrieval mode: Q_IS_GROUNDED and R_UNSUPPORTED (Tier 2 only), Q_TOOL_EVIDENCE and R_NO_EVIDENCE (no
  injected context only), Q_MODEL_REFUSED and R_MODEL, then the additions: Q_ADVISORY_TABLE
  (`answer_with_table`), Q_OFFER_KEYS (`offer_available_keys`), Q_CITE_TOOLS
  (`ensure_citation`), Q_CITE_PAGES (`mark_injected`), Q_SOURCE_WARNING (Tier 2 only), C_FIGURES and Q_SHOWN. The first decision,
  `tier`, chooses between Q_IS_GROUNDED, Q_TOOL_EVIDENCE and Q_MODEL_REFUSED. The state carries the
  `PreRetrievalTurn` that the input workflow (`input_checks.py`) filled.
- `strict_graph`, the strict mode: T_TOOL_EVIDENCE, T_GUARDRAIL (no evidence only), then the
  additions: T_ADVISORY_TABLE (`answer_with_table`), T_CITE_TOOLS (`ensure_citation`), T_MARK_UNCITED
  (`mark_uncited`), and C_FIGURES and T_SHOWN.
  The state carries no turn.

C_FIGURES is the figure check (`domain/chat/figure_check.py`), the last step before the
answer in both graphs. The two graphs register the same function of the catalog
for C_FIGURES. A refusal (R_UNSUPPORTED, R_NO_EVIDENCE, R_MODEL, T_GUARDRAIL) does not reach C_FIGURES: the code wrote it.

The streaming mode runs neither graph (`chat_turn_stream_with_context`,
`services/chat.py`). The streaming mode runs no check, C_FIGURES included.

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
    "Q_IS_GROUNDED", "Q_TOOL_EVIDENCE", "Q_MODEL_REFUSED", "R_UNSUPPORTED", "R_NO_EVIDENCE", "R_MODEL", "Q_ADVISORY_TABLE", "Q_OFFER_KEYS", "Q_CITE_TOOLS", "Q_CITE_PAGES", "Q_SOURCE_WARNING", "C_FIGURES", "Q_SHOWN",
)
STRICT_NODES = ("T_TOOL_EVIDENCE", "T_GUARDRAIL", "T_ADVISORY_TABLE", "T_CITE_TOOLS", "T_MARK_UNCITED", "C_FIGURES", "T_SHOWN")
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
    with the containment coefficient (Q_IS_GROUNDED), unless the sentence verifier checks it."""
    return checks.turn.plan.verify and not checks.turn.sentence_verifier


def _verified_by_sentences(checks: AnswerChecks) -> bool:
    """A Tier-2 answer that the sentence verifier checks after it is shown: it
    skips Q_IS_GROUNDED, and the verifier is its only comparison against the fragments."""
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

fragment_coverage = register(g, "Q_IS_GROUNDED")
tool_evidence = register(g, "Q_TOOL_EVIDENCE")
model_refusal = register(g, "Q_MODEL_REFUSED")
refuse_unsupported = register(g, "R_UNSUPPORTED")
refuse_ungrounded = register(g, "R_NO_EVIDENCE")
refuse_model = register(g, "R_MODEL")
table = register(g, "Q_ADVISORY_TABLE")
keys = register(g, "Q_OFFER_KEYS")
citation = register(g, "Q_CITE_TOOLS")
injected_reference = register(g, "Q_CITE_PAGES")
tier2_warning = register(g, "Q_SOURCE_WARNING")
figures = register(g, "C_FIGURES")
shown = register(g, "Q_SHOWN")

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

evidence = register(strict, "T_TOOL_EVIDENCE")
guardrail = register(strict, "T_GUARDRAIL")
strict_table = register(strict, "T_ADVISORY_TABLE")
strict_citation = register(strict, "T_CITE_TOOLS")
uncited_mark = register(strict, "T_MARK_UNCITED")
strict_figures = register(strict, "C_FIGURES")
strict_answer = register(strict, "T_SHOWN")

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
