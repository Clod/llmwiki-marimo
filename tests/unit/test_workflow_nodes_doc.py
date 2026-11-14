"""The node lists that each chat workflow declares match the graph it builds.

Each workflow module declares the nodes of the catalog that each of its graphs
registers (`PRE_RETRIEVAL_NODES`, `STRICT_NODES`) and the steps that are not in
the catalog (`OWN_STEPS`). The steps of a graph that are not own steps are
exactly the declared list, and every declared code is a key of
`catalog.CATALOG`. `docs/workflow_nodes.md` is the table generated from those
lists (`table.markdown`).
"""

from pathlib import Path

import pytest
from pydantic_graph import Step

from domain.chat.workflow import catalog, input_checks, output_checks, table

WORKFLOWS = [
    pytest.param(input_checks, input_checks.graph, input_checks.PRE_RETRIEVAL_NODES, id="input, pre-retrieval"),
    pytest.param(input_checks, input_checks.strict_graph, input_checks.STRICT_NODES, id="input, strict"),
    pytest.param(output_checks, output_checks.graph, output_checks.PRE_RETRIEVAL_NODES, id="output, pre-retrieval"),
    pytest.param(output_checks, output_checks.strict_graph, output_checks.STRICT_NODES, id="output, strict"),
]


@pytest.mark.parametrize(("module", "graph", "declared"), WORKFLOWS)
def test_the_declared_list_is_the_catalog_steps_of_the_graph(module, graph, declared):
    steps = {node_id for node_id, node in graph.nodes.items() if isinstance(node, Step)}
    assert len(declared) == len(set(declared))
    assert steps - set(module.OWN_STEPS) == set(declared)


@pytest.mark.parametrize(("module", "graph", "declared"), WORKFLOWS)
def test_every_declared_code_is_in_the_catalog(module, graph, declared):
    assert [code for code in declared if code not in catalog.CATALOG] == []


def test_the_generated_document_matches_the_declared_lists():
    """`docs/workflow_nodes.md` is the output of `scripts/workflow_nodes.py`: a node
    added to a workflow without regenerating the file fails here."""
    doc = Path(__file__).resolve().parents[2] / "docs" / "workflow_nodes.md"
    assert doc.read_text(encoding="utf-8") == table.markdown()
