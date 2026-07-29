"""Tests for domain/finance_argentina/validator.py — domain lint check.

Spec: docs/design_finance_argentina.md §3, §8 `test_validator_gate`. Uses an
in-memory fake DatasetSource (conforms to domain.datasets.models.DatasetSource)
that carries both rows and per-category attributes — no LLM, no network, no disk.
"""

from datetime import date

from domain.datasets.models import DatasetRow
from domain.finance_argentina.requirements import parse_requirements_markdown
from domain.finance_argentina.validator import validate_workspace

_MANIFEST = """\
---
categorias:
  plazo_fijo:
    metricas: [TNA]
    attributes: [disponibilidad, plazos_dias, moneda, metodo_calculo, metrica_tasa]
  fci_money_market:
    metricas: [rendimiento]
    attributes: [disponibilidad, moneda, metodo_calculo, metrica_tasa]
---
"""

_PLAZO_FIJO_ATTRS = {
    "disponibilidad": "a_plazo",
    "plazos_dias": [30, 60, 90],
    "moneda": "ARS",
    "metodo_calculo": "interes_simple_vencimiento",
    "metrica_tasa": "TNA",
}

_FCI_ATTRS = {
    "disponibilidad": "inmediata",
    "moneda": "ARS",
    "metodo_calculo": "capitalizacion_diaria",
    "metrica_tasa": "rendimiento",
}


class _FakeDatasetSource:
    """Minimal in-memory DatasetSource — conforms to the engine Protocol."""

    def __init__(self, rows_by_categoria, attrs_by_categoria=None) -> None:
        self._rows = rows_by_categoria
        self._attrs = attrs_by_categoria or {}

    def categories(self) -> list[str]:
        return list(self._rows.keys())

    def query(self, categoria, *, clave=None, metrica=None, dims=None):
        rows = self._rows.get(categoria, [])
        if clave is not None:
            rows = [r for r in rows if r.clave == clave]
        if metrica is not None:
            rows = [r for r in rows if r.metrica == metrica]
        if dims is not None:
            rows = [r for r in rows if r.dims == dims]
        return rows

    def attributes(self, categoria):
        return self._attrs.get(categoria, {})


def _row(categoria: str, clave: str, metrica: str, valor: float, dims=None) -> DatasetRow:
    return DatasetRow(
        categoria=categoria,
        clave=clave,
        metrica=metrica,
        valor=valor,
        unidad="%",
        dims=dims or {},
        as_of=date(2026, 6, 25),
        fuente="test",
    )


def test_validator_all_pass() -> None:
    requirements = parse_requirements_markdown(_MANIFEST)
    source = _FakeDatasetSource(
        {
            "plazo_fijo": [_row("plazo_fijo", "Banco Nación", "TNA", 35.0, {"plazo": "30d"})],
            "fci_money_market": [_row("fci_money_market", "Mercado Fondos", "rendimiento", 41.0)],
        },
        {"plazo_fijo": _PLAZO_FIJO_ATTRS, "fci_money_market": _FCI_ATTRS},
    )

    report = validate_workspace(requirements, source)

    assert set(report.passing) == {"plazo_fijo", "fci_money_market"}
    assert report.failing == ()


# ── §8 test_validator_gate ──────────────────────────────────────────────────


def test_validator_gate_missing_metric_excludes_category() -> None:
    """Missing TNA -> plazo_fijo excluded + reason; the rest still validates."""
    requirements = parse_requirements_markdown(_MANIFEST)
    source = _FakeDatasetSource(
        {
            # plazo_fijo dataset has a row but the wrong metric (no TNA)
            "plazo_fijo": [_row("plazo_fijo", "Banco Nación", "rendimiento", 35.0)],
            "fci_money_market": [_row("fci_money_market", "Mercado Fondos", "rendimiento", 41.0)],
        },
        {"plazo_fijo": _PLAZO_FIJO_ATTRS, "fci_money_market": _FCI_ATTRS},
    )

    report = validate_workspace(requirements, source)

    assert "plazo_fijo" not in report.passing
    assert "fci_money_market" in report.passing
    reason = report.reason_for("plazo_fijo")
    assert "plazo_fijo" in reason
    assert "TNA" in reason


def test_validator_missing_dataset_entirely() -> None:
    requirements = parse_requirements_markdown(_MANIFEST)
    source = _FakeDatasetSource({})  # no rows, no attributes at all

    report = validate_workspace(requirements, source)

    assert report.passing == ()
    assert len(report.failing) == 2


def test_validator_missing_attributes() -> None:
    """A category with rows but no attributes (front-matter contract absent) fails."""
    requirements = parse_requirements_markdown(_MANIFEST)
    source = _FakeDatasetSource(
        {
            "plazo_fijo": [_row("plazo_fijo", "Banco Nación", "TNA", 35.0)],
            "fci_money_market": [_row("fci_money_market", "Mercado Fondos", "rendimiento", 41.0)],
        },
        {"plazo_fijo": _PLAZO_FIJO_ATTRS},  # fci_money_market attributes missing
    )

    report = validate_workspace(requirements, source)

    assert "plazo_fijo" in report.passing
    assert "fci_money_market" not in report.passing
    assert "atributo" in report.reason_for("fci_money_market")


def test_validator_missing_one_attribute() -> None:
    requirements = parse_requirements_markdown(_MANIFEST)
    source = _FakeDatasetSource(
        {
            "plazo_fijo": [_row("plazo_fijo", "Banco Nación", "TNA", 35.0)],
            "fci_money_market": [_row("fci_money_market", "Mercado Fondos", "rendimiento", 41.0)],
        },
        {"plazo_fijo": {"disponibilidad": "a_plazo"}, "fci_money_market": _FCI_ATTRS},
    )

    report = validate_workspace(requirements, source)

    assert "plazo_fijo" not in report.passing
    reason = report.reason_for("plazo_fijo")
    assert "atributo" in reason
