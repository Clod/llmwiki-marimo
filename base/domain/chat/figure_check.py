"""The figure check of one chat turn: node C3.

`domain/verify/figures.py` classifies the figures of a text against authorised
values. This module gives it the material of one turn: the prose of the answer, the
passages of the turn, the texts the code appended, and the messages of the
conversation. The output workflow (`workflow/output_checks.py`) runs it as the step
`C3` in the pre-retrieval and the strict modes. The streaming mode does not run it
(`services/chat.py`).

The text of the model is never edited. When the prose has rounded figures, a fixed
note of the locale is appended after every other appended text.

Pure module: no I/O, no model call.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from domain.chat.postprocess import TABLE_NOTE
from domain.extensions import roles
from domain.i18n import SUPPORTED_LANGUAGES, get_locale
from domain.verify.figures import FigureReport, check_figures
from domain.verify.models import ContextPassage

# The labels of the citation lines `ensure_citation` appends.
_CITATION_LINE = re.compile(r"^[ \t]*(?:Fuente|Referencia)s?:.*$", re.MULTILINE)

# The origin of a value that a message of the user wrote.
QUESTION = "pregunta"


def generated_origin() -> str:
    """The origin of a value of a generated text: the name of the tool whose return is
    appended verbatim (`domain.extensions.roles()`), or `generated` with no such tool."""
    return "/".join(sorted(roles().verbatim)) or "generated"


@dataclass(frozen=True)
class FigureCheck:
    """The result of the check of one answer.

    report: the classification of every figure of the prose.
    note:   the note on rounded figures, or "" when the prose has none.
    """

    report: FigureReport
    note: str = ""


def _occurrences(text: str, part: str) -> list[tuple[int, int]]:
    """The spans of `text` where `part` occurs."""
    part = part.strip()
    if not part:
        return []
    spans, at = [], text.find(part)
    while at != -1:
        spans.append((at, at + len(part)))
        at = text.find(part, at + len(part))
    return spans


def appended_spans(answer: str, generated: Sequence[str]) -> list[tuple[int, int]]:
    """The spans of `answer` the code wrote: the generated texts, the note above the
    advisory table (`postprocess.TABLE_NOTE`), the citation lines and the Tier-2
    warning. They are not prose."""
    spans = [span for text in generated for span in _occurrences(answer, text)]
    spans += [span for pair in TABLE_NOTE.values() for sentence in pair for span in _occurrences(answer, sentence)]
    spans += [m.span() for m in _CITATION_LINE.finditer(answer)]
    for language in SUPPORTED_LANGUAGES:
        spans += _occurrences(answer, get_locale(language).tier2_warning)
    return spans


def conversation_sources(messages: Iterable[Any]) -> list[tuple[str, str]]:
    """The `(origin, text)` pairs of the conversation that authorise figures: every
    message of the user, and the passages of every earlier answer. The prose of an
    earlier answer is not a source: an unsupported figure of one turn would become
    authorised in the next."""
    sources: list[tuple[str, str]] = []
    for message in messages:
        if message.role == "user":
            sources.append((QUESTION, message.content))
        else:
            sources += [(p.page, p.text) for p in getattr(message, "context", ())]
    return sources


def counts(report: FigureReport | None) -> dict[str, int]:
    """The counts the node C3 records."""
    if report is None:
        return dict(figures_checked=0, figures_unsupported=0, figures_rounded=0)
    return dict(figures_checked=len(report.findings), figures_unsupported=len(report.unsupported()),
                figures_rounded=len(report.rounded()))


def rounded_note(language: str | None, has_table: bool, tolerance: float) -> str:
    """The note on rounded figures, in the language of the wiki. It names the table
    when the answer has one, and the data consulted otherwise."""
    locale = get_locale(language)
    template = locale.rounded_figures_note if has_table else locale.rounded_figures_note_data
    percent = f"{tolerance * 100:g}"
    # `str.replace`, not `%`: the text of the note holds a literal percent sign.
    return template.replace("%(pct)s", percent.replace(".", ",") if locale.code == "es" else percent)


def check_answer(
    answer: str, passages: Sequence[ContextPassage], generated: Sequence[str],
    conversation: Sequence[tuple[str, str]], *, tolerance: float, language: str | None,
) -> FigureCheck:
    """Classify every figure of the prose of `answer`.

    `passages` are the passages of this turn (injected pages and rows, tool returns);
    `generated` the texts the code appended (the advisory table); `conversation` the
    sources of `conversation_sources`. The figures of `generated`, of the note above
    the advisory table, of the citation lines and of the Tier-2 warning are not checked;
    the figures of `generated` are authorised values.
    """
    if not answer.strip():
        return FigureCheck(FigureReport())
    sources = [(p.page, p.text) for p in passages]
    sources += [(generated_origin(), text) for text in generated]
    sources += list(conversation)
    report = check_figures(answer, sources, skip=appended_spans(answer, generated), tolerance=tolerance)
    if not report.rounded():
        return FigureCheck(report)
    has_table = bool(generated) or any(p.page.startswith("datasets/") for p in passages)
    return FigureCheck(report, rounded_note(language, has_table, tolerance))


def with_note(answer: str, check: FigureCheck) -> str:
    """`answer` with the note of `check` appended, or unchanged when it has none."""
    return f"{answer.rstrip()}\n\n{check.note}" if check.note else answer
