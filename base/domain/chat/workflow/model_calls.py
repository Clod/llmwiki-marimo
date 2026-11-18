"""The model workflow of the pre-retrieval mode: the calls to the model, between the input
workflow (`input_checks.py`) and the output workflow (`output_checks.py`).

A pre-retrieval turn runs three graphs: input, model, output. The input workflow ends in
the node that names the text injected into the prompt (Q_AGENT_PAGES, Q_AGENT_COLLECTION,
Q_AGENT_TOOLS or Q_AGENT_FRAGMENTS). This workflow calls the model under that node. When
the model answered with the wiki pages, the check chosen for the turn
(`PreRetrievalTurn.relevance`, `domain/chat/relevance.py`) decides whether the pages answer
the question: none, the evidence quote (Q_EVIDENCE_QUOTE) or the judge (Q_JUDGE_PAGES).
When the model refused, or the check failed, it searches the source documents
(Q_RETRY_SOURCES) and, with fragments, calls the model again under Q_AGENT_FRAGMENTS. A
failed check with no fragment marks the turn (`relevance_failed`), and the output workflow
appends a warning (Q_RELEVANCE_WARNING). The output workflow then checks the last answer.

The steps call `preretrieval.call_agent` and `preretrieval.search_sources_for_retry`, which
record the nodes in the trace, as the code between the two workflows recorded them before
this workflow existed. Each decision records a node with the label of the branch it chose
(`trace_decisions`). The graph is built with `auto_instrument=False`, as the other two.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic_graph import GraphBuilder, StepContext

from domain.chat import preretrieval, relevance
from domain.chat.workflow.catalog import N_, register, trace_decisions
from domain.chat.workflow.input_checks import AGENT_CALL_NODES, AGENT_LABELS


@dataclass
class ModelCalls:
    """The state of the model workflow.

    turn:       the `PreRetrievalTurn` that the input workflow filled.
    node:       the node that the input workflow ended in: the first call to the model.
    run_agent:  the call to the model, `run_agent(prompt, history)`.
    on_context: the callback that receives the passages injected into a prompt, or None.
    result:     the result of the last call to the model; the output of the workflow.
    judge:      the call of the judge check, `judge(question, context) -> str`, or None.
    passed:     whether the check of the turn found that the pages answer the question.
    found:      whether the retry Q_RETRY_SOURCES found fragments of the source documents.
    """

    turn: Any
    node: str
    run_agent: Callable
    on_context: Callable | None = None
    result: Any = None
    judge: Callable | None = None
    passed: bool = True
    found: bool = False


Ctx = StepContext[ModelCalls, None, ModelCalls]

# The checks of the catalog (`catalog.CATALOG`) that this workflow registers, and the steps
# of this module: the calls to the model and the retry, which are not checks. The test
# `tests/unit/test_workflow_nodes_doc.py` compares the lists with the graph.
NODES = ("Q_EVIDENCE_QUOTE", "Q_JUDGE_PAGES")
OWN_STEPS = (*AGENT_CALL_NODES, "Q_RETRY_SOURCES")

g = GraphBuilder(
    name="model_calls", state_type=ModelCalls, input_type=ModelCalls,
    output_type=ModelCalls, auto_instrument=False,
)


def _call_step(node: str):
    """The step `node`: call the model with the prompt of the turn, recorded as `node`."""
    async def call(ctx: Ctx) -> ModelCalls:
        calls = ctx.state
        calls.result = await preretrieval.call_agent(calls.turn, node, calls.run_agent)
        return calls
    return g.step(call, node_id=node)


async def _retry(ctx: Ctx) -> ModelCalls:
    """Q_RETRY_SOURCES: search the source documents after a refusal with the wiki pages, or
    after a failed check. A failed check with no fragment marks the turn."""
    calls = ctx.state
    calls.found = preretrieval.search_sources_for_retry(calls.turn, calls.result.output, calls.on_context)
    calls.turn.relevance_failed = not calls.passed and not calls.found
    return calls


call_steps = {node: _call_step(node) for node in AGENT_CALL_NODES}
retry = g.step(_retry, node_id="Q_RETRY_SOURCES")
evidence = register(g, "Q_EVIDENCE_QUOTE")
judge = register(g, "Q_JUDGE_PAGES")


def _first(node: str):
    return lambda calls: calls.node == node


def _chosen(check: str):
    return lambda calls: calls.turn.relevance == check


def _refused(calls: ModelCalls) -> bool:
    return preretrieval.is_model_refusal(calls.result.output, calls.turn.language)


first_call = g.decision(node_id="model_call", note=N_("What text was injected into the prompt?"))
for _node, _label in zip(AGENT_CALL_NODES, AGENT_LABELS):
    first_call = first_call.branch(g.match(ModelCalls, matches=_first(_node)).label(_label).to(call_steps[_node]))

g.add(
    g.edge_from(g.start_node).to(first_call),
    g.edge_from(call_steps["Q_AGENT_PAGES"]).to(
        g.decision(node_id="after_Q_AGENT_PAGES", note=N_("Did the model refuse with the wiki pages?"))
        .branch(g.match(ModelCalls, matches=_refused).label(N_("the model refused")).to(retry))
        .branch(g.match(ModelCalls).label(N_("the model answered")).to(
            g.decision(node_id="relevance", note=N_("How is it checked that the wiki pages answer the question?"))
            .branch(g.match(ModelCalls, matches=_chosen(relevance.EVIDENCE)).label(N_("evidence quote")).to(evidence))
            .branch(g.match(ModelCalls, matches=_chosen(relevance.JUDGE)).label(N_("judge")).to(judge))
            .branch(g.match(ModelCalls).label(N_("no check")).to(g.end_node))
        ))
    ),
    g.edge_from(evidence, judge).to(
        g.decision(node_id="after_relevance", note=N_("Do the wiki pages answer the question?"))
        .branch(g.match(ModelCalls, matches=lambda calls: calls.passed)
                .label(N_("the pages answer the question")).to(g.end_node))
        .branch(g.match(ModelCalls).label(N_("the pages do not answer the question")).to(retry))
    ),
    g.edge_from(retry).to(
        g.decision(node_id="after_Q_RETRY_SOURCES", note=N_("Were fragments of the source documents found?"))
        .branch(g.match(ModelCalls, matches=lambda calls: calls.found)
                .label(N_("fragments found")).to(call_steps["Q_AGENT_FRAGMENTS"]))
        .branch(g.match(ModelCalls).label(N_("no fragment found")).to(g.end_node))
    ),
    g.edge_from(call_steps["Q_AGENT_COLLECTION"], call_steps["Q_AGENT_TOOLS"],
                call_steps["Q_AGENT_FRAGMENTS"]).to(g.end_node),
)

graph = trace_decisions(g.build())
