"""Tests for domain/finance_argentina/advisory.py — estimate_alternatives.

Spec: docs/design_finance_module.md §6-§8. In-memory manifest + concept pages
(written to tmp_path) + a fake DatasetSource — no LLM, no network.
"""

from datetime import date

import pytest

from domain.datasets.models import DatasetRow
from domain.finance_argentina.advisory import estimate_alternatives, render_markdown
from domain.finance_argentina.formulae import projected_gain, tea
from domain.finance_argentina.requirements import parse_requirements_markdown

_MANIFEST = """\
---
categorias:
  plazo_fijo:
    dataset:  { metricas: [TNA] }
    concept:  { attributes: [disponibilidad, plazos_dias, moneda, metodo_calculo, metrica_tasa] }
  fci_money_market:
    dataset:  { metricas: [rendimiento] }
    concept:  { attributes: [disponibilidad, moneda, metodo_calculo, metrica_tasa] }
  acciones:
    dataset:  { metricas: [precio] }
    concept:  { attributes: [disponibilidad, moneda, metodo_calculo, depende_de] }
---
"""

_PLAZO_FIJO_CONCEPT = """\
---
disponibilidad: a_plazo
plazos_dias: [30, 60, 90]
monto_minimo: 1000
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

_ACCIONES_CONCEPT = """\
---
disponibilidad: inmediata
moneda: ARS
metodo_calculo: no_deterministico
depende_de: [precio_mercado]
---
# Acciones
"""


class _FakeDatasetSource:
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


def _row(categoria, clave, metrica, valor, unidad="%", dims=None, as_of=date(2026, 6, 25), fuente="test"):
    return DatasetRow(
        categoria=categoria, clave=clave, metrica=metrica, valor=valor,
        unidad=unidad, dims=dims or {}, as_of=as_of, fuente=fuente,
    )


def _write_concepts(workspace, **pages: str) -> None:
    concepts_dir = workspace / "wiki" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    for slug, content in pages.items():
        (concepts_dir / f"{slug}.md").write_text(content, encoding="utf-8")


def _full_workspace(tmp_path):
    _write_concepts(
        tmp_path,
        plazo_fijo=_PLAZO_FIJO_CONCEPT,
        fci_money_market=_FCI_CONCEPT,
        acciones=_ACCIONES_CONCEPT,
    )
    source = _FakeDatasetSource(
        {
            "plazo_fijo": [
                _row("plazo_fijo", "Banco Nación", "TNA", 35.0, dims={"plazo": "30d"}),
                _row("plazo_fijo", "Banco Nación", "TNA", 36.0, dims={"plazo": "60d"}),
                _row("plazo_fijo", "Banco Nación", "TNA", 37.0, dims={"plazo": "90d"}),
                _row("plazo_fijo", "Banco Galicia", "TNA", 34.5, dims={"plazo": "30d"}),
            ],
            "fci_money_market": [
                _row("fci_money_market", "Mercado Fondos", "rendimiento", 41.0),
            ],
            "acciones": [
                _row("acciones", "GGAL", "precio", 5000.0, unidad="ARS"),
            ],
        }
    )
    return parse_requirements_markdown(_MANIFEST), source


# ── §8 test_eligibility_currency_min_horizon ────────────────────────────────


def test_eligibility_filters_by_currency(tmp_path) -> None:
    requirements, source = _full_workspace(tmp_path)
    result = estimate_alternatives(1_000_000, 3, requirements, source, tmp_path, moneda="USD")
    assert result.ranked == ()
    assert result.variable == ()


def test_eligibility_filters_by_monto_minimo(tmp_path) -> None:
    requirements, source = _full_workspace(tmp_path)
    # Below plazo_fijo's monto_minimo (1000) -> plazo_fijo excluded; fci has no minimum -> still eligible
    result = estimate_alternatives(500, 3, requirements, source, tmp_path, moneda="ARS")
    claves = {opt.clave for opt in result.ranked}
    assert "Banco Nación" not in claves
    assert "Mercado Fondos" in claves


def test_eligibility_filters_by_horizon_a_plazo(tmp_path) -> None:
    requirements, source = _full_workspace(tmp_path)
    # horizon_months=0 -> horizon_days=0; min(plazos_dias)=30 > 0 -> plazo_fijo not eligible.
    # fci is "inmediata" -> always eligible.
    result = estimate_alternatives(1_000_000, 0, requirements, source, tmp_path, moneda="ARS")
    claves = {opt.clave for opt in result.ranked}
    assert "Banco Nación" not in claves
    assert "Mercado Fondos" in claves


def test_eligibility_inmediata_always_eligible(tmp_path) -> None:
    requirements, source = _full_workspace(tmp_path)
    result = estimate_alternatives(1_000_000, 24, requirements, source, tmp_path, moneda="ARS")
    claves = {opt.clave for opt in result.ranked}
    assert "Mercado Fondos" in claves


# ── §8 test_non_deterministic_excluded_from_ranking ─────────────────────────


def test_non_deterministic_excluded_from_ranking(tmp_path) -> None:
    requirements, source = _full_workspace(tmp_path)
    result = estimate_alternatives(1_000_000, 3, requirements, source, tmp_path, moneda="ARS")

    ranked_categorias = {opt.categoria for opt in result.ranked}
    assert "acciones" not in ranked_categorias

    variable_categorias = {opt.categoria for opt in result.variable}
    assert "acciones" in variable_categorias
    acciones_var = next(v for v in result.variable if v.categoria == "acciones")
    assert acciones_var.depende_de == ("precio_mercado",)


# ── §8 test_empty_no_eligible ────────────────────────────────────────────────


def test_empty_no_eligible_options_is_honest(tmp_path) -> None:
    requirements, source = _full_workspace(tmp_path)
    # Currency mismatch -> nothing eligible at all.
    result = estimate_alternatives(1_000_000, 3, requirements, source, tmp_path, moneda="USD")

    assert result.ranked == ()
    assert result.variable == ()

    rendered = render_markdown(result)
    assert "No hay alternativas" in rendered
    assert "Sin alternativas de renta variable" in rendered


# ── §8 test_lists_all_eligible_ranked ────────────────────────────────────────


def test_lists_all_eligible_ranked(tmp_path) -> None:
    """Every eligible option listed, ranked by gain; entity/term/as_of/fuente carried."""
    requirements, source = _full_workspace(tmp_path)
    result = estimate_alternatives(1_000_000, 3, requirements, source, tmp_path, moneda="ARS")

    # plazo_fijo: Banco Nación (30/60/90d, best-fit 90d for horizon=90 days) + Banco Galicia (30d only)
    # fci_money_market: Mercado Fondos
    claves = [(opt.categoria, opt.clave, opt.term_label) for opt in result.ranked]
    assert ("plazo_fijo", "Banco Nación", "90d") in claves
    assert ("plazo_fijo", "Banco Galicia", "30d") in claves
    assert ("fci_money_market", "Mercado Fondos", "T+0") in claves

    # Ranked by gain, descending
    gains = [opt.gain for opt in result.ranked]
    assert gains == sorted(gains, reverse=True)

    # as_of/fuente carried through
    for opt in result.ranked:
        assert opt.as_of == "2026-06-25"
        assert opt.fuente == "test"

    # Hand-calc check for Banco Nación 90d TNA=37%
    expected_tea = tea("interes_simple_vencimiento", 0.37, term_days=90)
    expected_gain = projected_gain(1_000_000, expected_tea, 90)
    nacion_90d = next(
        opt for opt in result.ranked if opt.clave == "Banco Nación" and opt.term_label == "90d"
    )
    assert nacion_90d.tea == pytest.approx(expected_tea, abs=1e-4)
    assert nacion_90d.gain == pytest.approx(expected_gain, abs=1e-4)


def test_best_fit_term_picks_largest_fitting_term(tmp_path) -> None:
    """Banco Nación has 30/60/90d rows; horizon=45 days -> best fit is 30d (largest <= 45)."""
    requirements, source = _full_workspace(tmp_path)
    result = estimate_alternatives(1_000_000, 1.5, requirements, source, tmp_path, moneda="ARS")  # 45 days

    nacion_options = [opt for opt in result.ranked if opt.clave == "Banco Nación"]
    assert len(nacion_options) == 1
    assert nacion_options[0].term_label == "30d"


def test_excluded_category_reason_surfaced(tmp_path) -> None:
    requirements, source = _full_workspace(tmp_path)
    # Drop the fci concept page entirely -> fci_money_market fails validation.
    (tmp_path / "wiki" / "concepts" / "fci_money_market.md").unlink()

    result = estimate_alternatives(1_000_000, 3, requirements, source, tmp_path, moneda="ARS")

    assert any("fci_money_market" in reason for reason in result.excluded_reasons)
    fci_categorias = {opt.categoria for opt in result.ranked}
    assert "fci_money_market" not in fci_categorias

    rendered = render_markdown(result)
    assert "fci_money_market" in rendered
