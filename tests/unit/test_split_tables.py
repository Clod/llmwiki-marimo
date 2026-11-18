"""A table cut by a page break: the header of the table that ends a page is repeated at
the top of the table that starts the next one (`domain/ingestion/tables.py`).

The pages are the ones the extractor wrote for `05 Bonos Dólar Linked y Duales.docx` of
the finance example, measured on 2026-11-18: «Moneda de cotización», a data row, was the
header of the second half."""

from domain.ingestion.tables import repeat_split_table_headers

PAGE_2 = """## Ficha técnica

| Atributo | Valor |
| --- | --- |
| Tipo de instrumento | Renta Fija Soberana/Corporativa indexada al TC oficial |
"""
PAGE_3 = """| Moneda de cotización | Pesos (pagaderos en pesos ajustados al TC oficial A3500) |
| --- | --- |
| Exposición económica | Dólar oficial mayorista (BCRA Com. A3500) |

Texto después de la tabla.
"""


def test_the_second_half_takes_the_header_and_its_first_row_is_data_again():
    pages = repeat_split_table_headers([(2, PAGE_2), (3, PAGE_3)])
    assert pages[0] == (2, PAGE_2)
    assert pages[1] == (3, """| Atributo | Valor |
| --- | --- |
| Moneda de cotización | Pesos (pagaderos en pesos ajustados al TC oficial A3500) |
| Exposición económica | Dólar oficial mayorista (BCRA Com. A3500) |

Texto después de la tabla.
""")


def test_a_table_over_three_pages_takes_the_first_header_on_each():
    page_4 = "| Mercados | BYMA |\n| --- | --- |\n| Referencia | A3500 |\n"
    page_3 = "| Moneda | Pesos |\n| --- | --- |\n| Exposición | Dólar |"     # ends the page
    pages = repeat_split_table_headers([(2, PAGE_2), (3, page_3), (4, page_4)])
    assert pages[1][1].startswith("| Atributo | Valor |\n| --- | --- |\n| Moneda | Pesos |")
    assert pages[2][1].startswith("| Atributo | Valor |\n| --- | --- |\n| Mercados | BYMA |")


def test_text_between_the_tables_leaves_both_alone():
    page_3 = "Una tabla nueva:\n\n| Instrumento | Plazo |\n| --- | --- |\n| Lecap | 30 días |\n"
    assert repeat_split_table_headers([(2, PAGE_2), (3, page_3)])[1] == (3, page_3)


def test_text_after_the_table_on_the_first_page_leaves_the_next_alone():
    page_2 = PAGE_2 + "\nUn párrafo después de la tabla.\n"
    assert repeat_split_table_headers([(2, page_2), (3, PAGE_3)])[1] == (3, PAGE_3)


def test_other_columns_or_a_repeated_header_leave_the_page_alone():
    three = "| A | B | C |\n| --- | --- | --- |\n| 1 | 2 | 3 |\n"
    repeated = "| Atributo | Valor |\n| --- | --- |\n| Moneda | Pesos |\n"
    assert repeat_split_table_headers([(2, PAGE_2), (3, three)])[1] == (3, three)
    assert repeat_split_table_headers([(2, PAGE_2), (3, repeated)])[1] == (3, repeated)


def test_pages_without_tables_are_unchanged():
    pages = [(1, "Texto.\n"), (2, "Más texto.\n"), (3, "")]
    assert repeat_split_table_headers(pages) == pages
