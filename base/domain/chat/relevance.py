"""Whether the wiki pages injected into a pre-retrieval prompt answer the question.

The search ranks pages by the words they share with the question, and a page can share
them without answering it: «¿Cómo castigaron a la reina al final de Blancanieves?» brings
the passage of the poisoned apple, which names the queen. The model workflow
(`domain/chat/workflow/model_calls.py`) runs one of two checks after the answer, chosen
per turn (`PreRetrievalTurn.relevance`):

- **evidence** (Q_EVIDENCE_QUOTE): the prompt asks the answer to open with the sentence of
  the context that answers the question (`prompts/relevance_evidence.*.md`), and the code
  looks for that sentence in the context. A sentence that is not there, «ninguna» or no
  line fail.
- **judge** (Q_JUDGE_PAGES): a separate call to the model receives the question and the
  pages and answers yes or no (`prompts/relevance_judge.*.md`).

A failed check sends the turn to the retry with the source documents (Q_RETRY_SOURCES).
Neither check proves that the pages answer the question: the evidence check proves that
the quoted sentence is in the context, and the judge is a model whose answer nothing
checks.
"""

from __future__ import annotations

import re

from domain.prompts import load_prompt

NONE, EVIDENCE, JUDGE = "none", "evidence", "judge"
CHOICES = (NONE, EVIDENCE, JUDGE)

# A quoted sentence shorter than this does not count: «la reina» is in almost any context.
MIN_QUOTE_WORDS = 4

_EVIDENCE_LINE = re.compile(r"^[\s>*_#-]*(?:evidencia|evidence)[\s*_]*:\s*(.*)$", re.IGNORECASE | re.MULTILINE)
_NO_QUOTE = {"ninguna", "ninguno", "none", "no"}
_MARKS = re.compile(r"[«»“”\"'‘’*_`>#]")
_SPACES = re.compile(r"\s+")


def choice(value: str | None) -> str:
    """The check of a turn: one of `CHOICES`; anything else is NONE."""
    return value if value in CHOICES else NONE


def evidence_prompt(language: str | None) -> str:
    """The instruction that asks for the evidence line, appended to the prompt of the turn."""
    return load_prompt("relevance_evidence", language)


def _normal(text: str) -> str:
    return _SPACES.sub(" ", _MARKS.sub("", text)).strip().casefold()


def evidence_quote(answer: str) -> str | None:
    """The sentence of the first evidence line of `answer`, without quotes and marks; None
    when there is no evidence line or it says «ninguna» / «none»."""
    match = _EVIDENCE_LINE.search(answer)
    if not match:
        return None
    quote = _normal(match.group(1)).rstrip(".")
    return None if not quote or quote in _NO_QUOTE else quote


def quote_found(quote: str | None, context: str) -> bool:
    """Whether `quote` is in `context`, ignoring case, spaces, quotes and markdown marks;
    a quote shorter than `MIN_QUOTE_WORDS` words does not count."""
    if not quote or len(quote.split()) < MIN_QUOTE_WORDS:
        return False
    return quote in _normal(context)


def judge_system_prompt(language: str | None) -> str:
    return load_prompt("relevance_judge", language)


def judge_user_prompt(question: str, context: str, language: str | None) -> str:
    """The message the judge receives: the question and the text of the pages."""
    if (language or "").lower().startswith("es"):
        return f"Pregunta: {question}\n\nTexto de las páginas:\n{context}"
    return f"Question: {question}\n\nText of the pages:\n{context}"


def judge_says_yes(output: str) -> bool:
    """The verdict of the judge: False only when its first word is «no»; any other answer
    counts as yes, so an unexpected answer does not send the turn to the retry."""
    words = re.findall(r"\w+", output.casefold())
    return not (words and words[0] == "no")


_WARNING = {
    "es": "La comprobación ({check}) indica que las páginas de la wiki no responden esta pregunta.",
    "en": "The check ({check}) says that the wiki pages do not answer this question.",
}
_CHECK_NAMES = {
    "es": {EVIDENCE: "cita de evidencia", JUDGE: "juez"},
    "en": {EVIDENCE: "evidence quote", JUDGE: "judge"},
}


def warning(check: str, language: str | None) -> str:
    """The line appended to an answer whose check failed with no source fragment to retry with."""
    lang = "es" if (language or "").lower().startswith("es") else "en"
    return _WARNING[lang].format(check=_CHECK_NAMES[lang].get(check, check))
