"""Split an answer into sentences, in Spanish and English, keeping offsets.

The answer is markdown. A block is one line of a list, one row of a table, one
heading, or a run of plain lines (a paragraph). A block is split at `.`, `?`,
`!` and `…` when whitespace and the start of a new sentence follow, except after
an abbreviation. A period between digits is never a boundary, so `1.187,5` and
`32,5 %` stay whole. The offsets index the original text.
"""

from __future__ import annotations

import re

from domain.verify.models import Sentence

# Abbreviations whose final period does not end a sentence. Lowercase, without
# the final period. Dotted acronyms (`S.A`, `EE.UU`) are covered by the pattern
# below, so they need no entry.
ABBREVIATIONS = frozenset({
    "dr", "dra", "sr", "sra", "srta", "ud", "uds", "vs", "etc", "aprox", "núm", "num",
    "pág", "pag", "págs", "pp", "p", "art", "arts", "cap", "lic", "ing", "ej", "cf", "fig",
    "no", "nº", "av", "mr", "mrs", "ms", "inc", "ltd", "corp", "co", "st", "approx",
    "vol", "sec", "mín", "máx", "prof", "tel", "dpto", "apdo", "ref", "est", "gral",
    "e.g", "i.e", "p. ej", "s.a", "s.r.l", "s.a.s", "ee.uu", "a.c", "d.c",
})

# A token made of letters joined by dots: `S.A`, `EE.UU`, `a.m`.
_DOTTED = re.compile(r"^(?:[^\W\d_]{1,3}\.)+[^\W\d_]{1,3}$")
_TERMINATORS = ".?!…"
# What can start a sentence after the whitespace of a boundary.
_OPENERS = re.compile(r"[\w¿¡(\[«\"'“*_`$#]", re.UNICODE)
_LIST_MARKER = re.compile(r"^(\s*)(?:[-*+•]\s+|\d{1,3}[.)]\s+|>\s*)+")
_TABLE_ROW = re.compile(r"^\s*\|")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_HEADING = re.compile(r"^\s*#{1,6}\s")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _token_before(text: str, index: int) -> str:
    """The word that ends at `index` (exclusive), dots inside it included."""
    start = index
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] in ".ºª"):
        start -= 1
    return text[start:index]


def _is_boundary(text: str, i: int) -> bool:
    """True if the terminator at `text[i]` ends a sentence."""
    char = text[i]
    j = i + 1
    while j < len(text) and text[j] in _TERMINATORS + "\"'»”)]*_":
        j += 1                                   # `?!`, closing quotes, `**`
    if j >= len(text):
        return True
    if not text[j].isspace():
        return False                             # `1.187`, `ejemplo.com`
    k = j
    while k < len(text) and text[k].isspace():
        k += 1
    if k >= len(text):
        return True
    if not _OPENERS.match(text[k]):
        return False
    if char == ".":
        token = _token_before(text, i)
        if not token:
            return True
        low = token.lower().rstrip(".")
        if low in ABBREVIATIONS or _DOTTED.match(token):
            return False
        if len(low) == 1 and low.isalpha() and token.isupper():
            return False                         # an initial: `J. Pérez`
        if text[k].islower():
            return False                         # the next word is lowercase
    return True


def _split_segment(text: str, start: int, end: int) -> list[Sentence]:
    """The sentences of `text[start:end]`, a block with no list marker."""
    sentences: list[Sentence] = []
    segment = text[start:end]
    first = 0
    i = 0
    while i < len(segment):
        if segment[i] in _TERMINATORS and _is_boundary(segment, i):
            j = i + 1
            while j < len(segment) and segment[j] in _TERMINATORS + "\"'»”)]*_":
                j += 1
            _emit(sentences, text, start + first, start + j)
            first = j
            i = j
            continue
        i += 1
    _emit(sentences, text, start + first, end)
    return sentences


def _emit(out: list[Sentence], text: str, start: int, end: int) -> None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if end > start:
        out.append(Sentence(start, end, text[start:end]))


def split_sentences(text: str) -> list[Sentence]:
    """The sentences of `text`, in order, with their offsets in `text`.

    Headings, table separators and the content of code fences are dropped. A
    table row is one sentence. A list item starts a new sentence after its marker.
    """
    sentences: list[Sentence] = []
    paragraph: tuple[int, int] | None = None
    in_fence = False
    pos = 0

    def flush() -> None:
        nonlocal paragraph
        if paragraph is not None:
            sentences.extend(_split_segment(text, *paragraph))
            paragraph = None

    for line in text.splitlines(keepends=True):
        line_start, line_end = pos, pos + len(line.rstrip("\r\n"))
        pos += len(line)
        if _FENCE.match(line):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line.strip():
            flush()
        elif _HEADING.match(line):
            flush()
        elif _TABLE_ROW.match(line):
            flush()
            if not _TABLE_SEPARATOR.match(line):
                _emit(sentences, text, line_start, line_end)
        else:
            marker = _LIST_MARKER.match(line)
            if marker:
                flush()
                paragraph = (line_start + marker.end(), line_end)
            elif paragraph is None:
                paragraph = (line_start, line_end)
            else:
                paragraph = (paragraph[0], line_end)
    flush()
    return sentences
