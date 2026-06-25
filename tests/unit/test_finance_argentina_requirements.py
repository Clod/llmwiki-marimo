"""Tests for domain/finance_argentina/requirements.py — manifest parsing.

Spec: docs/design_finance_module.md §2. In-memory manifest strings only.
"""

from domain.finance_argentina.requirements import load_requirements_file, parse_requirements_markdown

_MANIFEST = """\
---
categorias:
  plazo_fijo:
    dataset:  { metricas: [TNA] }
    concept:  { attributes: [disponibilidad, plazos_dias, moneda, metodo_calculo, metrica_tasa] }
  acciones:
    dataset:  { metricas: [precio] }
    concept:  { attributes: [disponibilidad, moneda, metodo_calculo, depende_de] }
---
# Finance requirements
Prose documentation, not machine-read.
"""


def test_parse_requirements_markdown_basic() -> None:
    reqs = parse_requirements_markdown(_MANIFEST)

    assert set(reqs.category_names()) == {"plazo_fijo", "acciones"}

    plazo_fijo = reqs.categorias["plazo_fijo"]
    assert plazo_fijo.metricas == ("TNA",)
    assert plazo_fijo.attributes == (
        "disponibilidad",
        "plazos_dias",
        "moneda",
        "metodo_calculo",
        "metrica_tasa",
    )

    acciones = reqs.categorias["acciones"]
    assert acciones.metricas == ("precio",)
    assert "depende_de" in acciones.attributes


def test_parse_requirements_markdown_no_frontmatter_is_empty() -> None:
    reqs = parse_requirements_markdown("# Just prose\nNo front-matter here.")
    assert reqs.category_names() == ()


def test_parse_requirements_markdown_skips_malformed_category() -> None:
    text = """\
---
categorias:
  plazo_fijo:
    dataset:  { metricas: [TNA] }
    concept:  { attributes: [disponibilidad] }
  broken_category:
    dataset:  { metricas: [TNA] }
    # missing concept block entirely
---
"""
    reqs = parse_requirements_markdown(text)
    assert set(reqs.category_names()) == {"plazo_fijo"}


def test_load_requirements_file_seed_manifest() -> None:
    """The actual shipped base/domain/finance_argentina/requirements.md parses with the v1 seed categories."""
    from pathlib import Path

    manifest_path = Path(__file__).parent.parent.parent / "base" / "domain" / "finance_argentina" / "requirements.md"
    reqs = load_requirements_file(manifest_path)

    expected = {"plazo_fijo", "fci_money_market", "caucion", "acciones", "dolar"}
    assert expected.issubset(set(reqs.category_names()))

    fci = reqs.categorias["fci_money_market"]
    assert fci.metricas == ("rendimiento",)

    dolar = reqs.categorias["dolar"]
    assert set(dolar.metricas) == {"compra", "venta"}


def test_load_requirements_file_missing_path_is_empty(tmp_path) -> None:
    reqs = load_requirements_file(tmp_path / "does_not_exist.md")
    assert reqs.category_names() == ()
