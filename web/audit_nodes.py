"""The short text of each node of the reading diagram, for the audit page of an answer.

The English texts are `NODE_TEXTS`, in `base/domain/chat/workflow/node_texts.py`, so
the script that writes `docs/workflow_diagrams.md` reads them without the web
dependencies. The text of a decision is its question, the `note` of the decision in
its graph. The Spanish text is in `locale/es/LC_MESSAGES/messages.po`. A node that
is in neither is shown by its identifier alone (`text`).
"""

from __future__ import annotations

from pydantic_graph.decision import Decision

from domain.chat.workflow import input_checks, output_checks
from domain.chat.workflow.node_texts import NODE_TEXTS
from web.i18n import _

__all__ = ["DECISION_QUESTIONS", "NODE_TEXTS", "text"]

# The question of each decision of the chat graphs, by the id of the decision.
DECISION_QUESTIONS = {
    node.id: node.note
    for graph in (input_checks.graph, output_checks.graph, output_checks.strict_graph)
    for node in graph.nodes.values()
    if isinstance(node, Decision) and node.note
}


def text(node_id: str) -> str:
    """The short text of a node in the interface language; '' when the node has none."""
    if node_id in NODE_TEXTS:
        return _(NODE_TEXTS[node_id])
    return _(DECISION_QUESTIONS[node_id]) if node_id in DECISION_QUESTIONS else ""
