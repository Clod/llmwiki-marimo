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


# The origin line of an assistant turn whose answer used data. The history keeps the
# text of each answer and drops the context and the tool calls of its turn, so a
# table of rates in a past answer reaches the model with no source in sight; asked
# to take its figures only from tools, the model then calls the table unsupported.
_ORIGIN = {
    "es": ("[Origen de las cifras de esta respuesta: {items}. Esa lectura ocurrió en ese turno "
           "y no figura en este historial.]",
           "`{page}`, leído por el código", "`{page}`, devuelto por la herramienta `{tool}`",
           "la herramienta `{tool}`"),
    "en": ("[Origin of the figures of this answer: {items}. That read happened in that turn "
           "and is not in this history.]",
           "`{page}`, read by the code", "`{page}`, returned by the tool `{tool}`",
           "the tool `{tool}`"),
}


@dataclass(frozen=True)
class _HistoryMsg:
    """Immutable stand-in for a compacted turn (originals are left untouched)."""

    role: str
    content: str
    context: tuple = ()


def data_origin(context, language: str | None = None) -> str | None:
    """The origin line of the data passages of a turn, or None when the turn read no
    data. A data passage is a dataset file (`datasets/x.md`), injected by the code or
    returned by a tool, or the return of a calculation tool (its page is the tool
    name). Wiki pages and source fragments are not data."""
    template, by_code, by_tool, calculation = _ORIGIN.get(language or "en", _ORIGIN["en"])
    items: list[str] = []
    for passage in context or ():
        if passage.page.startswith("datasets/"):
            item = by_tool.format(page=passage.page, tool=passage.tool) if passage.tool \
                else by_code.format(page=passage.page)
        elif passage.tool and passage.page == passage.tool:
            item = calculation.format(tool=passage.tool)
        else:
            continue
        if item not in items:
            items.append(item)
    return template.format(items="; ".join(items)) if items else None


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
        return _HistoryMsg(role=role, content=_strip_table(content), context=getattr(message, "context", ()))
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
