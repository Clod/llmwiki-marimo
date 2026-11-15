"""The steps of a recorded path, without the decisions.

Each decision of the chat graphs records a node with the branch it chose
(`trace_decisions`, `base/domain/chat/workflow/catalog.py`). The tests that check
the sequence of steps compare `steps(path)`. The tests of the decisions read the
whole path.
"""

from __future__ import annotations

from pydantic_graph.decision import Decision

from domain.chat.workflow import input_checks, output_checks

DECISIONS = frozenset(
    node.id
    for graph in (input_checks.graph, output_checks.graph, output_checks.strict_graph)
    for node in graph.nodes.values()
    if isinstance(node, Decision)
)


def steps(path) -> list[str]:
    """The ids of the recorded nodes that are steps, in order."""
    return [entry["node"] for entry in path if entry["node"] not in DECISIONS]
