"""Citation/grounding post-check for chat answers.

PURPOSE:
A system prompt can ASK the model to answer only from the wiki, but it cannot
GUARANTEE it — LLMs leak general knowledge on "related" topics (e.g. explaining
an ETF, or a fruit). This is a deterministic guardrail that runs AFTER the agent
finishes: if the run produced no substantive result from any tool, the answer
cannot be grounded in the wiki/data.

What follows depends on the mode. The pre-retrieval mode replaces the answer of a data
question with a refusal (`enforce_grounding`, node R_NO_EVIDENCE). The agent mode keeps
the refusal as a last resort: it retries the model once (T_RETRY, `services/chat.py`)
and then shows the answer with a warning (T_NO_EVIDENCE_WARNING). The model no longer
gets to decide whether it stayed grounded — the code does.

Design notes:
- Every tool the chat agent registers is a *grounding* tool (read a wiki page,
  search the wiki/sources, query a dataset, run the advisory). So "the run
  obtained a substantive tool result" is a sound, domain-agnostic proxy for
  "the answer can be grounded". We don't hard-code tool names.
- "Substantive" = a tool return that is non-empty and is not one of the tools'
  own "nothing found / failed" sentinels (see NEGATIVE_MARKERS).
- Known limitation: this catches answers with NO grounding evidence (the common,
  egregious leak). It does not catch an answer that ignored a result it did
  retrieve; that needs answer-vs-source verification (future work).
"""

from __future__ import annotations

from dataclasses import is_dataclass, replace
from types import SimpleNamespace

from pydantic_ai.messages import ModelMessage, ToolReturnPart

from domain.chat import refusal

# Refusal text, by wiki language. Must match the system prompt's exact phrase.
# What the system could not do, not what the wiki holds: "Eso no está en mi base de
# conocimiento." said the wiki lacks a datum it held (pil-01, pil-04, pil-06).
REFUSAL_ES = "No pude respaldar esta respuesta con una consulta a la wiki en este turno."
REFUSAL_EN = "I could not back this answer with a lookup in the wiki in this turn."
# The phrases before 2026-10-11, still recognised in stored conversations.
LEGACY_REFUSALS = ("Eso no está en mi base de conocimiento.", "That isn't in my knowledge base.")

# Lowercased substrings that mark a tool return as "nothing found / failed".
# Sourced from the chat tools' own empty/error messages (wiki_tools.py,
# dataset_tools.py, chat/tools.py).
NEGATIVE_MARKERS = (
    "page not found",
    "invalid path",
    "error reading",
    "no wiki pages found",
    "no results found",       # chat/tools.py search_source_chunks: "No results found for '{query}'."
    "no source chunks found",
    "no chunks found",
    "no dataset rows found",
    "dataset lookup failed",
    "wiki search failed",
    "search failed",
    "no se encontró",
    "no se encontro",
)


def _is_substantive(content: object) -> bool:
    """True if a tool-return content looks like real evidence (not empty/not-found)."""
    text = str(content).strip()
    if not text:
        return False
    low = text.lower()
    return not any(marker in low for marker in NEGATIVE_MARKERS)


def is_tool_result(part: object) -> bool:
    """True if `part` is the return of a tool call that ran and succeeded.

    Pydantic AI 2 records a call rejected with `ModelRetry` as a `ToolReturnPart`
    with `outcome='retried'`, and its content is the error (check 18). Only
    `outcome='success'` is a result.
    """
    return isinstance(part, ToolReturnPart) and part.outcome == "success"


def has_grounding(messages: list[ModelMessage]) -> bool:
    """True if any tool in the run returned substantive content."""
    for message in messages:
        for part in getattr(message, "parts", []):
            if is_tool_result(part) and _is_substantive(part.content):
                return True
    return False


def enforce_grounding(output: str, messages: list[ModelMessage], *, refusal: str) -> str:
    """Return `output` if the run is grounded, else the `refusal` string.

    Args:
        output: The agent's final text answer.
        messages: The run's full message history (result.all_messages()).
        refusal: The language-appropriate refusal phrase to substitute when the
            answer has no grounding evidence.
    """
    return output if has_grounding(messages) else refusal


def refusal_for(language: str | None) -> str:
    """The refusal phrase for a wiki's content language."""
    return REFUSAL_ES if (language or "en").lower().startswith("es") else REFUSAL_EN


def _is_refused(message) -> bool:
    """True for an assistant message that is a refusal: the guardrail's, a legacy one,
    or one that opens with the fixed refusal sentence."""
    content = (getattr(message, "content", "") or "").strip()
    return getattr(message, "role", None) == "assistant" and (
        content in (REFUSAL_ES, REFUSAL_EN, *LEGACY_REFUSALS) or refusal.is_refusal(content)
    )


def strip_refused_exchanges(messages: list) -> list:
    """Drop each refusal from the messages, with the user turn that triggered it.

    Used where a refusal holds no content at all: the markdown of a conversation
    drafted as a page (`services.chat.conversation_markdown`). The history that the
    model receives keeps the question instead (`mark_refused_exchanges`).

    Operates on chat messages duck-typed as having `.role` ("user"/"assistant")
    and `.content`; unknown shapes pass through untouched.
    """
    msgs = list(messages)
    drop: set[int] = set()
    for i, m in enumerate(msgs):
        if _is_refused(m):
            drop.add(i)
            if i - 1 >= 0 and getattr(msgs[i - 1], "role", None) == "user":
                drop.add(i - 1)  # the question that got refused
    return [m for i, m in enumerate(msgs) if i not in drop]


# The note that replaces a refusal in the history that the model receives. It names no
# cause: a refusal can come from the blacklist, the coverage roster, the guardrail or
# the model. Written by the code, between brackets, as the advisory-table placeholder
# of `history._PLACEHOLDER`.
REFUSED_NOTE = {"es": "[pregunta anterior sin respuesta]", "en": "[earlier question, not answered]"}


def refused_note(language: str | None) -> str:
    """The note of `language` that replaces a refusal; English when the language has none."""
    return REFUSED_NOTE.get((language or "en")[:2], REFUSED_NOTE["en"])


def mark_refused_exchanges(messages: list, language: str | None = None) -> list:
    """The messages with each refusal replaced by the note of `language`; the question
    that got the refusal stays.

    A citation-less refusal left in the history made the model answer the next
    question without a citation too (verified with the refusal text in the history).
    Removing the question as well lost the topic of a follow-up: after a refusal,
    «¿Y la carroza?» had nothing to be rewritten from. The note keeps the question
    and the turn order, and gives the model no prose of its own to imitate.

    A replaced message keeps its other fields; its passages (`context`) are emptied,
    so no origin line is added to the note.
    """
    note = refused_note(language)
    marked = []
    for m in messages:
        if not _is_refused(m):
            marked.append(m)
        elif is_dataclass(m):
            fields = {"content": note, **({"context": ()} if hasattr(m, "context") else {})}
            marked.append(replace(m, **fields))
        else:
            marked.append(SimpleNamespace(role="assistant", content=note, context=()))
    return marked


def count_refused(messages: list) -> int:
    """The number of refusals among `messages`."""
    return sum(1 for m in messages if _is_refused(m))
