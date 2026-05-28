"""Unit tests for the opt-in ingestion trace (base/domain/ingestion/trace.py).

Covers: disabled no-op path, channel resolution + toggling, sha/size always
present, transparent client proxy, meta header shape, and contextvar tagging.
No API calls — uses FakeLLMClient.
"""

import json
from pathlib import Path

import pytest

from domain.ingestion import trace
from tests.helpers.fake_llm import FakeLLMClient


def _read(run_dir: Path) -> list[dict]:
    lines = (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.fixture(autouse=True)
def _clear_active():
    """Ensure the module-level active-run contextvar never leaks between tests."""
    trace._active.set(None)
    yield
    trace._active.set(None)


# ── Channel resolution ───────────────────────────────────────────────────────

def test_channels_default_is_all(monkeypatch):
    monkeypatch.delenv("WIKI_TRACE_CAPTURE", raising=False)
    assert trace._channels() == trace.CHANNELS


def test_channels_none(monkeypatch):
    monkeypatch.setenv("WIKI_TRACE_CAPTURE", "none")
    assert trace._channels() == frozenset()


def test_channels_explicit_list(monkeypatch):
    monkeypatch.setenv("WIKI_TRACE_CAPTURE", "prompts, chunks")
    assert trace._channels() == frozenset({"prompts", "chunks"})


def test_channels_ignores_unknown(monkeypatch):
    monkeypatch.setenv("WIKI_TRACE_CAPTURE", "prompts,bogus")
    assert trace._channels() == frozenset({"prompts"})


# ── Disabled path is a true no-op ────────────────────────────────────────────

def test_disabled_returns_null_and_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("WIKI_TRACE", raising=False)
    tracer, created = trace.active_or_start(tmp_path, "db")
    assert tracer is trace.NULL
    assert created is False

    client = FakeLLMClient()
    assert tracer.wrap(client) is client  # client returned unchanged
    with tracer.stage("x"):
        tracer.artifact("markdown", "n", "content")
    assert not (tmp_path / ".llmwiki" / "traces").exists()


# ── Enabled: meta header + structure ─────────────────────────────────────────

def test_meta_header_is_first_line_with_join_map(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_TRACE", "1")
    monkeypatch.setenv("WIKI_TRACE_CAPTURE", "none")
    tracer = trace.IngestionTracer(tmp_path, "/db/index.db")
    tracer.finish()

    events = _read(tracer.run_dir)
    meta = events[0]
    assert meta["event"] == "meta"
    assert meta["schema_version"] == trace.SCHEMA_VERSION
    assert meta["db_path"] == "/db/index.db"
    assert meta["db_join_map"]["document_id"] == "documents.id"
    assert meta["capture"] == []
    assert events[-1]["event"] == "run_end"


# ── Transparent proxy captures every create() call ───────────────────────────

def test_proxy_records_llm_call_and_is_transparent(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_TRACE", "1")
    monkeypatch.setenv("WIKI_TRACE_CAPTURE", "none")
    tracer = trace.IngestionTracer(tmp_path, None)

    fake = FakeLLMClient(response_content="HELLO")
    wrapped = tracer.wrap(fake)

    resp = wrapped.chat.completions.create(
        model="m", messages=[{"role": "user", "content": "hi"}], temperature=0.2
    )
    # Transparency: real response object returned, real client saw the call.
    assert resp.choices[0].message.content == "HELLO"
    assert fake.calls and fake.calls[0]["model"] == "m"

    tracer.finish()
    calls = [e for e in _read(tracer.run_dir) if e["event"] == "llm_call"]
    assert len(calls) == 1
    call = calls[0]
    assert call["model"] == "m"
    assert call["params"] == {"temperature": 0.2}  # messages + model excluded
    # Channel off → no sidecar ref, but sha + size still present.
    assert call["prompt_ref"] is None
    assert call["response_ref"] is None
    assert call["prompt_bytes"] > 0
    assert len(call["response_sha256"]) == 64


def test_wrap_is_idempotent_for_same_tracer(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_TRACE", "1")
    tracer = trace.IngestionTracer(tmp_path, None)
    fake = FakeLLMClient()
    once = tracer.wrap(fake)
    twice = tracer.wrap(once)
    assert once is twice  # no double-wrapping (matters for the batch path)
    tracer.finish()


# ── Sidecars: channel on writes files, off does not ──────────────────────────

def test_channel_on_writes_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_TRACE", "1")
    monkeypatch.setenv("WIKI_TRACE_CAPTURE", "markdown")
    tracer = trace.IngestionTracer(tmp_path, None)
    tracer.artifact("markdown", "summary", "# Title\n\nbody", ext="md",
                    relative_path="wiki/summaries/x.md")
    tracer.finish()

    art = [e for e in _read(tracer.run_dir) if e["event"] == "artifact"][0]
    assert art["channel"] == "markdown"
    assert art["ref"] is not None
    sidecar = tracer.run_dir / art["ref"]
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8") == "# Title\n\nbody"
    assert art["bytes"] == len("# Title\n\nbody".encode("utf-8"))


def test_channel_off_records_hash_but_no_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_TRACE", "1")
    monkeypatch.setenv("WIKI_TRACE_CAPTURE", "none")
    tracer = trace.IngestionTracer(tmp_path, None)
    tracer.artifact("extracted_text", "extracted_text", "lots of text")
    tracer.finish()

    art = [e for e in _read(tracer.run_dir) if e["event"] == "artifact"][0]
    assert art["ref"] is None
    assert len(art["sha256"]) == 64
    assert art["bytes"] == len("lots of text".encode("utf-8"))
    assert not tracer.payload_dir.exists()


# ── Correlation: document + stage tagging ────────────────────────────────────

def test_document_and_stage_tagging(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_TRACE", "1")
    monkeypatch.setenv("WIKI_TRACE_CAPTURE", "none")
    tracer = trace.IngestionTracer(tmp_path, None)

    tracer.document_start("doc-123", "f.pdf", "sources/f.pdf")
    fake = tracer.wrap(FakeLLMClient())
    with tracer.stage("structured_extraction"):
        fake.chat.completions.create(model="m", messages=[{"role": "user", "content": "x"}])
    tracer.document_end(status="ok")
    tracer.finish()

    events = _read(tracer.run_dir)
    call = [e for e in events if e["event"] == "llm_call"][0]
    assert call["document_id"] == "doc-123"
    assert call["relative_path"] == "sources/f.pdf"
    assert call["stage"] == "structured_extraction"

    end = [e for e in events if e["event"] == "document_end"][0]
    assert end["status"] == "ok"
    # contextvars are reset after the document/stage close.
    assert trace._current_doc.get() is None
    assert trace._current_stage.get() is None


# ── active_or_start run ownership (batch semantics) ───────────────────────────

def test_active_or_start_reuses_existing_run(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_TRACE", "1")
    monkeypatch.setenv("WIKI_TRACE_CAPTURE", "none")
    outer, created_outer = trace.active_or_start(tmp_path, None)
    inner, created_inner = trace.active_or_start(tmp_path, None)
    assert created_outer is True
    assert created_inner is False
    assert inner is outer  # the nested caller joins the same run
    outer.finish()


# ── End-to-end: a real ingest produces a trace that joins against the DB ──────

_EXTRACTION_JSON = json.dumps({
    "document_summary": "A classic fairy tale about a princess and an evil queen.",
    "concepts": [
        {"name": "Snow White", "category": "entity", "insight": "The protagonist princess"},
        {"name": "Evil Queen", "category": "entity", "insight": "The antagonist"},
    ],
})


def test_end_to_end_ingest_trace_matches_db(monkeypatch, tmp_workspace):
    monkeypatch.setenv("WIKI_TRACE", "1")
    monkeypatch.delenv("WIKI_TRACE_CAPTURE", raising=False)  # default: all channels

    from domain.ingestion.pipeline import ingest_file
    from domain.tools.db import get_connection
    from tests.helpers.fake_llm import FakeLLMClient

    pdf = Path(__file__).parent.parent / "fixtures" / "pdfs" / "Blancanieves.pdf"
    # call 1 = structured extraction (JSON); later calls = concept/overview markdown.
    fake = FakeLLMClient(responses=[_EXTRACTION_JSON, "# Concept\n", "# Overview\n"])

    result = ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace,
                         fake, "fake-model")
    assert result.status == "ingested"

    traces_dir = tmp_workspace.workspace / ".llmwiki" / "traces"
    runs = list(traces_dir.iterdir())
    assert len(runs) == 1, "exactly one trace run per ingest"
    events = _read(runs[0])

    # Structure: meta header first, run bookends, document scope.
    assert events[0]["event"] == "meta"
    assert events[0]["capture"] == sorted(trace.CHANNELS)
    assert events[-1]["event"] == "run_end"
    kinds = [e["event"] for e in events]
    assert "document_start" in kinds and "document_end" in kinds

    # Every pipeline stage appears.
    stages = {e.get("stage") for e in events if e["event"] == "stage_start"}
    assert {"extract", "chunk", "structured_extraction", "concepts",
            "summary", "overview"} <= stages

    # One llm_call per FakeLLMClient call, all tagged to the document.
    llm_calls = [e for e in events if e["event"] == "llm_call"]
    assert len(llm_calls) == len(fake.calls) >= 1
    doc_ids = {e["document_id"] for e in llm_calls}
    assert doc_ids == {result.doc_id}

    # Intermediate-data artifacts captured with sidecars (default = all channels).
    artifacts = [e for e in events if e["event"] == "artifact"]
    channels = {a["channel"] for a in artifacts}
    assert {"extracted_text", "chunks", "markdown"} <= channels
    for a in artifacts:
        assert (runs[0] / a["ref"]).exists()  # sidecar written

    # Correlation: the trace's document_id is the real source row in the DB.
    with get_connection(tmp_workspace.db_path) as conn:
        row = conn.execute(
            "SELECT id, relative_path FROM documents WHERE id=? AND source_kind='source'",
            (result.doc_id,),
        ).fetchone()
    assert row is not None
    assert row["relative_path"] == "sources/Blancanieves.pdf"

    # No credential leaked into the trace text.
    raw = (runs[0] / "trace.jsonl").read_text(encoding="utf-8")
    assert "api_key" not in raw.lower()
