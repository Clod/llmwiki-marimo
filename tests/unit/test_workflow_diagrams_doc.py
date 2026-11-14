"""`docs/workflow_diagrams.md` is the output of `scripts/workflow_diagrams.py`.

The document holds the flowchart of each chat workflow, converted from its graph
(`diagrams.markdown`). A step, a decision or a branch changed in a workflow
without regenerating the file fails here.
"""

from pathlib import Path

from domain.chat.workflow import diagrams

DOC = Path(__file__).resolve().parents[2] / "docs" / "workflow_diagrams.md"


def test_the_generated_document_matches_the_graphs():
    assert DOC.read_text(encoding="utf-8") == diagrams.markdown()


def test_the_document_has_one_flowchart_per_workflow():
    text = diagrams.markdown()
    assert text.count("```mermaid\nflowchart TD\n") == len(diagrams.WORKFLOWS) == 4
    assert "classDef visited" not in text
