"""Split the pages the model read into passages for the verifier.

A page is split at its headings, then at paragraph boundaries. Paragraphs of a
section are packed into passages of at most `max_chars` characters. Each passage
of a section starts with the heading line, so the premise names its topic. A
paragraph longer than the limit is split at lines, then at sentences, then at
the limit.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from domain.verify.models import ContextPassage, Passage
from domain.verify.sentences import split_sentences

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def _sections(text: str) -> list[tuple[str, str]]:
    """`(heading line, body)` pairs; the first heading is empty when the text
    starts with no heading."""
    sections: list[tuple[str, str]] = []
    heading, body = "", []
    for line in text.splitlines():
        if _HEADING.match(line):
            sections.append((heading, "\n".join(body)))
            heading, body = line.strip(), []
        else:
            body.append(line)
    sections.append((heading, "\n".join(body)))
    return [(h, b) for h, b in sections if b.strip()]


def _hard_cut(text: str, limit: int) -> list[str]:
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _split_long(paragraph: str, limit: int) -> list[str]:
    """Pieces of `paragraph` no longer than `limit`: lines, then sentences."""
    pieces: list[str] = []
    for line in paragraph.splitlines():
        if len(line) <= limit:
            pieces.append(line)
            continue
        sentences = [s.text for s in split_sentences(line)] or [line]
        for sentence in sentences:
            pieces.extend([sentence] if len(sentence) <= limit else _hard_cut(sentence, limit))
    return [p for p in pieces if p.strip()]


def _pack(units: list[str], limit: int, joiner: str) -> list[str]:
    packed: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(joiner) + len(unit) > limit:
            packed.append(current)
            current = ""
        current = f"{current}{joiner}{unit}" if current else unit
    if current:
        packed.append(current)
    return packed


def split_passages(page: ContextPassage, max_chars: int) -> list[Passage]:
    """The passages of one context item, each at most `max_chars` long plus its
    heading line."""
    text = _FRONTMATTER.sub("", page.text.replace("\r\n", "\n"), count=1).strip()
    passages: list[Passage] = []
    for heading, body in _sections(text):
        prefix = f"{heading}\n" if heading else ""
        room = max(max_chars - len(prefix), max_chars // 2)
        units: list[str] = []
        for paragraph in _PARAGRAPH_BREAK.split(body.strip()):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            units.extend([paragraph] if len(paragraph) <= room else _split_long(paragraph, room))
        for chunk in _pack(units, room, "\n\n"):
            passages.append(Passage(page.page, prefix + chunk))
    return passages


def split_context(context: Iterable[ContextPassage], max_chars: int) -> list[Passage]:
    """The passages of every context item, in order."""
    result: list[Passage] = []
    for page in context:
        result.extend(split_passages(page, max_chars))
    return result
