"""Tests for domain/datasets/source.py — LocalMarkdownSource (parse-on-read).

Spec: .trellis/spec/backend/datasets-format.md §2.3 (normalized row),
docs/design_datasets.md §3.1 (DatasetSource Protocol). Uses a real tmp_path
directory with markdown dataset files — no LLM, no network.
"""

from datetime import date

from domain.datasets.models import DatasetRow
from domain.datasets.source import LocalMarkdownSource

_PLAZO_FIJO = """\
---
type: dataset
categoria: plazo_fijo
formato: matriz
clave: entidad
columnas_dim: plazo
metrica: TNA
unidad: "%"
as_of: 2026-06-25
fuente: bna.com.ar
---

| entidad        | 30d   | 60d   |
|----------------|-------|-------|
| Banco Nación   | 35.00 | 36.00 |
| Banco Galicia  | 34.50 | 35.50 |
"""

_DOLAR = """\
---
type: dataset
categoria: dolar
formato: largo
clave: tipo
metricas: { compra: "ARS", venta: "ARS" }
as_of: 2026-06-25
fuente: ámbito
---

| tipo    | compra | venta |
|---------|--------|-------|
| Oficial | 1000   | 1050  |
| MEP     | 1180   | 1185  |
"""

_NOT_A_DATASET = """\
---
title: Plazo Fijo (concept)
tags: [concept]
---

# Plazo Fijo

Prose, not a dataset.
"""


def _make_datasets_dir(tmp_path):
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    (datasets_dir / "plazo_fijo.md").write_text(_PLAZO_FIJO, encoding="utf-8")
    (datasets_dir / "dolar.md").write_text(_DOLAR, encoding="utf-8")
    (datasets_dir / "not_a_dataset.md").write_text(_NOT_A_DATASET, encoding="utf-8")
    return datasets_dir


def test_categories_lists_only_valid_dataset_files(tmp_path) -> None:
    datasets_dir = _make_datasets_dir(tmp_path)
    source = LocalMarkdownSource(datasets_dir)
    assert sorted(source.categories()) == ["dolar", "plazo_fijo"]


def test_query_by_categoria_returns_all_rows(tmp_path) -> None:
    datasets_dir = _make_datasets_dir(tmp_path)
    source = LocalMarkdownSource(datasets_dir)
    rows = source.query("dolar")
    assert len(rows) == 4
    assert all(isinstance(r, DatasetRow) for r in rows)


def test_query_filters_by_clave(tmp_path) -> None:
    datasets_dir = _make_datasets_dir(tmp_path)
    source = LocalMarkdownSource(datasets_dir)
    rows = source.query("dolar", clave="MEP")
    assert len(rows) == 2
    assert all(r.clave == "MEP" for r in rows)


def test_query_filters_by_metrica(tmp_path) -> None:
    datasets_dir = _make_datasets_dir(tmp_path)
    source = LocalMarkdownSource(datasets_dir)
    rows = source.query("dolar", metrica="compra")
    assert len(rows) == 2
    assert all(r.metrica == "compra" for r in rows)


def test_query_filters_by_dims(tmp_path) -> None:
    datasets_dir = _make_datasets_dir(tmp_path)
    source = LocalMarkdownSource(datasets_dir)
    rows = source.query("plazo_fijo", dims={"plazo": "30d"})
    assert len(rows) == 2
    assert all(r.dims == {"plazo": "30d"} for r in rows)
    assert {r.clave for r in rows} == {"Banco Nación", "Banco Galicia"}


def test_query_unknown_categoria_returns_empty(tmp_path) -> None:
    datasets_dir = _make_datasets_dir(tmp_path)
    source = LocalMarkdownSource(datasets_dir)
    assert source.query("nonexistent") == []


def test_query_combined_filters(tmp_path) -> None:
    datasets_dir = _make_datasets_dir(tmp_path)
    source = LocalMarkdownSource(datasets_dir)
    rows = source.query("plazo_fijo", clave="Banco Nación", dims={"plazo": "60d"})
    assert len(rows) == 1
    row = rows[0]
    assert row.valor == 36.00
    assert row.unidad == "%"
    assert row.as_of == date(2026, 6, 25)
    assert row.fuente == "bna.com.ar"


def test_parse_on_read_reflects_file_changes(tmp_path) -> None:
    """No caching: editing the file on disk is reflected on the next query()."""
    datasets_dir = _make_datasets_dir(tmp_path)
    source = LocalMarkdownSource(datasets_dir)
    rows_before = source.query("dolar", clave="MEP", metrica="compra")
    assert rows_before[0].valor == 1180

    (datasets_dir / "dolar.md").write_text(
        _DOLAR.replace("MEP     | 1180   | 1185", "MEP     | 1300   | 1305"),
        encoding="utf-8",
    )
    rows_after = source.query("dolar", clave="MEP", metrica="compra")
    assert rows_after[0].valor == 1300


def test_query_rejects_path_traversal(tmp_path) -> None:
    """categoria is LLM-supplied (via query_dataset); it must not escape datasets/.

    A *valid* dataset placed outside datasets/ would leak its rows if a `..`
    categoria reached it — the traversal guard must reject it.
    """
    datasets_dir = _make_datasets_dir(tmp_path)
    (tmp_path / "secret.md").write_text(
        _DOLAR.replace("categoria: dolar", "categoria: secret"), encoding="utf-8"
    )
    source = LocalMarkdownSource(datasets_dir)

    assert source.query("../secret") == []        # parent-dir escape
    assert source.query("/etc/passwd") == []       # absolute path
    assert source.query("plazo_fijo")              # legitimate categoria still works
