"""Citation/grounding post-check for chat answers.

PURPOSE:
A system prompt can ASK the model to answer only from the wiki, but it cannot
GUARANTEE it — LLMs leak general knowledge on "related" topics (e.g. explaining
an ETF, or a fruit). This is a deterministic guardrail that runs AFTER the agent
finishes: if the run produced no substantive result from any tool, the answer
cannot be grounded in the wiki/data, so we replace it with an honest refusal.

The model no longer gets to decide whether it stayed grounded — the code does.

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

from pydantic_ai.messages import ModelMessage, ToolReturnPart

# Refusal text, by wiki language. Must match the system prompt's exact phrase.
REFUSAL_ES = "Eso no está en mi base de conocimiento."
REFUSAL_EN = "That isn't in my knowledge base."

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


def has_grounding(messages: list[ModelMessage]) -> bool:
    """True if any tool in the run returned substantive content."""
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart) and _is_substantive(part.content):
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
