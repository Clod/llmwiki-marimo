"""Lean history for the chat model (context management).

Long accumulated context degrades gpt-4o: the chat trace's session arc shows it
working for ~3 turns and then collapsing — refusing advisory questions without
calling any tool, and leaking on off-corpus ones. Two causes compound: sheer
context length, and bloat from the big advisory tables the answer post-processor
appends (a 40-row table per advisory turn).

`trim_history` builds a LEAN history to send to the model (the user still sees
the full answers in the UI):
  - compact big advisory tables out of older assistant turns — the model can
    re-call `estimar_alternativas` if it needs the numbers, so the table only
    costs context,
  - cap to the last N turns.

Composes with `strip_refused_exchanges` (guardrail.py), which is applied first.
Operates on chat messages duck-typed as `.role` ("user"/"assistant") + `.content`;
compacted turns are returned as small immutable stand-ins.
"""

from __future__ import annotations

from dataclasses import dataclass

# Markers of the advisory table block inside an assistant answer. We cut from the
# earliest one to the end and replace it with a short placeholder.
_TABLE_HEADS = ("**Alternativas con ganancia estimada**", "| opción")
_PLACEHOLDER = "[tabla de alternativas omitida del historial]"

DEFAULT_MAX_TURNS = 3


@dataclass(frozen=True)
class _HistoryMsg:
    """Immutable stand-in for a compacted turn (originals are left untouched)."""

    role: str
    content: str


def _strip_table(content: str) -> str:
    """Drop the advisory table block, keeping the model's prose head."""
    positions = [p for p in (content.find(head) for head in _TABLE_HEADS) if p != -1]
    if not positions:
        return content
    head = content[: min(positions)].rstrip()
    return f"{head}\n{_PLACEHOLDER}".strip()


def _compact(message: object) -> object:
    """Compact an assistant turn that carries an advisory table; else pass through."""
    role = getattr(message, "role", None)
    content = getattr(message, "content", "") or ""
    if role == "assistant" and "| opción" in content:
        return _HistoryMsg(role=role, content=_strip_table(content))
    return message


def trim_history(messages: list, max_turns: int = DEFAULT_MAX_TURNS) -> list:
    """Return a lean history: advisory tables compacted out of older turns, then
    capped to the last `max_turns` turns (``max_turns * 2`` messages).

    Args:
        messages: chat messages (`.role`/`.content`), already refusal-stripped.
        max_turns: how many recent turns to keep; ``0``/``None`` disables the cap.
    """
    compacted = [_compact(m) for m in messages]
    if not max_turns:
        return compacted
    return compacted[-(max_turns * 2):]
