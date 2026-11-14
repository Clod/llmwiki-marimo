"""The diagrams of a turn for the audit page of a conversation.

A turn of the pre-retrieval or the strict mode shows three diagrams: the input
workflow, the call to the model and the output workflow. The input and output
diagrams are the Mermaid text of the graphs (`graph.render()`), so they show
the steps that run. Each step carries its short text (`audit_nodes.text`), in
the interface language. The diagram of the call to the model is a chain built
from the path of the turn: the node of the call (Q13 to Q16, or T1) and each
tool node (K1 to K10) in the order of the path.

A turn of the streaming mode shows the overview of `docs/query_nodes.md` (the
first Mermaid block of the file), read from the file each time. Each diagram
comes with a style that marks the nodes the turn went through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic_graph import Step

from domain.chat.workflow import input_checks, output_checks
from web import audit_nodes
from web.i18n import N_

SOURCE = Path(__file__).resolve().parent.parent / "docs" / "query_nodes.md"

# The input and the output graph of each mode with workflows. The streaming mode has none.
WORKFLOW_GRAPHS = {
    "pre-retrieval": (input_checks.graph, output_checks.graph),
    "strict": (input_checks.strict_graph, output_checks.strict_graph),
}

# The nodes that call the model between the two workflows of a mode.
MODEL_CALLS = (*input_checks.AGENT_NODES, "T1")

VISITED_STYLE = "classDef visited fill:#fde047,stroke:#a16207,stroke-width:3px,color:#1f2937"

TITLES = {
    "input": N_("Input workflow"),
    "model": N_("Model call"),
    "output": N_("Output workflow"),
}

_BLOCK = re.compile(r"^```mermaid\n(.*?)^```", re.S | re.M)
# A node line: `    Q["Q1 · question"]`, `C2{"C2 · ..."}`, `IDX[("DB · ...")]`. The first group is
# the id Mermaid uses, the second the id of the node in the path, which opens the label.
_NODE = re.compile(r'^\s*(\w+)\s*(?:\[\(|\[|\{|\()\s*"([A-Za-z]\w*) ·', re.M)
# A step line of `graph.render()`: the identifier alone, indented.
_STEP = re.compile(r"^(\s+)(\w+)$")
_TOOL = re.compile(r"K\d+")


@dataclass(frozen=True)
class Diagram:
    """One diagram of a turn: its title (none for the overview) and its Mermaid text.
    `text` is empty when the turn did not call the model."""

    title: str | None
    text: str


def blocks() -> list[str]:
    """The Mermaid blocks of `docs/query_nodes.md`, in order; none when the file cannot be read."""
    try:
        return _BLOCK.findall(SOURCE.read_text(encoding="utf-8"))
    except OSError:
        return []


def mark_visited(text: str, path_ids) -> str:
    """The diagram with the nodes of the path styled: only the ids the block defines."""
    labelled = {node: mermaid for mermaid, node in _NODE.findall(text)}
    marked = sorted({labelled[i] for i in path_ids if i in labelled})
    return _with_style(text, marked)


def _with_style(text: str, marked) -> str:
    if not marked:
        return text
    return f"{text.rstrip()}\n    {VISITED_STYLE}\n    class {','.join(marked)} visited\n"


def _label(node_id: str) -> str:
    """The label of a node: its identifier and its short text, without double quotes."""
    text = audit_nodes.text(node_id).replace('"', "'")
    return f"{node_id} · {text}" if text else node_id


def steps(graph) -> list[str]:
    """The identifiers of the steps of a graph, without its decisions."""
    return [node_id for node_id, node in graph.nodes.items() if isinstance(node, Step)]


def workflow_diagram(graph, path_ids) -> str:
    """The Mermaid text of a graph, each step with its label and the steps of the path marked."""
    own = set(steps(graph))

    def labelled(line: str) -> str:
        found = _STEP.match(line)
        if not found or found.group(2) not in own:
            return line
        indent, node_id = found.groups()
        return f'{indent}state "{_label(node_id)}" as {node_id}'

    text = "\n".join(labelled(line) for line in graph.render().splitlines())
    return _with_style(text, sorted(own & set(path_ids)))


def model_call_chain(path_ids) -> list[str]:
    """The node of the call to the model and the tool nodes that follow it, in the order of
    the path; empty when the turn did not call the model."""
    path_ids = list(path_ids)
    start = next((n for n, node in enumerate(path_ids) if node in MODEL_CALLS), None)
    if start is None:
        return []
    return [path_ids[start], *(node for node in path_ids[start + 1:] if _TOOL.fullmatch(node))]


def model_call_diagram(path_ids) -> str:
    """The chain of the call to the model as a Mermaid state diagram, every link marked.
    Each link has its own Mermaid id, because a tool can be called more than once."""
    chain = model_call_chain(path_ids)
    if not chain:
        return ""
    ids = [f"n{n}" for n in range(len(chain))]
    lines = ["stateDiagram-v2"]
    lines += [f'  state "{_label(node)}" as {i}' for i, node in zip(ids, chain)]
    lines.append("")
    lines += [f"  {a} --> {b}" for a, b in zip(["[*]", *ids], ids)]
    return _with_style("\n".join(lines), ids)


def diagrams_for(mode: str, path_ids) -> list[Diagram]:
    """The diagrams of a turn, with its path marked: the input workflow, the call to the model
    and the output workflow for a mode with workflows; the overview for the streaming mode."""
    path_ids = list(path_ids)
    if mode in WORKFLOW_GRAPHS:
        entry, exit_ = WORKFLOW_GRAPHS[mode]
        return [
            Diagram(TITLES["input"], workflow_diagram(entry, path_ids)),
            Diagram(TITLES["model"], model_call_diagram(path_ids)),
            Diagram(TITLES["output"], workflow_diagram(exit_, path_ids)),
        ]
    found = blocks()
    return [Diagram(None, mark_visited(found[0], path_ids))] if found else []
