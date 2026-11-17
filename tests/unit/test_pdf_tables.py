"""A table of a PDF reaches the extracted text as a Markdown table.

opendataloader-pdf returns a table as a `table` element: `table row` elements in `rows`,
`table cell` elements in `cells`, and the content of each cell in `kids`. The extraction
dropped every element type it did not know, so every table of a PDF, and of an office
file converted to PDF, was lost (HALLAZGOS, 2026-11-17)."""

from domain.ingestion.pdf_extract import _element_to_markdown, _elements_to_pages


def _p(text):
    return {"type": "paragraph", "content": text}


def _cell(row, col, *kids, rspan=1, cspan=1):
    return {"type": "table cell", "row number": row, "column number": col,
            "row span": rspan, "column span": cspan, "kids": list(kids)}


def _table(rows, n_rows, n_cols):
    return {"type": "table", "page number": 1, "number of rows": n_rows, "number of columns": n_cols,
            "rows": [{"type": "table row", "row number": i + 1, "cells": cells} for i, cells in enumerate(rows)]}


def test_a_table_becomes_a_markdown_table_with_a_header():
    table = _table([[_cell(1, 1, _p("Atributo")), _cell(1, 2, _p("Valor"))],
                    [_cell(2, 1, _p("Garantía")), _cell(2, 2, _p("SEDESA hasta monto máximo garantizado por el BCRA"))]],
                   2, 2)
    assert _element_to_markdown(table) == (
        "| Atributo | Valor |\n"
        "| --- | --- |\n"
        "| Garantía | SEDESA hasta monto máximo garantizado por el BCRA |")


def test_a_cell_with_several_paragraphs_or_a_list_stays_on_one_line():
    lst = {"type": "list", "list items": [{"content": "uno"}, {"content": "dos"}]}
    table = _table([[_cell(1, 1, _p("A")), _cell(1, 2, _p("B"))],
                    [_cell(2, 1, _p("primero"), _p("segundo")), _cell(2, 2, lst)]], 2, 2)
    assert _element_to_markdown(table).splitlines()[2] == "| primero segundo | - uno - dos |"


def test_a_pipe_inside_a_cell_does_not_break_the_table():
    table = _table([[_cell(1, 1, _p("Moneda")), _cell(1, 2, _p("Pesos | Dólares"))]], 1, 2)
    assert _element_to_markdown(table).splitlines()[0] == r"| Moneda | Pesos \| Dólares |"


def test_a_spanning_cell_does_not_shift_the_other_cells():
    table = _table([[_cell(1, 1, _p("Título"), cspan=2)],
                    [_cell(2, 1, _p("a")), _cell(2, 2, _p("b"))]], 2, 2)
    assert _element_to_markdown(table).splitlines() == ["| Título |  |", "| --- | --- |", "| a | b |"]


def test_a_table_without_text_adds_nothing():
    assert _element_to_markdown(_table([[_cell(1, 1), _cell(1, 2, _p(" "))]], 1, 2)) == ""


def test_rows_and_cells_nested_in_kids_are_read_too():
    table = {"type": "table", "page number": 1, "kids": [
        {"type": "table row", "row number": 1, "kids": [_cell(1, 1, _p("x")), _cell(1, 2, _p("y"))]}]}
    assert _element_to_markdown(table).splitlines()[0] == "| x | y |"


def test_the_table_keeps_its_place_on_the_page():
    elements = [{"type": "heading", "heading level": 2, "content": "Tabla resumen", "page number": 1},
                _table([[_cell(1, 1, _p("A")), _cell(1, 2, _p("B"))]], 1, 2),
                {"type": "paragraph", "content": "Después", "page number": 1}]
    page = _elements_to_pages(elements, 1)[0][1]
    assert page.index("## Tabla resumen") < page.index("| A | B |") < page.index("Después")
