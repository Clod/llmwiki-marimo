"""Query condensation: a follow-up question rewritten so that it stands alone.

Under pre-retrieval the code decides, before the model runs, whether a question
names a topic of the wiki (the coverage gate, node Q5) and searches with it (Q6).
An elliptic follow-up ("¿Y qué riesgos tiene?", after a question about the
caución) names no topic, so the gate refuses it although the conversation makes
its subject plain. Condensation asks the model to rewrite such a question, from
the earlier turns, into one that names its subject ("¿Qué riesgos tiene la
caución bursátil?"); the gate and the search then run on the rewrite. The
answering model still receives the original question and the full history.

The rewrite only resolves references to what was already said: the gate still
decides. A question about another subject ("¿Cuál es la capital de Francia?")
is rewritten unchanged and refused as before.

Pure helpers: the prompt, the conversation as text, and the check of a rewrite.
The model call itself is injected by the caller (`services/chat.py`).
"""

from __future__ import annotations

from collections.abc import Sequence

from domain.prompts import load_prompt

# How many earlier exchanges the rewrite sees, and how much of each answer.
MAX_EXCHANGES = 3
MAX_ANSWER_CHARS = 600
# A rewrite longer than this is not a question but an answer, or a ramble.
MAX_REWRITE_CHARS = 300

_LABELS = {"es": ("Usuario", "Asistente", "Última pregunta"),
           "en": ("User", "Assistant", "Last question")}


def _lang(language: str | None) -> str:
    return "es" if (language or "en").lower().startswith("es") else "en"


def system_prompt(language: str | None) -> str:
    """The fixed instruction of the rewrite, in the wiki's language."""
    return load_prompt("condense", _lang(language))


def exchanges(history: Sequence) -> list[tuple[str, str]]:
    """(question, answer) pairs of a Pydantic AI message history, oldest first."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    pairs: list[tuple[str, str]] = []
    question: str | None = None
    for message in history:
        if isinstance(message, ModelRequest):
            texts = [p.content for p in message.parts
                     if isinstance(p, UserPromptPart) and isinstance(p.content, str)]
            if texts:
                question = texts[-1]
        elif isinstance(message, ModelResponse) and question is not None:
            answer = "".join(p.content for p in message.parts if isinstance(p, TextPart))
            pairs.append((question, answer))
            question = None
    return pairs


def prompt(question: str, history: Sequence, language: str | None) -> str:
    """The user prompt of the rewrite: the last exchanges, then the question."""
    user, assistant, last = _LABELS[_lang(language)]
    lines = []
    for asked, answered in exchanges(history)[-MAX_EXCHANGES:]:
        answered = answered.strip()
        if len(answered) > MAX_ANSWER_CHARS:
            answered = answered[:MAX_ANSWER_CHARS].rstrip() + "…"
        lines.append(f"{user}: {asked.strip()}\n{assistant}: {answered}")
    lines.append(f"{last}: {question.strip()}")
    return "\n\n".join(lines)


def accepted(rewrite: str | None, question: str) -> str:
    """The rewrite to gate and search with, or `question` when the rewrite is
    empty, longer than a question, or not a single line."""
    text = (rewrite or "").strip().strip('"«»').strip()
    if not text or len(text) > MAX_REWRITE_CHARS or "\n" in text:
        return question
    return text
