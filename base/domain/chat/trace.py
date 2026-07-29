"""Opt-in JSONL trace of read-app chat turns, for offline diagnosis.

Parallel to the ingestion tracer (``base/domain/ingestion/trace.py``), but much
smaller: the chat needs a flat, append-per-turn record, not a multi-stage run.
Enabled with ``WIKI_CHAT_TRACE=1``; a no-op otherwise. Write-only and
best-effort — a trace failure must never break a chat turn.

Each turn appends one JSON object to ``<workspace>/.llmwiki/chat_trace.jsonl``:

    ts                    ISO-8601 UTC, millisecond precision
    question              the user's message this turn
    language              the wiki's content language (es/en/…)
    strict_mode           the "Modo estricto" guardrail toggle state
    history               prior turns sent as context: [{role, content}]
    tool_calls            [{name, args}] the agent invoked this turn
    tool_returns          [{name, content}] what each retrieval returned
    raw_output            the model's answer *before* the guardrail
    grounded              did any retrieval return substantive evidence
    refusal_substituted   did the guardrail replace the answer with a refusal
    final_answer          what the user actually saw
    cited                 convenience flag: does final_answer carry a citation

The point is exactly the class of bug that is invisible on screen: e.g. a prior
refusal in ``history`` priming the model to drop its citation on the next
grounded answer. With the trace on, that becomes one grep over the JSONL.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TRACE_FILE = "chat_trace.jsonl"


def chat_trace_enabled() -> bool:
    """True when ``WIKI_CHAT_TRACE`` is set to anything truthy."""
    return os.environ.get("WIKI_CHAT_TRACE", "").strip() not in ("", "0", "false", "False")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _jsonable(value: Any) -> Any:
    """Best-effort coerce a value to something ``json.dumps`` can serialize."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


_CITATION_MARKERS = (".md", ".docx", ".doc", ".pdf", ".txt", ".csv")


def _looks_cited(text: str) -> bool:
    """Cheap heuristic: a grounded answer names its source.

    A curated wiki page (``*.md``) OR the source document it came from
    (``*.docx`` / ``*.pdf`` …) — the pre-retrieval curated path cites the source
    doc, so matching only ``.md`` would miss it and read as uncited.
    """
    return any(marker in (text or "") for marker in _CITATION_MARKERS)


def extract_tool_activity(messages: Any) -> tuple[list[dict], list[dict]]:
    """Pull ``(tool_calls, tool_returns)`` from a pydantic-ai message list.

    Defensive about part kinds/attributes so a pydantic-ai upgrade degrades to
    empty lists rather than raising. The read app builds ``message_history`` as
    plain text turns (no tool parts), so the only tool activity in
    ``result.all_messages()`` is this turn's.
    """
    calls: list[dict] = []
    returns: list[dict] = []
    for message in messages or []:
        for part in getattr(message, "parts", []):
            kind = getattr(part, "part_kind", None)
            if kind == "tool-call":
                calls.append({
                    "name": getattr(part, "tool_name", "?"),
                    "args": _jsonable(getattr(part, "args", None)),
                })
            elif kind == "tool-return":
                returns.append({
                    "name": getattr(part, "tool_name", "?"),
                    "content": _jsonable(getattr(part, "content", None)),
                })
    return calls, returns


def build_turn_record(
    *,
    question: str,
    language: str | None,
    strict_mode: bool,
    history: list[dict],
    messages: Any,
    raw_output: str,
    final_answer: str,
    grounded: bool,
    refusal_substituted: bool,
) -> dict:
    """Assemble one JSONL turn record (pure; safe to unit-test)."""
    tool_calls, tool_returns = extract_tool_activity(messages)
    cited = _looks_cited(final_answer)
    # A cited answer IS grounded even with no tool calls: the pre-retrieval curated
    # path answers from injected context (tool-less) and cites its source, so the
    # tool-based `grounded` passed in reads False — the citation is the evidence.
    return {
        "ts": _now_iso(),
        "question": question,
        "language": language,
        "strict_mode": strict_mode,
        "history": [
            {"role": h.get("role"), "content": h.get("content")} for h in (history or [])
        ],
        "tool_calls": tool_calls,
        "tool_returns": tool_returns,
        "raw_output": raw_output,
        "grounded": bool(grounded) or cited,
        "refusal_substituted": refusal_substituted,
        "final_answer": final_answer,
        "cited": cited,
    }


def record_turn(workspace: Path | str | None, record: dict) -> None:
    """Append ``record`` to ``<workspace>/.llmwiki/chat_trace.jsonl``.

    No-op when the trace is disabled or ``workspace`` is None. Never raises —
    a trace write must not break the user's chat turn.
    """
    if not chat_trace_enabled() or workspace is None:
        return
    try:
        path = Path(workspace) / ".llmwiki" / _TRACE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — tracing is best-effort; never break chat
        logger.debug("chat trace write failed", exc_info=True)
