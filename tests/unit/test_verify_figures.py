"""The figure check (`domain/verify/figures.py`): which numbers of an answer are figures,
which values authorise them and how each figure is classified. No model, no network."""

from __future__ import annotations

from decimal import Decimal

import pytest

from domain.verify.figures import (
    CURRENCY_WORDS,
    MAX_COUNT,
    EXACT,
    ROUNDED,
    UNSUPPORTED,
    authorised_values,
    check_figures,
    figures_in,
)

DATASET = (
    "**3 dataset row(s)** for categoria='tasas':\n\n"
    "| clave | metrica | valor | unidad | dims | as_of | fuente |\n"
    "|---|---|---|---|---|---|---|\n"
    "| peso | tna | 1187.5 | ARS | plazo=30 | 2026-10-01 | bcra.gob.ar |\n"
    "| peso | tna | 41.84 | % | plazo=60 | 2026-10-01 | bcra.gob.ar |\n"
    "| peso | tna | 0.4 | % | - | 2026-10-01 | bcra.gob.ar |\n"
)
ADVISORY = (
    "Para $1,000,000 a ~30 días (ARS), si la tasa actual se mantiene:\n\n"
    "| opción | clave | plazo | TEA | ganancia est. | al fecha | fuente |\n"
    "|---|---|---|---|---|---|---|\n"
    "| plazo_fijo | banco | 30 | 12.34% | $9,500 | 2026-10-01 | bcra.gob.ar |\n"
)


def statuses(answer, *sources, skip=(), tolerance=0.05):
    report = check_figures(answer, list(sources), skip=skip, tolerance=tolerance)
    return [(f.figure.text, f.status) for f in report.findings]


# ── reading the figures of a text ────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("1.187,5", [Decimal("1187.5")]),                       # both separators: the last is decimal
    ("1,234.5", [Decimal("1234.5")]),
    ("1.000.000", [Decimal("1000000")]),                    # one kind, repeated: thousands
    ("41,84", [Decimal("41.84")]),                          # one separator, not three digits: decimal
    ("1.200", [Decimal("1200"), Decimal("1.200")]),         # one separator, three digits: ambiguous
    ("0.125", [Decimal("125"), Decimal("0.125")]),
    ("90000", [Decimal("90000")]),
])
def test_the_separator_rule_gives_one_or_two_values(text, expected):
    (figure,) = figures_in(text)
    assert list(figure.candidates) == expected


def test_a_dollar_sign_before_and_a_percent_sign_after_belong_to_the_figure():
    answer = "pagan $90,000 y rinden 12% o 3,5 %"
    assert [f.text for f in figures_in(answer)] == ["$90,000", "12%", "3,5"]
    for figure in figures_in(answer):
        assert answer[figure.start:figure.end] == figure.text


@pytest.mark.parametrize("answer", [
    "el archivo informe_cnv_2024.pdf",          # a letter or an underscore touches the number
    "el nodo Q13",
    "la sigla R2a",
    "la página concepts/x2.md",
    "al 1 de octubre de 2026",                  # dates
    "el 2026-10-01",
    "el 15/03/2026",
    "on October 1, 2026",
    "1. Plazo fijo",                            # ordered-list marker
    "2) Caución",
    "en https://example.com/a/123.456 y más",   # URL
    "ver [la tabla](tabla-2025-11.md)",         # link target
    "en 2026",                                  # a year alone
    "las 3 opciones",                           # a count
    "2 bancos",
])
def test_what_is_not_a_figure(answer):
    assert figures_in(answer) == []


@pytest.mark.parametrize("answer, text", [
    ("13 bancos", "13"),                        # above the limit of a count
    ("1%", "1%"),                               # a count with a percent sign
    ("$5", "$5"),                               # a count with a dollar sign
    ("5 dólares", "5"),                         # a count with a currency word
    ("100 USD", "100"),
    ("2026 pesos", "2026"),                     # a year with a unit
    ("0%", "0%"),
])
def test_what_is_a_figure(answer, text):
    assert [f.text for f in figures_in(answer)] == [text]


def test_a_list_marker_does_not_hide_the_figures_of_the_item():
    assert [f.text for f in figures_in("1. Plazo fijo al 41,84%\n2. Caución")] == ["41,84%"]


def test_the_limit_of_a_count_and_the_currency_words_are_constants():
    assert MAX_COUNT == 12 and "dólares" in CURRENCY_WORDS and "pesos" in CURRENCY_WORDS
    assert figures_in(f"{MAX_COUNT} bancos") == [] and figures_in(f"{MAX_COUNT + 1} bancos") != []


def test_the_spans_to_skip_are_not_read():
    answer = "rinde 41,84% y 99,5%"
    skip = [(answer.index("99,5"), len(answer))]
    assert [f.text for f in figures_in(answer, skip)] == ["41,84%"]


# ── the authorised set ───────────────────────────────────────────────────────

def values(*sources):
    return {a.value for a in authorised_values(sources)}


def test_a_dataset_table_authorises_only_the_columns_valor_and_dims():
    found = values(("datasets/tasas.md", DATASET))
    assert {Decimal("1187.5"), Decimal("41.84"), Decimal("0.4"), Decimal(30), Decimal(60)} <= found
    assert Decimal(3) not in found                  # the row count of `**3 dataset row(s)**`
    assert Decimal(1) not in found and Decimal(10) not in found     # the digits of as_of


