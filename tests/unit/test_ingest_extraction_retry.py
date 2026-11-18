"""The extraction decision of the ingestion workflow (`domain/ingestion/workflow.py`): a
reply that is not valid JSON is retried once (I_CONCEPTS_RETRY), and a second invalid reply
fails the run with no page written. The model is `FakeLLMClient`; the source is markdown."""

from domain import tracing
from domain.ingestion.pipeline import ingest_file
from domain.tools.db import get_connection
from tests.helpers.workspace import tmp_workspace  # noqa: F401 — the fixture
from tests.unit.test_ingest_characterization import CONCEPT, EXTRACTION, OVERVIEW, SOURCE

CUT = '{"document_summary": "Un documento", "concepts": [{"na'


def _ingest(ws, responses):
    path = ws.workspace / "sources" / "cauciones.md"
    path.write_text(SOURCE, encoding="utf-8")
    ws.llm.responses = list(responses)
    lines = []
    with tracing.collect() as trace:
        result = ingest_file(path, ws.db_path, ws.workspace, ws.llm, "fake", progress_cb=lines.append,
                             language="es")
    return result, [entry["node"] for entry in trace], [str(line) for line in lines]


def test_a_valid_reply_is_not_retried(tmp_workspace):  # noqa: F811
    result, nodes, _ = _ingest(tmp_workspace, [EXTRACTION, CONCEPT, OVERVIEW])
    assert result.status == "ingested" and "I_CONCEPTS_RETRY" not in nodes
    assert len(tmp_workspace.llm.calls) == 3      # extraction, concept page, overview


def test_an_invalid_reply_and_a_valid_retry_write_the_pages_of_the_retry(tmp_workspace):  # noqa: F811
    result, nodes, lines = _ingest(tmp_workspace, [CUT, EXTRACTION, CONCEPT, OVERVIEW])
    assert result.status == "ingested"
    assert nodes[nodes.index("I_CONCEPTS"):nodes.index("I_CONCEPTS") + 3] == [
        "I_CONCEPTS", "I_CONCEPTS_RETRY", "I_CONCEPT_PAGES"]
    assert (tmp_workspace.workspace / "wiki" / "concepts" / "caucion-bursatil.md").exists()
    summary = (tmp_workspace.workspace / "wiki" / "summaries" / "cauciones.md").read_text(encoding="utf-8")
    assert "Un documento sobre la caución bursátil." in summary and CUT not in summary
    retry = tmp_workspace.llm.calls[1]["messages"]
    assert retry[-2] == {"role": "assistant", "content": CUT}
    assert "no es un JSON válido" in retry[-1]["content"]
    assert any("asking the model again" in line for line in lines)


def test_two_invalid_replies_fail_the_run_with_no_page(tmp_workspace):  # noqa: F811
    result, nodes, lines = _ingest(tmp_workspace, [CUT, "Esto tampoco es JSON."])
    assert result.status == "failed" and "not valid JSON" in result.message
    assert nodes[-2:] == ["I_CONCEPTS_RETRY", "E_ROLLED_BACK"]
    assert not (tmp_workspace.workspace / "wiki" / "summaries" / "cauciones.md").exists()
    with get_connection(tmp_workspace.db_path) as conn:
        row = conn.execute("SELECT status, error_message FROM documents WHERE relative_path = 'sources/cauciones.md'").fetchone()
    assert row["status"] == "failed" and "not valid JSON" in row["error_message"]
    assert any(line.startswith("❌") and "not valid JSON" in line for line in lines)
