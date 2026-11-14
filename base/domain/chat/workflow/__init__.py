"""The workflows of the chat checks, declared with pydantic-graph `GraphBuilder`.

The pre-retrieval mode and the strict mode each have two workflows: an input
workflow (`input_checks`), which builds the history and checks the question,
and an output workflow (`output_checks`), which checks the answer. The call to
the model runs between the two workflows of a mode, outside both. The streaming
mode has no workflows.

Each node of the four workflows is a step function of the catalog (`catalog`),
written once. Each workflow registers the functions that it uses and declares
their codes (`PRE_RETRIEVAL_NODES`, `STRICT_NODES`). `table` writes, from those
lists, the table of `docs/workflow_nodes.md`. `flowchart` converts a graph into
a Mermaid flowchart, and `diagrams` writes, with it, the flowcharts of
`docs/workflow_diagrams.md`.
"""