def test_the_advisory_table_authorises_every_cell_but_the_date_and_the_source():
    found = values(("estimar_alternativas", ADVISORY))
    assert {Decimal("12.34"), Decimal(9500), Decimal(1000000), Decimal(30)} <= found
    assert Decimal(10) not in found


def test_an_ambiguous_token_authorises_both_readings_as_absolute_values():
    assert values(("p", "Python 0.125 y -3.5")) == {Decimal(125), Decimal("0.125"), Decimal("3.5")}


def test_a_value_keeps_the_origin_of_the_first_source_that_holds_it():
    found = authorised_values([("a", "vale 7"), ("b", "vale 7 y 8")])
    assert [(x.value, x.origin) for x in found] == [(Decimal(7), "a"), (Decimal(8), "b")]


def test_a_date_in_a_source_gives_no_authorised_value():
    assert values(("p", "updated: 2026-10-01")) == set()
    assert values(("p", "al 1 de octubre de 2026, 15/10/2026")) == set()


def test_a_year_alone_in_a_source_is_read_like_any_number():
    assert Decimal(2024) in values(("p", "la ley de 2024"))


# ── classifying ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("answer, source, expected", [
    ("1.187,5", ("datasets/tasas.md", DATASET), [("1.187,5", EXACT)]),
    ("cerca de 1.200", ("datasets/tasas.md", DATASET), [("1.200", ROUNDED)]),
    ("1.190", ("datasets/tasas.md", DATASET), [("1.190", ROUNDED)]),
    ("1.150", ("datasets/tasas.md", DATASET), [("1.150", UNSUPPORTED)]),        # wrong rounding
    ("1.000", ("datasets/tasas.md", DATASET), [("1.000", UNSUPPORTED)]),        # 15.8 % > 5 %
    ("1.187,52", ("datasets/tasas.md", DATASET), [("1.187,52", UNSUPPORTED)]),  # more precision than the data
    ("0,125", ("p", "Python 0.125"), [("0,125", EXACT)]),
    ("12,3 %", ("estimar_alternativas", ADVISORY), [("12,3", ROUNDED)]),
    ("tus $100.000", ("pregunta", "100.000 pesos"), [("$100.000", EXACT)]),
    ("cayó 3,5%", ("p", "variación -3.5"), [("3,5%", EXACT)]),
    ("0%", ("p", "rinde 0.4"), [("0%", UNSUPPORTED)]),                           # zero never rounds
    ("13 bancos", ("p", "hay bancos"), [("13", UNSUPPORTED)]),
    ("5 dólares", ("p", "hay bancos"), [("5", UNSUPPORTED)]),
])
def test_each_figure_is_exact_rounded_or_unsupported(answer, source, expected):
    assert statuses(answer, source) == expected


def test_the_motivating_case_an_invented_one_percent_is_unsupported():
    peso = DATASET.replace("1187.5", "34.5").replace("41.84", "36.2").replace("0.4", "33.9")
    answer = "las tasas en dólares suelen ser mucho menores, a menudo por debajo del 1% anual"
    report = check_figures(answer, [("datasets/tasas.md", peso)])
    (finding,) = report.unsupported()
    assert finding.figure.text == "1%"
    assert finding.nearest.origin == "datasets/tasas.md"


def test_the_motivating_case_stays_unsupported_when_a_dated_page_was_read():
    page = "---\nupdated: 2026-10-01\n---\n# Caución\nRinde una tasa nominal anual."
    answer = "a menudo por debajo del 1% anual"
    (finding,) = check_figures(answer, [("/wiki/concepts/caucion.md", page)]).unsupported()
    assert finding.figure.text == "1%"


def test_the_nearest_value_of_an_unsupported_figure_has_the_smallest_relative_difference():
    (finding,) = check_figures("1.150", [("a", "1187.5"), ("b", "900")]).findings
    assert (finding.status, finding.nearest.value, finding.nearest.origin) == (UNSUPPORTED, Decimal("1187.5"), "a")


def test_a_tie_for_the_nearest_value_goes_to_the_first_source():
    (finding,) = check_figures("100", [("a", "80"), ("b", "125")]).findings
    assert (finding.nearest.value, finding.nearest.origin) == (Decimal(80), "a")   # 20/100 and 25/125


def test_an_empty_authorised_set_has_no_nearest_value():
    (finding,) = check_figures("1.187,5", []).findings
    assert (finding.status, finding.nearest) == (UNSUPPORTED, None)


def test_the_tolerance_is_a_parameter():
    source = ("p", "1187.5")
    assert statuses("1.000", source, tolerance=0.2) == [("1.000", ROUNDED)]
    assert statuses("1.200", source, tolerance=0.001) == [("1.200", UNSUPPORTED)]


def test_a_figure_inside_a_skipped_span_is_not_classified():
    answer = "rinde 41,84%\n\n| a |\n| 77,7 |"
    skip = [(answer.index("| a |"), len(answer))]
    assert statuses(answer, ("p", "41.84"), skip=skip) == [("41,84%", EXACT)]


def test_the_report_lists_the_unsupported_and_the_rounded_figures():
    report = check_figures("1.200 y 7.777 y 1.187,5", [("p", "1187.5")])
    assert [f.figure.text for f in report.unsupported()] == ["7.777"]
    assert [f.figure.text for f in report.rounded()] == ["1.200"]
