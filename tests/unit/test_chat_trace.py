"""Unit tests for the opt-in read-app chat trace (base/domain/chat/trace.py)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from domain.chat import trace as chat_trace


# ── Fake pydantic-ai message parts ──────────────────────────────────────────

def _call(name: str, args: object) -> SimpleNamespace:
    return SimpleNamespace(part_kind="tool-call", tool_name=name, args=args)


def _ret(name: str, content: object) -> SimpleNamespace:
    return SimpleNamespace(part_kind="tool-return", tool_name=name, content=content)


def _text(content: str) -> SimpleNamespace:
    return SimpleNamespace(part_kind="text", content=content)


def _msg(*parts: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(parts=list(parts))


# ── enabled() env logic ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,expected",
    [(None, False), ("", False), ("0", False), ("false", False), ("False", False),
     ("1", True), ("true", True), ("yes", True)],
)
def test_chat_trace_enabled(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("WIKI_CHAT_TRACE", raising=False)
    else:
        monkeypatch.setenv("WIKI_CHAT_TRACE", value)
    assert chat_trace.chat_trace_enabled() is expected


# ── extract_tool_activity ───────────────────────────────────────────────────

def test_extract_tool_activity_splits_calls_and_returns():
    messages = [
        _msg(_call("search_wiki_fts", {"query": "caución"})),
        _msg(_ret("search_wiki_fts", "hit: cauciones-bursatiles.md")),
        _msg(_text("una caución es…")),
    ]
    calls, returns = chat_trace.extract_tool_activity(messages)
    assert calls == [{"name": "search_wiki_fts", "args": {"query": "caución"}}]
    assert returns == [{"name": "search_wiki_fts", "content": "hit: cauciones-bursatiles.md"}]


def test_extract_tool_activity_tolerates_empty_and_unknown():
    assert chat_trace.extract_tool_activity(None) == ([], [])
    assert chat_trace.extract_tool_activity([_msg(_text("no tools"))]) == ([], [])


def test_extract_tool_activity_coerces_unserializable_args():
    calls, _ = chat_trace.extract_tool_activity([_msg(_call("t", object()))])
    # object() is not JSON-serializable -> coerced to str, still json.dumps-able
    json.dumps(calls)


# ── build_turn_record ───────────────────────────────────────────────────────

def test_build_turn_record_shape_and_cited_flag():
    rec = chat_trace.build_turn_record(
        question="¿Qué es una caución?",
        language="es",
        strict_mode=True,
        history=[{"role": "user", "content": "hola"},
                 {"role": "assistant", "content": "Eso no está en mi base de conocimiento."}],
        messages=[_msg(_call("read_wiki_page", {"path": "x"}), _ret("read_wiki_page", "…"))],
        raw_output="una caución… Fuente: [wiki/concepts/cauciones-bursatiles.md]",
        final_answer="una caución… Fuente: [wiki/concepts/cauciones-bursatiles.md]",
        grounded=True,
        refusal_substituted=False,
    )
    assert rec["question"] == "¿Qué es una caución?"
    assert rec["strict_mode"] is True
    assert rec["grounded"] is True
    assert rec["cited"] is True  # ".md" present
    assert len(rec["history"]) == 2
    assert rec["tool_calls"][0]["name"] == "read_wiki_page"
    assert "ts" in rec
    json.dumps(rec)  # must be serializable


def test_build_turn_record_uncited_answer():
    rec = chat_trace.build_turn_record(
        question="q", language="es", strict_mode=True, history=[],
        messages=[], raw_output="sin fuente", final_answer="sin fuente",
        grounded=True, refusal_substituted=False,
    )
    assert rec["cited"] is False


# ── record_turn (I/O) ───────────────────────────────────────────────────────

def _record() -> dict:
    return {"ts": "t", "question": "q", "final_answer": "a"}


def test_record_turn_appends_jsonl_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_CHAT_TRACE", "1")
    chat_trace.record_turn(tmp_path, _record())
    chat_trace.record_turn(tmp_path, _record())
    trace_file = tmp_path / ".llmwiki" / "chat_trace.jsonl"
    lines = trace_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["question"] == "q"


def test_record_turn_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("WIKI_CHAT_TRACE", raising=False)
    chat_trace.record_turn(tmp_path, _record())
    assert not (tmp_path / ".llmwiki" / "chat_trace.jsonl").exists()


def test_record_turn_noop_when_workspace_none(monkeypatch):
    monkeypatch.setenv("WIKI_CHAT_TRACE", "1")
    chat_trace.record_turn(None, _record())  # must not raise


def test_record_turn_never_raises_on_bad_workspace(monkeypatch):
    monkeypatch.setenv("WIKI_CHAT_TRACE", "1")
    # A record with a non-serializable value + a path that can't be written is
    # swallowed; the call must return normally.
    chat_trace.record_turn("/proc/nonexistent-does-not-exist\0", {"x": object()})
