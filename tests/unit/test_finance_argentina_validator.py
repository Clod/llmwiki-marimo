"""Tests for domain/finance_argentina/validator.py — domain lint check.

Spec: docs/design_finance_module.md §3, §8 `test_validator_gate`. Uses an
in-memory fake DatasetSource (conforms to domain.datasets.models.DatasetSource)
plus real concept-page files written into tmp_path — no LLM, no network.
"""

from datetime import date

from domain.datasets.models import DatasetRow
from domain.finance_argentina.requirements import parse_requirements_markdown
from domain.finance_argentina.validator import validate_workspace

_MANIFEST = """\
---
categorias:
  plazo_fijo:
    dataset:  { metricas: [TNA] }
    concept:  { attributes: [disponibilidad, plazos_dias, moneda, metodo_calculo, metrica_tasa] }
  fci_money_market:
    dataset:  { metricas: [rendimiento] }
    concept:  { attributes: [disponibilidad, moneda, metodo_calculo, metrica_tasa] }
---
"""

_PLAZO_FIJO_CONCEPT = """\
---
disponibilidad: a_plazo
plazos_dias: [30, 60, 90]
moneda: ARS
metodo_calculo: interes_simple_vencimiento
metrica_tasa: TNA
---
# Plazo Fijo
"""

_FCI_CONCEPT = """\
---
disponibilidad: inmediata
moneda: ARS
metodo_calculo: capitalizacion_diaria
metrica_tasa: rendimiento
---
# FCI Money Market
"""


class _FakeDatasetSource:
    """Minimal in-memory DatasetSource — conforms to the engine Protocol."""

    def __init__(self, rows_by_categoria: dict[str, list[DatasetRow]]) -> None:
        self._rows = rows_by_categoria

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


def _write_concepts(workspace, **pages: str) -> None:
    concepts_dir = workspace / "wiki" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    for slug, content in pages.items():
        (concepts_dir / f"{slug}.md").write_text(content, encoding="utf-8")


def test_validator_all_pass(tmp_path) -> None:
    requirements = parse_requirements_markdown(_MANIFEST)
    _write_concepts(
        tmp_path, plazo_fijo=_PLAZO_FIJO_CONCEPT, fci_money_market=_FCI_CONCEPT
    )
    source = _FakeDatasetSource(
        {
            "plazo_fijo": [_row("plazo_fijo", "Banco Nación", "TNA", 35.0, {"plazo": "30d"})],
            "fci_money_market": [_row("fci_money_market", "Mercado Fondos", "rendimiento", 41.0)],
        }
    )

    report = validate_workspace(requirements, source, tmp_path)

    assert set(report.passing) == {"plazo_fijo", "fci_money_market"}
    assert report.failing == ()


# ── §8 test_validator_gate ──────────────────────────────────────────────────


def test_validator_gate_missing_metric_excludes_category(tmp_path) -> None:
    """Missing TNA -> plazo_fijo excluded + reason; the rest still validates."""
    requirements = parse_requirements_markdown(_MANIFEST)
    _write_concepts(
        tmp_path, plazo_fijo=_PLAZO_FIJO_CONCEPT, fci_money_market=_FCI_CONCEPT
    )
    source = _FakeDatasetSource(
        {
            # plazo_fijo dataset has a row but the wrong metric (no TNA)
            "plazo_fijo": [_row("plazo_fijo", "Banco Nación", "rendimiento", 35.0)],
            "fci_money_market": [_row("fci_money_market", "Mercado Fondos", "rendimiento", 41.0)],
        }
    )

    report = validate_workspace(requirements, source, tmp_path)

    assert "plazo_fijo" not in report.passing
    assert "fci_money_market" in report.passing
    reason = report.reason_for("plazo_fijo")
    assert "plazo_fijo" in reason
    assert "TNA" in reason


def test_validator_missing_dataset_entirely(tmp_path) -> None:
    requirements = parse_requirements_markdown(_MANIFEST)
    _write_concepts(tmp_path, plazo_fijo=_PLAZO_FIJO_CONCEPT, fci_money_market=_FCI_CONCEPT)
    source = _FakeDatasetSource({})  # no datasets at all

    report = validate_workspace(requirements, source, tmp_path)

    assert report.passing == ()
    assert len(report.failing) == 2


def test_validator_missing_concept_page(tmp_path) -> None:
    requirements = parse_requirements_markdown(_MANIFEST)
    _write_concepts(tmp_path, plazo_fijo=_PLAZO_FIJO_CONCEPT)  # fci concept missing
    source = _FakeDatasetSource(
        {
            "plazo_fijo": [_row("plazo_fijo", "Banco Nación", "TNA", 35.0)],
            "fci_money_market": [_row("fci_money_market", "Mercado Fondos", "rendimiento", 41.0)],
        }
    )

    report = validate_workspace(requirements, source, tmp_path)

    assert "plazo_fijo" in report.passing
    assert "fci_money_market" not in report.passing
    assert "concepto" in report.reason_for("fci_money_market")


def test_validator_missing_concept_attribute(tmp_path) -> None:
    requirements = parse_requirements_markdown(_MANIFEST)
    incomplete_concept = "---\ndisponibilidad: a_plazo\n---\n# Plazo Fijo incompleto\n"
    _write_concepts(tmp_path, plazo_fijo=incomplete_concept, fci_money_market=_FCI_CONCEPT)
    source = _FakeDatasetSource(
        {
            "plazo_fijo": [_row("plazo_fijo", "Banco Nación", "TNA", 35.0)],
            "fci_money_market": [_row("fci_money_market", "Mercado Fondos", "rendimiento", 41.0)],
        }
    )

    report = validate_workspace(requirements, source, tmp_path)

    assert "plazo_fijo" not in report.passing
    reason = report.reason_for("plazo_fijo")
    assert "atributo" in reason
