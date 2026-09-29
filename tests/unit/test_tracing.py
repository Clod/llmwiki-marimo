"""Unit tests for the opt-in OpenTelemetry tracing module (base/domain/tracing.py).

Every test drives the module through its public API (``enabled``, ``root``,
``node``, ``mark``, ``Node.set``, ``Node.payload``, ``wrap_openai``) and reads
back the span file the module writes, ``<workspace>/.llmwiki/traces/spans.jsonl``,
rather than reaching into private state. No network calls.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from domain import tracing


def _read_spans(workspace: Path) -> list[dict]:
    path = workspace / ".llmwiki" / "traces" / tracing.SPAN_FILE
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _by_name(spans: list[dict], name: str) -> dict:
    return next(s for s in spans if s["name"] == name)


# ── A minimal OpenAI-shaped fake, with usage — FakeLLMClient has none ────────

class _Usage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _Response:
    def __init__(self, content: str, model: str) -> None:
        self.choices = [_Choice(content)]
        self.usage = _Usage(10, 5)
        self.model = model


class _Completions:
    def create(self, *, model: str, messages: list, **kwargs) -> _Response:
        return _Response("HELLO", model)


class _Chat:
    def __init__(self) -> None:
        self.completions = _Completions()


class _FakeClient:
    def __init__(self) -> None:
        self.chat = _Chat()


# ── Disabled: a true no-op ───────────────────────────────────────────────────

def test_disabled_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv(tracing.ENV_VAR, raising=False)
    assert tracing.enabled() is False
    with tracing.root("turn", tmp_path, question="hola"):
        with tracing.node("Q2", tracing.READING):
            pass
    assert not (tmp_path / ".llmwiki" / "traces" / tracing.SPAN_FILE).exists()


# ── root() writes to the workspace's span file ───────────────────────────────

def test_root_writes_to_workspace_span_file(monkeypatch, tmp_path):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    with tracing.root("turn", tmp_path, question="hola"):
        pass
    spans = _read_spans(tmp_path)
    assert len(spans) == 1
    root = spans[0]
    assert root["name"] == "turn"
    assert root["parent_id"] is None
    assert root["attributes"]["llmwiki.question"] == "hola"


# ── Child spans share the root's trace id and name their parent ─────────────

def test_child_span_shares_trace_id_and_names_parent(monkeypatch, tmp_path):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    with tracing.root("turn", tmp_path):
        with tracing.node("Q2", tracing.READING):
            pass
    spans = _read_spans(tmp_path)
    root = _by_name(spans, "turn")
    child = _by_name(spans, "Q2")
    assert child["context"]["trace_id"] == root["context"]["trace_id"]
    assert child["parent_id"] == root["context"]["span_id"]


# ── node() sets llmwiki.node and llmwiki.diagram ─────────────────────────────

def test_node_sets_node_and_diagram_attributes(monkeypatch, tmp_path):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    with tracing.root("ingest", tmp_path):
        with tracing.node("I4", tracing.WRITING):
            pass
    i4 = _by_name(_read_spans(tmp_path), "I4")
    assert i4["attributes"]["llmwiki.node"] == "I4"
    assert i4["attributes"]["llmwiki.diagram"] == "writing"


def test_mark_records_a_zero_work_decision(monkeypatch, tmp_path):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    with tracing.root("ingest", tmp_path):
        tracing.mark("I9", tracing.WRITING, answer=True)
    i9 = _by_name(_read_spans(tmp_path), "I9")
    assert i9["attributes"]["llmwiki.answer"] is True
    assert i9["attributes"]["llmwiki.diagram"] == "writing"


# ── Node.set: lists of strings pass through, dicts become JSON strings ──────

def test_node_set_converts_lists_and_dicts(monkeypatch, tmp_path):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    with tracing.root("turn", tmp_path):
        with tracing.node("Q5", tracing.READING) as n:
            n.set(matched=["plazo_fijo", "uva"], plan={"tier": "curado", "verify": False})
    q5 = _by_name(_read_spans(tmp_path), "Q5")
    attrs = q5["attributes"]
    assert attrs["llmwiki.matched"] == ["plazo_fijo", "uva"]
    assert json.loads(attrs["llmwiki.plan"]) == {"tier": "curado", "verify": False}


# ── Node.payload: content-addressed file + three attributes ────────────────

def test_payload_writes_content_addressed_file(monkeypatch, tmp_path):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    monkeypatch.delenv(tracing.CAPTURE_ENV_VAR, raising=False)
    with tracing.root("ingest", tmp_path):
        with tracing.node("I4", tracing.WRITING) as n:
            n.payload("extracted_text", "hello world", channel="extracted_text", ext="txt")
    i4 = _by_name(_read_spans(tmp_path), "I4")
    attrs = i4["attributes"]
    sha = hashlib.sha256(b"hello world").hexdigest()
    assert attrs["llmwiki.extracted_text.sha256"] == sha
    assert attrs["llmwiki.extracted_text.bytes"] == len(b"hello world")
    assert attrs["llmwiki.extracted_text.ref"] == f"payloads/{sha}.txt"
    payload_path = tmp_path / ".llmwiki" / "traces" / "payloads" / f"{sha}.txt"
    assert payload_path.read_text(encoding="utf-8") == "hello world"


def test_payload_channel_off_still_records_hash_and_size(monkeypatch, tmp_path):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    monkeypatch.setenv(tracing.CAPTURE_ENV_VAR, "none")
    with tracing.root("ingest", tmp_path):
        with tracing.node("I4", tracing.WRITING) as n:
            n.payload("extracted_text", "hello world", channel="extracted_text")
    i4 = _by_name(_read_spans(tmp_path), "I4")
    attrs = i4["attributes"]
    sha = hashlib.sha256(b"hello world").hexdigest()
    assert attrs["llmwiki.extracted_text.sha256"] == sha
    assert attrs["llmwiki.extracted_text.bytes"] == len(b"hello world")
    assert "llmwiki.extracted_text.ref" not in attrs
    assert not (tmp_path / ".llmwiki" / "traces" / "payloads").exists()


# ── root() inside an open span reuses it ────────────────────────────────────

def test_root_inside_open_span_is_reused(monkeypatch, tmp_path):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    with tracing.root("ingest", tmp_path, files=2):
        with tracing.root("ingest", tmp_path):
            pass
    spans = _read_spans(tmp_path)
    roots = [s for s in spans if s["name"] == "ingest"]
    assert len(roots) == 1
    assert roots[0]["parent_id"] is None


# ── Two workspaces in one process write to their own files ─────────────────

def test_two_workspaces_write_to_their_own_files(monkeypatch, tmp_path):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    ws_a = tmp_path / "a"
    ws_b = tmp_path / "b"
    with tracing.root("turn", ws_a, question="a"):
        pass
    with tracing.root("turn", ws_b, question="b"):
        pass
    spans_a = _read_spans(ws_a)
    spans_b = _read_spans(ws_b)
    assert len(spans_a) == 1 and spans_a[0]["attributes"]["llmwiki.question"] == "a"
    assert len(spans_b) == 1 and spans_b[0]["attributes"]["llmwiki.question"] == "b"


# ── wrap_openai ───────────────────────────────────────────────────────────

def test_wrap_openai_disabled_returns_client_unchanged(monkeypatch):
    monkeypatch.delenv(tracing.ENV_VAR, raising=False)
    client = _FakeClient()
    assert tracing.wrap_openai(client) is client


def test_wrap_openai_is_idempotent(monkeypatch):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    fake = _FakeClient()
    once = tracing.wrap_openai(fake)
    twice = tracing.wrap_openai(once)
    assert once is twice


def test_wrap_openai_records_chat_span_with_token_usage(monkeypatch, tmp_path):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    fake = _FakeClient()
    wrapped = tracing.wrap_openai(fake)
    with tracing.root("ingest", tmp_path):
        resp = wrapped.chat.completions.create(model="gpt-fake", messages=[{"role": "user", "content": "hi"}])
    assert resp.choices[0].message.content == "HELLO"  # real response, untouched
    call = _by_name(_read_spans(tmp_path), "chat gpt-fake")
    attrs = call["attributes"]
    assert attrs["gen_ai.request.model"] == "gpt-fake"
    assert attrs["gen_ai.usage.input_tokens"] == 10
    assert attrs["gen_ai.usage.output_tokens"] == 5


@pytest.mark.parametrize(
    "value, expected",
    [("1", True), ("true", True), ("", False), ("0", False), ("false", False),
     ("FALSE", False), ("no", False), ("off", False), ("Off", False)],
)
def test_enabled_accepts_the_project_falsy_values(monkeypatch, value, expected):
    monkeypatch.setenv("WIKI_TRACE", value)
    assert tracing.enabled() is expected
