"""The figure check: every figure of the prose of a chat answer is classified.

A figure is a number written with digits. The check compares each figure with the
authorised values, the numbers found in the material the model read, in the
generated texts and in the messages of the user. A figure is exact, rounded or
unsupported. The check is deterministic: it calls no model and does not need the
NLI verifier of the rest of `domain/verify/`.

The check proves that a figure comes from the material read. It does not prove that
the figure is the right one for the sentence that holds it. Limits:

  - a number written in words ("tres", "un tercio") is not checked;
  - a date and a year alone are not checked;
  - an integer from 0 to `MAX_COUNT` with no unit is a count and is not checked;
  - the authorised set is permissive: a number of any passage read authorises the
    figure, even when the passage speaks of another thing;
  - a computed figure (a sum, a difference) is unsupported unless a tool computed it.

Pure module: no I/O, no model call.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation

# An integer from 0 to this value, with no unit, is a count of the rows or items the
# model received. It is not a figure.
MAX_COUNT = 12

# A year alone is an integer of four digits between these two values.
FIRST_YEAR, LAST_YEAR = 1900, 2100

# The words that make a number a figure when they touch it: "5 dólares" is a figure,
# "5 bancos" is a count. Compared in lower case.
CURRENCY_WORDS = (
    "pesos", "peso", "dólares", "dólar", "dolares", "dolar", "usd", "ars", "dollars", "dollar",
)

EXACT, ROUNDED, UNSUPPORTED = "exact", "rounded", "unsupported"

# The columns of a table that give no authorised value. A dataset table
# (`dataset_tools.format_rows_as_table`) gives values only in the columns of
# `DATASET_VALUE_COLUMNS`.
NON_VALUE_COLUMNS = ("as_of", "al fecha", "fuente")
DATASET_VALUE_COLUMNS = ("valor", "dims")

_MONTHS = (
    "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre|"
    "january|february|march|april|may|june|july|august|september|october|november|december"
)

# `\d+` groups joined by `.` or `,`.
_TOKEN = re.compile(r"\d+(?:[.,]\d+)*")
_ROWS_LINE = re.compile(r"^\s*\*\*\d+ dataset row\(s\)\*\*")
_SEPARATOR_CELL = re.compile(r"^:?-+:?$")

# Dates. Their digits are not figures of the prose and are not authorised values:
# a page with `updated: 2026-10-01` must not authorise 1, 10 and 2026.
_DATES = (
    re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?)?"),
    re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}"),
    re.compile(rf"\d{{1,2}}\s+de\s+(?:{_MONTHS})(?:\s+(?:de|del)\s+\d{{4}})?", re.I),
    re.compile(rf"(?:{_MONTHS})\s+(?:de\s+)?\d{{4}}", re.I),
    re.compile(rf"(?:{_MONTHS})\s+\d{{1,2}}(?:,?\s+\d{{4}})?", re.I),
    re.compile(rf"\d{{1,2}}\s+(?:{_MONTHS})(?:,?\s+\d{{4}})?", re.I),
)

# Spans of the text that hold digits and are not figures.
_EXCLUDED = (
    re.compile(r"https?://\S+"),
    re.compile(r"\]\([^)]*\)"),                       # target of a markdown link
    re.compile(r"^[ \t]*\d+[.)][ \t]", re.M),         # marker of an ordered list
    *_DATES,
)

_CURRENCY = "|".join(sorted(CURRENCY_WORDS, key=len, reverse=True))
_UNIT_BEFORE = re.compile(rf"(?:\$|\b(?:{_CURRENCY}))\s*$", re.I)
_UNIT_AFTER = re.compile(rf"\s*(?:%|(?:{_CURRENCY})\b)", re.I)

# Precision of the arithmetic of the rounding rule; the default of 28 digits can overflow
# on a very long token.
_CONTEXT = Context(prec=60)


@dataclass(frozen=True)
class Figure:
    """A figure of a text: `text[start:end] == text_of_the_figure`.

    `text` is written as in the answer ("1.187,5", "12%", "$90,000"). `candidates`
    holds one value, or two when the separator is ambiguous ("1.200" is 1200 or 1.2).
    """

    start: int
    end: int
    text: str
    candidates: tuple[Decimal, ...]


@dataclass(frozen=True)
class Authorised:
    """A number the code accepts as the support of a figure, with its origin: the
    `ContextPassage.page` it comes from, or "pregunta" for a message of the user."""

    value: Decimal
    origin: str


@dataclass(frozen=True)
class FigureFinding:
    """The verdict on one figure: "exact", "rounded" or "unsupported", and the
    authorised value nearest to it (None when the authorised set is empty)."""

    figure: Figure
    status: str
    nearest: Authorised | None


@dataclass(frozen=True)
class FigureReport:
    findings: tuple[FigureFinding, ...] = ()

    def unsupported(self) -> tuple[FigureFinding, ...]:
        return tuple(f for f in self.findings if f.status == UNSUPPORTED)

    def rounded(self) -> tuple[FigureFinding, ...]:
        return tuple(f for f in self.findings if f.status == ROUNDED)


# ── reading numbers ──────────────────────────────────────────────────────────

def _to_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _candidates(token: str) -> tuple[Decimal, ...]:
    """The values a token can have, by the rule of the separators.

    Both `.` and `,`: the last one is the decimal separator. One kind, more than once:
    thousands. One kind, once, followed by three digits: thousands or decimal (two
    values). One kind, once, followed by another number of digits: decimal.
    """
    dot, comma = "." in token, "," in token
    values: list[Decimal | None]
    if dot and comma:
        decimal = "." if token.rfind(".") > token.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        values = [_to_decimal(token.replace(thousands, "").replace(decimal, "."))]
    elif not dot and not comma:
        values = [_to_decimal(token)]
    else:
        separator = "." if dot else ","
        if token.count(separator) > 1:
            values = [_to_decimal(token.replace(separator, ""))]
        else:
            before, after = token.split(separator)
            as_decimal = _to_decimal(f"{before}.{after}")
            values = [_to_decimal(before + after), as_decimal] if len(after) == 3 else [as_decimal]
    return tuple(v for v in values if v is not None)


def _touches_name(text: str, start: int, end: int) -> bool:
    """True when a letter, `_`, `/` or `\\` touches the token: a file name, an
    identifier or a path, not a figure."""
    def blocks(char: str) -> bool:
        return char.isalpha() or char in "_/\\"
    return (start > 0 and blocks(text[start - 1])) or (end < len(text) and blocks(text[end]))


def _has_unit(text: str, start: int, end: int) -> bool:
    """True when `%` follows the token, `$` precedes it, or a currency word is next to it."""
    return bool(_UNIT_BEFORE.search(text[max(0, start - 16):start]) or _UNIT_AFTER.match(text, end))


def _spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for pattern in _EXCLUDED for m in pattern.finditer(text)]


def _overlaps(start: int, end: int, spans: Iterable[tuple[int, int]]) -> bool:
    return any(start < b and end > a for a, b in spans)


def figures_in(text: str, skip: Iterable[tuple[int, int]] = ()) -> list[Figure]:
    """The figures of `text`, in order, outside the spans `skip`.

    A token is not a figure when it is part of a URL, a link target, an ordered-list
    marker or a date; when it is a year alone or a count (an integer from 0 to
    `MAX_COUNT`) with no unit; or when a letter, `_`, `/` or `\\` touches it. A `$`
    before the token and a `%` after it belong to the figure.
    """
    skip = list(skip)
    excluded = _spans(text)
    figures: list[Figure] = []
    for match in _TOKEN.finditer(text):
        start, end = match.span()
        token = match.group()
        if _touches_name(text, start, end) or _overlaps(start, end, excluded) or _overlaps(start, end, skip):
            continue
        candidates = _candidates(token)
        if not candidates:
            continue
        if token.isdigit() and not _has_unit(text, start, end):
            value = int(token)
            if value <= MAX_COUNT or (len(token) == 4 and FIRST_YEAR <= value <= LAST_YEAR):
                continue
        first = start - 1 if start > 0 and text[start - 1] == "$" else start
        last = end + 1 if end < len(text) and text[end] == "%" else end
        figures.append(Figure(first, last, text[first:last], candidates))
    return figures


# ── the authorised set ───────────────────────────────────────────────────────

def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _tokens_of(text: str) -> list[Decimal]:
    """Every number of `text`, except the numbers of a name and of a date."""
    dates = [m.span() for pattern in _DATES for m in pattern.finditer(text)]
    values: list[Decimal] = []
    for match in _TOKEN.finditer(text):
        if not _touches_name(text, *match.span()) and not _overlaps(*match.span(), dates):
            values.extend(_candidates(match.group()))
    return values


def _numbers_of(text: str) -> list[Decimal]:
    """Every number of `text` with the rule of the separators, except the tool-return
    metadata (the row count `**N dataset row(s)**` of a dataset table and the columns
    `as_of`, `al fecha` and `fuente`) and the dates. Of a dataset table (header with `valor` and
    `as_of`) only the columns `valor` and `dims` count."""
    values: list[Decimal] = []
    header: list[str] | None = None
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            header = None
            if not _ROWS_LINE.match(line):
                values.extend(_tokens_of(line))
            continue
        cells = _cells(line)
        if all(_SEPARATOR_CELL.match(c) for c in cells if c) and any(cells):
            continue
        if header is None:
            header = [c.lower() for c in cells]
        is_dataset = "valor" in header and "as_of" in header
        for index, cell in enumerate(cells):
            name = header[index] if index < len(header) else ""
            if (name in DATASET_VALUE_COLUMNS) if is_dataset else (name not in NON_VALUE_COLUMNS):
                values.extend(_tokens_of(cell))
    return values


def authorised_values(sources: Iterable[tuple[str, str]]) -> list[Authorised]:
    """The authorised values of `sources`, a sequence of `(origin, text)`.

    Every number of every text except the dates, with every candidate of an ambiguous separator, as an
    absolute value. A value that several texts hold appears once, with the origin of
    the first text in the order of `sources`.
    """
    seen: dict[Decimal, Authorised] = {}
    for origin, text in sources:
        for value in _numbers_of(text):
            seen.setdefault(value.copy_abs(), Authorised(value.copy_abs(), origin))
    return list(seen.values())


# ── classifying ──────────────────────────────────────────────────────────────

def _rounds_to(candidate: Decimal, authorised: Decimal) -> bool:
    """True when `candidate` equals `authorised` rounded to the decimal position of the
    last non-zero digit of `candidate` (1200 is hundreds, 1190 is tens, 12.3 is tenths)."""
    if candidate == 0:
        return False
    exponent = candidate.normalize(_CONTEXT).as_tuple().exponent
    rounded = authorised.quantize(Decimal(1).scaleb(exponent), rounding=ROUND_HALF_UP, context=_CONTEXT)
    return rounded == candidate


def _gap(candidate: Decimal, authorised: Decimal) -> Decimal:
    """The difference between two numbers relative to the larger one; 0 for 0 and 0."""
    larger = max(abs(candidate), abs(authorised))
    return Decimal(0) if larger == 0 else abs(candidate - authorised) / larger


def _classify(figure: Figure, authorised: Sequence[Authorised], tolerance: float) -> FigureFinding:
    limit = Decimal(str(tolerance))
    matches: dict[str, list[tuple[Decimal, Authorised]]] = {EXACT: [], ROUNDED: []}
    for candidate in figure.candidates:
        for item in authorised:
            if candidate == item.value:
                matches[EXACT].append((_gap(candidate, item.value), item))
            elif (item.value != 0 and _rounds_to(candidate, item.value)
                  and abs(candidate - item.value) / abs(item.value) <= limit):
                matches[ROUNDED].append((_gap(candidate, item.value), item))
    for status in (EXACT, ROUNDED):
        if matches[status]:
            return FigureFinding(figure, status, min(matches[status], key=lambda pair: pair[0])[1])
    nearest, best = None, None
    for candidate in figure.candidates:
        for item in authorised:
            gap = _gap(candidate, item.value)
            if best is None or gap < best:
                nearest, best = item, gap
    return FigureFinding(figure, UNSUPPORTED, nearest)


def check_figures(answer: str, sources: Iterable[tuple[str, str]], *,
                  skip: Iterable[tuple[int, int]] = (), tolerance: float = 0.05) -> FigureReport:
    """Classify every figure of `answer` outside the spans `skip`.

    exact:       a candidate equals an authorised value.
    rounded:     a candidate equals an authorised value rounded to the precision the
                 model wrote, and differs from it by no more than `tolerance`
                 (relative). A zero is never rounded.
    unsupported: any other figure. `nearest` is the authorised value with the smallest
                 relative difference; on a tie, the first in the order of `sources`.
    """
    authorised = authorised_values(sources)
    return FigureReport(tuple(_classify(f, authorised, tolerance) for f in figures_in(answer, skip)))
