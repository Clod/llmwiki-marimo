"""Which sentences of an answer the verifier checks.

The verifier checks what the model wrote. It skips what the code writes (the
citation line, the advisory table, the Tier 2 warning, the key offer line, the
refusal messages) and the sentences that hold no claim (questions, headings of
a table, fragments with fewer than three words).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from domain.chat import refusal
from domain.chat.guardrail import LEGACY_REFUSALS, REFUSAL_EN, REFUSAL_ES
from domain.verify.models import Sentence

# `Referencia: x`, `**Fuente:** y`, `- Fuentes: z`, in Spanish and English.
_CITATION_LINE = re.compile(
    r"^[\s>*_\-]*(referencias?|fuentes?|sources?|references?)\s*[*_]*\s*:", re.IGNORECASE,
)
# The line `offer_available_keys` appends: "Datos disponibles en `x` (y): ...".
_KEY_OFFER = re.compile(r"^(datos disponibles en|data available in)\s+`", re.IGNORECASE)
_TIER2_WARNING = re.compile(r"^>?\s*⚠️?\s*esta respuesta proviene de un documento fuente", re.IGNORECASE)
_FIXED = tuple(s.lower() for s in (*refusal.FIXED_SENTENCE.values(), REFUSAL_ES, REFUSAL_EN, *LEGACY_REFUSALS))
_MARKUP = re.compile(r"[*_`#>|~\[\]()]|https?://\S+")
_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
MIN_WORDS = 3


def plain_text(sentence: str) -> str:
    """The sentence without markdown marks, as the verifier receives it."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", sentence)
    text = text.replace("|", " ; ").replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"^[\s>#*\-]+", "", text)
    return re.sub(r"\s+", " ", text).strip(" ;")


def is_table_header(sentence: Sentence, answer: str) -> bool:
    """True for the row of a table that is followed by a separator row."""
    if not sentence.text.lstrip().startswith("|"):
        return False
    nxt = answer[sentence.end:].lstrip("\r\n").split("\n", 1)[0]
    return bool(re.match(r"^\s*\|?\s*:?-{2,}", nxt))


def _in_spans(sentence: Sentence, spans: list[tuple[int, int]]) -> bool:
    return any(start <= sentence.start and sentence.end <= end for start, end in spans)


def generated_spans(answer: str, generated: Iterable[str]) -> list[tuple[int, int]]:
    """The character ranges of `answer` that are texts the code wrote."""
    spans = []
    for text in generated:
        text = (text or "").strip()
        if not text:
            continue
        index = answer.find(text)
        if index >= 0:
            spans.append((index, index + len(text)))
    return spans


def is_checkable(sentence: Sentence, answer: str, spans: list[tuple[int, int]]) -> bool:
    """True if the verifier checks `sentence`."""
    text = sentence.text.strip()
    if _in_spans(sentence, spans):
        return False
    if _CITATION_LINE.match(text) or _KEY_OFFER.match(text) or _TIER2_WARNING.match(text):
        return False
    plain = plain_text(text)
    if plain.lower().rstrip(".") in {f.rstrip(".") for f in _FIXED} or refusal.is_refusal(plain):
        return False
    if plain.endswith("?"):
        return False
    if is_table_header(sentence, answer):
        return False
    return len(_WORD.findall(_MARKUP.sub(" ", plain))) >= MIN_WORDS
