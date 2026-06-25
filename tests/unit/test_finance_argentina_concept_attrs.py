"""Tests for domain/finance_argentina/concept_attrs.py — concept-page attribute reading.

Spec: docs/design_finance_module.md §4. In-memory concept-page strings only.
"""

from domain.finance_argentina.concept_attrs import parse_concept_attributes

_PLAZO_FIJO_CONCEPT = """\
---
tags: [entity]
disponibilidad: a_plazo
plazos_dias: [30, 60, 90]
monto_minimo: 1000
moneda: ARS
metodo_calculo: interes_simple_vencimiento
metrica_tasa: TNA
---

# Plazo Fijo
Body prose, not parsed here.
"""

_ACCIONES_CONCEPT = """\
---
disponibilidad: inmediata
moneda: ARS
metodo_calculo: no_deterministico
depende_de: [precio_mercado]
---

# Acciones
"""


def test_parse_concept_attributes_full() -> None:
    attrs = parse_concept_attributes(_PLAZO_FIJO_CONCEPT, page_slug="plazo_fijo")

    assert attrs.disponibilidad == "a_plazo"
    assert attrs.plazos_dias == (30, 60, 90)
    assert attrs.monto_minimo == 1000.0
    assert attrs.moneda == "ARS"
    assert attrs.metodo_calculo == "interes_simple_vencimiento"
    assert attrs.metrica_tasa == "TNA"
    assert attrs.depende_de == ()


def test_parse_concept_attributes_non_deterministic() -> None:
    attrs = parse_concept_attributes(_ACCIONES_CONCEPT, page_slug="acciones")

    assert attrs.disponibilidad == "inmediata"
    assert attrs.metodo_calculo == "no_deterministico"
    assert attrs.depende_de == ("precio_mercado",)
    assert attrs.metrica_tasa is None


def test_parse_concept_attributes_no_frontmatter_is_absent() -> None:
    attrs = parse_concept_attributes("# Just prose\nNo front-matter.")
    assert attrs.disponibilidad is None
    assert attrs.plazos_dias == ()
    assert attrs.has("disponibilidad") is False


def test_has_and_is_well_typed() -> None:
    attrs = parse_concept_attributes(_PLAZO_FIJO_CONCEPT, page_slug="plazo_fijo")

    assert attrs.has("disponibilidad") is True
    assert attrs.has("depende_de") is False  # empty tuple -> absent
    assert attrs.is_well_typed("disponibilidad") is True
    assert attrs.is_well_typed("metodo_calculo") is True


def test_is_well_typed_rejects_invalid_enum() -> None:
    bad = """\
---
disponibilidad: algun_dia
metodo_calculo: formula_inventada
---
"""
    attrs = parse_concept_attributes(bad)
    assert attrs.has("disponibilidad") is True
    assert attrs.is_well_typed("disponibilidad") is False
    assert attrs.is_well_typed("metodo_calculo") is False


def test_monto_minimo_defaults_to_zero() -> None:
    attrs = parse_concept_attributes("---\nmoneda: ARS\n---\n")
    assert attrs.monto_minimo == 0.0
