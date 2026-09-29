"""Tests for the span-file renderer (scripts/render_trace.py).

Builds a small, real span file with `domain.tracing` (two chat turns of one
conversation, plus one ingest with a document span) and checks the timeline
`render_trace.py` prints for each CLI option.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from domain import tracing

_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "render_trace.py"


def _load():
    spec = importlib.util.spec_from_file_location("render_trace", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


render_trace = _load()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A workspace with two turns of one conversation, and one ingest."""
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    monkeypatch.delenv(tracing.CAPTURE_ENV_VAR, raising=False)

    with tracing.root(
        "turn", tmp_path, conversation_id="c1", turn=1, mode="pre-retrieval", question="q1",
    ):
        with tracing.node("Q2", tracing.READING):
            pass
        with tracing.node("Q13", tracing.READING) as q13:
            q13.payload("prompt", "the injected prompt", channel="prompts", ext="txt")

    with tracing.root(
        "turn", tmp_path, conversation_id="c1", turn=2, mode="pre-retrieval", question="q2",
    ):
        pass

    with tracing.root("ingest", tmp_path):
        with tracing.node("document", tracing.WRITING, filename="a.pdf") as doc:
            doc.set(document_id="doc-1", relative_path="sources/a.pdf")
            with tracing.node("I4", tracing.WRITING):
                pass

    return tmp_path


def _run(monkeypatch, capsys, argv: list[str]) -> str:
    monkeypatch.setattr(sys, "argv", ["render_trace.py", *argv])
    render_trace.main()
    return capsys.readouterr().out


def _span_lines(workspace: Path) -> list[dict]:
    path = workspace / ".llmwiki" / "traces" / tracing.SPAN_FILE
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── Default: every trace, spans indented by depth ───────────────────────────

def test_default_renders_every_trace_indented(workspace, monkeypatch, capsys):
    out = _run(monkeypatch, capsys, [str(workspace)])
    assert out.count("TRACE ") == 3  # two turns + one ingest
    assert "turn (" in out
    assert "ingest (" in out
    assert "  Q2 (" in out  # indented one level under its root
    assert "    document (" not in out  # document is a child of ingest, one level
    assert "  document (" in out


# ── --trace: only that trace_id ──────────────────────────────────────────────

def test_trace_filters_to_one_trace(workspace, monkeypatch, capsys):
    spans = _span_lines(workspace)
    first_turn_trace_id = next(s for s in spans if s["name"] == "turn")["context"]["trace_id"]
    out = _run(monkeypatch, capsys, [str(workspace), "--trace", first_turn_trace_id])
    assert out.count("TRACE ") == 1
    assert "ingest" not in out


# ── --conversation: every turn, in turn order ────────────────────────────────

def test_conversation_orders_turns(workspace, monkeypatch, capsys):
    out = _run(monkeypatch, capsys, [str(workspace), "--conversation", "c1"])
    assert out.count("TRACE ") == 2
    assert out.index("llmwiki.question=q1") < out.index("llmwiki.question=q2")


# ── --doc: the spans under the document span only ────────────────────────────

def test_doc_renders_the_document_subtree(workspace, monkeypatch, capsys):
    out = _run(monkeypatch, capsys, [str(workspace), "--doc", "doc-1"])
    assert out.startswith("document (")
    assert "I4 (" in out
    assert "turn" not in out
    assert "TRACE " not in out  # --doc prints the subtree only, no trace header


# ── --show: inlines the payload file ─────────────────────────────────────────

def test_show_inlines_the_payload(workspace, monkeypatch, capsys):
    out = _run(monkeypatch, capsys, [str(workspace), "--show", "prompts"])
    assert "--- prompt ---" in out
    assert "the injected prompt" in out


def test_show_omitted_does_not_inline(workspace, monkeypatch, capsys):
    out = _run(monkeypatch, capsys, [str(workspace)])
    assert "the injected prompt" not in out


# ── Omitted attributes never appear ──────────────────────────────────────────

def test_workspace_node_and_diagram_attributes_are_omitted(workspace, monkeypatch, capsys):
    out = _run(monkeypatch, capsys, [str(workspace)])
    assert "llmwiki.workspace=" not in out
    assert "llmwiki.node=" not in out
    assert "llmwiki.diagram=" not in out
