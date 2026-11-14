"""The Mermaid flowchart of a chat workflow, converted from the text of its graph.

`flowchart` reads the text of `graph.render()`, a `stateDiagram-v2`, and writes a
Mermaid flowchart. Each step is a box with its identifier and its short text, each
decision a diamond with its question, and each branch an arrow with its label.

The conversion has no web dependency. Two callers use it:

- The audit page (`web/audit_diagram.py`) passes the translation function and the
  node texts of the interface language, and marks the steps of the turn.
- `docs/workflow_diagrams.md` (`diagrams.py`) uses the defaults: the English texts
  of `node_texts.NODE_TEXTS`, with no translation and no marks.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pydantic_graph import Step

from domain.chat.workflow.node_texts import NODE_TEXTS

# The lines of `graph.render()`: a step (the identifier alone, indented), a decision
# (`state after_Q3 <<choice>>`), the first line of the note that holds its question
# (`note right of after_Q3`), and an edge (`after_Q3 --> R1: names a blacklisted term`).
_STEP = re.compile(r"^\s+(\w+)$")
_CHOICE = re.compile(r"^\s*state (\w+) <<choice>>$")
_NOTE = re.compile(r"^\s*note right of (\w+)$")
_EDGE = re.compile(r"^\s*(\S+) --> (\S+)(?:: (.+))?$")
# The ids of the start and the end of a flowchart. `[*]` is their name in `graph.render()`.
START, END = "wf_start", "wf_end"


def english_text(node_id: str) -> str:
    """The English short text of a node; '' when the node has none."""
    return NODE_TEXTS.get(node_id, "")


def _unchanged(text: str) -> str:
    return text


def steps(graph) -> list[str]:
    """The identifiers of the steps of a graph, without its decisions."""
    return [node_id for node_id, node in graph.nodes.items() if isinstance(node, Step)]


def quoted(text: str) -> str:
    """A text as a quoted Mermaid label: double quotes become single quotes."""
    return '"' + text.replace('"', "'") + '"'


def label(node_id: str, node_text: Callable[[str], str] = english_text) -> str:
    """The label of a node: its identifier and its short text, without double quotes."""
    text = node_text(node_id).replace('"', "'")
    return f"{node_id} · {text}" if text else node_id


def flowchart(
    rendered: str,
    translate: Callable[[str], str] = _unchanged,
    node_text: Callable[[str], str] = english_text,
) -> str:
    """The Mermaid flowchart of the text of `graph.render()`, a `stateDiagram-v2`.

    Each step becomes a box with its identifier and its short text (`node_text`), each
    decision a diamond with its question (the note of the decision), and each edge an
    arrow with its label. `translate` receives each question and each label. The start
    is a small filled circle, the end a framed circle.
    """
    # Each node in the order of `graph.render()`: a step maps to None, a decision to its question.
    nodes: dict[str, str | None] = {}
    edges: list[str] = []
    lines = iter(rendered.splitlines())
    for line in lines:
        if found := _NOTE.match(line):
            nodes[found.group(1)] = next(lines).strip()
            next(lines)                                     # the line `end note`
        elif found := _CHOICE.match(line):
            nodes[found.group(1)] = ""
        elif found := _EDGE.match(line):
            source, target, text = found.groups()
            source = START if source == "[*]" else source
            target = END if target == "[*]" else target
            arrow = f"-->|{quoted(translate(text))}|" if text else "-->"
            edges.append(f"  {source} {arrow} {target}")
        elif found := _STEP.match(line):
            nodes[found.group(1)] = None
    shapes = [
        f"  {node}[{quoted(label(node, node_text))}]" if question is None
        else f"  {node}{{{quoted(translate(question) if question else '')}}}"
        for node, question in nodes.items()
    ]
    head = ["flowchart TD", f"  {START}@{{ shape: sm-circ }}", f"  {END}@{{ shape: fr-circ }}"]
    return "\n".join(head + shapes + [""] + edges)
