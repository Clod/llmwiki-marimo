"""Tests for domain/finance_argentina/instrument_attrs.py — attribute interpretation.

Spec: docs/design_finance_argentina.md §4. Inputs are already-parsed front-matter
mappings (as a DatasetSource.attributes() returns) — YAML parsing lives in the
datasets layer, so these tests pass dicts directly.
"""

from domain.finance_argentina.instrument_attrs import attributes_from_meta

_PLAZO_FIJO_META = {
    "tags": ["entity"],  # engine/other keys are ignored by the finance overlay
    "disponibilidad": "a_plazo",
    "plazos_dias": [30, 60, 90],
    "monto_minimo": 1000,
    "moneda": "ARS",
    "metodo_calculo": "interes_simple_vencimiento",
    "metrica_tasa": "TNA",
}

_ACCIONES_META = {
    "disponibilidad": "inmediata",
    "moneda": "ARS",
    "metodo_calculo": "no_deterministico",
    "depende_de": ["precio_mercado"],
}


def test_attributes_from_meta_full() -> None:
    attrs = attributes_from_meta(_PLAZO_FIJO_META, categoria="plazo_fijo")

    assert attrs.disponibilidad == "a_plazo"
    assert attrs.plazos_dias == (30, 60, 90)
    assert attrs.monto_minimo == 1000.0
    assert attrs.moneda == "ARS"
    assert attrs.metodo_calculo == "interes_simple_vencimiento"
    assert attrs.metrica_tasa == "TNA"
    assert attrs.depende_de == ()


def test_attributes_from_meta_non_deterministic() -> None:
    attrs = attributes_from_meta(_ACCIONES_META, categoria="acciones")

    assert attrs.disponibilidad == "inmediata"
    assert attrs.metodo_calculo == "no_deterministico"
    assert attrs.depende_de == ("precio_mercado",)
    assert attrs.metrica_tasa is None


def test_empty_mapping_is_absent() -> None:
    attrs = attributes_from_meta({})
    assert attrs.disponibilidad is None
    assert attrs.plazos_dias == ()
    assert attrs.has("disponibilidad") is False


def test_has_and_is_well_typed() -> None:
    attrs = attributes_from_meta(_PLAZO_FIJO_META, categoria="plazo_fijo")

    assert attrs.has("disponibilidad") is True
    assert attrs.has("depende_de") is False  # empty tuple -> absent
    assert attrs.is_well_typed("disponibilidad") is True
    assert attrs.is_well_typed("metodo_calculo") is True


def test_is_well_typed_rejects_invalid_enum() -> None:
    attrs = attributes_from_meta(
        {"disponibilidad": "algun_dia", "metodo_calculo": "formula_inventada"}
    )
    assert attrs.has("disponibilidad") is True
    assert attrs.is_well_typed("disponibilidad") is False
    assert attrs.is_well_typed("metodo_calculo") is False


def test_monto_minimo_defaults_to_zero() -> None:
    attrs = attributes_from_meta({"moneda": "ARS"})
    assert attrs.monto_minimo == 0.0
