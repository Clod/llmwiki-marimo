"""A table that crosses a page break, made whole on each page.

The PDF extractor (opendataloader-pdf) writes each page on its own, and makes the
first row of every table its header. A table cut by a page break comes out as two
tables, and the second one takes a data row as its header. In
`05 Bonos Dólar Linked y Duales.docx` page 2 ends with

    | Atributo | Valor |
    | --- | --- |
    | Tipo de instrumento | Renta Fija Soberana/Corporativa indexada al TC oficial |

and page 3 starts with

    | Moneda de cotización | Pesos (pagaderos en pesos ajustados al TC oficial A3500) |
    | --- | --- |
    | Exposición económica | Dólar oficial mayorista (BCRA Com. A3500) |

`repeat_split_table_headers` puts the header of the table that ends a page at the top
of the table that starts the next page, when that table has the same number of
columns: «Moneda de cotización» becomes a data row again. Each row stays on its page,
so a citation keeps its page number, and every reader of the page text — the model
that writes the wiki, the search, the retry with the source documents — sees a
whole table.

The rule is by position: in the extracted text a promoted data row and a real header
look the same. It fails only for a new table that starts the next page with no text
before it and has the same columns; none of the example sources has one (2026-11-18).
A page that already repeats the header is left as it is. The office files and the
PDFs both go through it (`extractor.extract`); a .md or .txt file has no pages.
"""

from __future__ import annotations

import re

_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _is_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def _cells(line: str) -> list[str]:
    """The cells of a table row, stripped. An escaped `\\|` stays inside its cell."""
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", inner)]


def _last_table_header(text: str) -> tuple[str, str] | None:
    """The header row and separator of the table that ends `text`, or None when the
    page does not end with a table."""
    lines = [line for line in text.rstrip().splitlines()]
    if not lines or not _is_row(lines[-1]):
        return None
    start = len(lines) - 1
    while start > 0 and _is_row(lines[start - 1]):
        start -= 1
    block = lines[start:]
    if len(block) < 2 or not _SEPARATOR_RE.match(block[1]):
        return None
    return block[0], block[1]


def _with_header(text: str, header: str, separator: str) -> str | None:
    """`text` with `header` at the top of the table it starts with, or None when the
    page does not start with a table of the same columns, or already repeats it."""
    lines = text.split("\n")
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is None or not _is_row(lines[first]):
        return None
    if first + 1 >= len(lines) or not _SEPARATOR_RE.match(lines[first + 1]):
        return None
    if len(_cells(lines[first])) != len(_cells(header)):
        return None
    if _cells(lines[first]) == _cells(header):
        return None
    # The promoted row becomes a data row again, under the header of the table it continues.
    return "\n".join([*lines[:first], header, separator, lines[first], *lines[first + 2:]])


def repeat_split_table_headers(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """`pages` with the header of a table cut by a page break repeated on the next page.

    Pages run in order, so a table over three pages takes the header of the first on
    the second and on the third."""
    out: list[tuple[int, str]] = []
    for number, text in pages:
        previous = _last_table_header(out[-1][1]) if out else None
        if previous is not None:
            fixed = _with_header(text, *previous)
            if fixed is not None:
                text = fixed
        out.append((number, text))
    return out
