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

`model_history` builds the history that the model receives in the pre-retrieval
and strict modes from three helpers, `compact_tables`, `cap_turns` and
`with_origin_lines`, after `strip_refused_exchanges`. The nodes C2a to C2d of
the input workflows (`domain/chat/workflow/catalog.py`) run the same four steps,
one per node.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from domain.chat.guardrail import strip_refused_exchanges

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


def compact_tables(messages: list) -> list:
    """`messages` with the advisory table of each assistant turn replaced by a
    placeholder (`_compact`)."""
    return [_compact(m) for m in messages]


def cap_turns(messages: list, max_turns: int | None = DEFAULT_MAX_TURNS) -> list:
    """The last `max_turns` turns of `messages` (``max_turns * 2`` messages);
    ``0``/``None`` disables the cap."""
    if not max_turns:
        return list(messages)
    return messages[-(max_turns * 2):]


def trim_history(messages: list, max_turns: int = DEFAULT_MAX_TURNS) -> list:
    """Return a lean history: advisory tables compacted out of older turns, then
    capped to the last `max_turns` turns (``max_turns * 2`` messages).

    Args:
        messages: chat messages (`.role`/`.content`), already refusal-stripped.
        max_turns: how many recent turns to keep; ``0``/``None`` disables the cap.
    """
    return cap_turns(compact_tables(messages), max_turns)


def with_origin_lines(messages: list, language: str | None = None) -> list:
    """`messages` as Pydantic AI messages. An assistant turn that read data ends
    with its origin line (`data_origin`)."""
    history: list = []
    for message in messages:
        if message.role == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=message.content)]))
        elif message.role == "assistant":
            origin = data_origin(getattr(message, "context", ()), language)
            content = f"{message.content}\n\n{origin}" if origin else message.content
            history.append(ModelResponse(parts=[TextPart(content=content)]))
    return history


def model_history(messages: Sequence[Any], language: str | None = None) -> list:
    """The prior turns as Pydantic AI messages, without refused exchanges.

    A citation-less refusal left in the context primes the model to answer the
    next question without a citation too (verified), so refused exchanges are
    dropped before the history is trimmed and converted. An assistant turn that
    read data ends with its origin line (`data_origin`): the history has no tool
    call and no injected context, so without the line the model calls the figures
    of a past answer unsupported.
    """
    return with_origin_lines(trim_history(strip_refused_exchanges(list(messages))), language)


def history_counts(prior: Sequence[Any], language: str | None = None) -> dict:
    """The counts that node C1 records: the prior messages, the refused ones
    `strip_refused_exchanges` removed, and the messages the model receives."""
    prior = list(prior)
    return dict(
        prior_messages=len(prior),
        refused_dropped=len(prior) - len(strip_refused_exchanges(prior)),
        history_messages=len(model_history(prior, language)),
    )
