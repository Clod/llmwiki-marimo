"""Tests for domain/datasets/parser.py — dataset markdown → normalized rows.

Spec: .trellis/spec/backend/datasets-format.md §2-§3, §5 (Validation & Error
Matrix), §7 (Tests Required). All inputs are deterministic in-memory markdown
strings — no LLM, no network, no filesystem.
"""

import logging
from datetime import date

from domain.datasets.parser import parse_dataset_markdown

# ── §7 test_parse_matriz ──────────────────────────────────────────────────────


def test_parse_matriz() -> None:
    """key x dim grid -> N x M rows; metrica/unidad/dims/as_of/fuente correct."""
    text = """\
---
type: dataset
categoria: plazo_fijo
formato: matriz
clave: entidad
columnas_dim: plazo
metrica: TNA
unidad: "%"
as_of: 2026-06-25
fuente: bna.com.ar, galicia.com.ar
---

| entidad        | 30d   | 60d   | 90d   |
|----------------|-------|-------|-------|
| Banco Nación   | 35.00 | 36.00 | 37.00 |
| Banco Galicia  | 34.50 | 35.50 | 36.50 |
"""
    rows = parse_dataset_markdown(text, filename_slug="plazo_fijo")

    assert len(rows) == 6  # 2 keys x 3 dim columns

    nacion_30d = next(r for r in rows if r.clave == "Banco Nación" and r.dims == {"plazo": "30d"})
    assert nacion_30d.categoria == "plazo_fijo"
    assert nacion_30d.metrica == "TNA"
    assert nacion_30d.valor == 35.00
    assert nacion_30d.unidad == "%"
    assert nacion_30d.as_of == date(2026, 6, 25)
    assert nacion_30d.fuente == "bna.com.ar, galicia.com.ar"

    galicia_90d = next(r for r in rows if r.clave == "Banco Galicia" and r.dims == {"plazo": "90d"})
    assert galicia_90d.valor == 36.50
    assert galicia_90d.metrica == "TNA"
    assert galicia_90d.unidad == "%"


# ── §7 test_parse_largo ───────────────────────────────────────────────────────


def test_parse_largo() -> None:
    """Each metricas column -> one row per key; dimensiones captured into dims; units correct."""
    text = """\
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
    rows = parse_dataset_markdown(text, filename_slug="dolar")

    assert len(rows) == 4  # 2 keys x 2 metrics

    mep_compra = next(r for r in rows if r.clave == "MEP" and r.metrica == "compra")
    assert mep_compra.valor == 1180
    assert mep_compra.unidad == "ARS"
    assert mep_compra.dims == {}
    assert mep_compra.as_of == date(2026, 6, 25)
    assert mep_compra.fuente == "ámbito"

    oficial_venta = next(r for r in rows if r.clave == "Oficial" and r.metrica == "venta")
    assert oficial_venta.valor == 1050
    assert oficial_venta.unidad == "ARS"


def test_parse_largo_with_dimensiones() -> None:
    """dimensiones columns are captured into dims, not emitted as metric rows."""
    text = """\
---
type: dataset
categoria: fci
formato: largo
clave: nombre
metricas: { tna: "%" }
dimensiones: [moneda]
as_of: 2026-06-25
fuente: bcra
---

| nombre  | moneda | tna  |
|---------|--------|------|
| Fondo A | ARS    | 40.0 |
"""
    rows = parse_dataset_markdown(text, filename_slug="fci")

    assert len(rows) == 1
    row = rows[0]
    assert row.clave == "Fondo A"
    assert row.metrica == "tna"
    assert row.valor == 40.0
    assert row.dims == {"moneda": "ARS"}


# ── §7 test_type_discriminator ────────────────────────────────────────────────


def test_type_discriminator() -> None:
    """A concept page (no type: dataset) yields zero dataset rows."""
    text = """\
---
title: Plazo Fijo
tags: [concept]
---

# Plazo Fijo

Plain concept content, not a dataset.
"""
    rows = parse_dataset_markdown(text, filename_slug="plazo_fijo")
    assert rows == []


def test_no_frontmatter_yields_zero_rows() -> None:
    """A plain markdown file with no front-matter at all is not a dataset."""
    text = "# Just a page\n\nNo front-matter here.\n"
    rows = parse_dataset_markdown(text, filename_slug="plazo_fijo")
    assert rows == []


# ── §7 test_missing_frontmatter_key ───────────────────────────────────────────


def test_missing_frontmatter_key_formato(caplog: object) -> None:
    """Missing formato -> file rejected, error logged, no rows."""
    text = """\
---
type: dataset
categoria: plazo_fijo
as_of: 2026-06-25
fuente: bna.com.ar
---

| entidad      | 30d  |
|--------------|------|
| Banco Nación | 35.0 |
"""
    with caplog.at_level(logging.ERROR, logger="wiki"):
        rows = parse_dataset_markdown(text, filename_slug="plazo_fijo")
    assert rows == []
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_missing_matrix_key_rejected() -> None:
    """matriz missing clave/columnas_dim/metrica/unidad -> file rejected, no rows."""
    text = """\
---
type: dataset
categoria: plazo_fijo
formato: matriz
as_of: 2026-06-25
fuente: bna.com.ar
---

| entidad      | 30d  |
|--------------|------|
| Banco Nación | 35.0 |
"""
    rows = parse_dataset_markdown(text, filename_slug="plazo_fijo")
    assert rows == []


def test_missing_largo_key_rejected() -> None:
    """largo missing clave or metricas -> file rejected, no rows."""
    text = """\
---
type: dataset
categoria: dolar
formato: largo
as_of: 2026-06-25
fuente: ambito
---

| tipo    | compra |
|---------|--------|
| Oficial | 1000   |
"""
    rows = parse_dataset_markdown(text, filename_slug="dolar")
    assert rows == []


def test_unknown_formato_rejected() -> None:
    """formato not in {matriz, largo} -> file rejected."""
    text = """\
---
type: dataset
categoria: dolar
formato: ancho
clave: tipo
metricas: { compra: "ARS" }
as_of: 2026-06-25
fuente: ambito
---

| tipo    | compra |
|---------|--------|
| Oficial | 1000   |
"""
    rows = parse_dataset_markdown(text, filename_slug="dolar")
    assert rows == []


def test_clave_column_absent_from_header_rejected() -> None:
    """clave column declared in front-matter but absent from the table header -> rejected."""
    text = """\
---
type: dataset
categoria: dolar
formato: largo
clave: moneda_tipo
metricas: { compra: "ARS" }
as_of: 2026-06-25
fuente: ambito
---

| tipo    | compra |
|---------|--------|
| Oficial | 1000   |
"""
    rows = parse_dataset_markdown(text, filename_slug="dolar")
    assert rows == []


# ── §7 test_non_numeric_cell ──────────────────────────────────────────────────


def test_non_numeric_cell(caplog: object) -> None:
    """A non-numeric cell -> that row/cell skipped, warning logged, other rows still parsed."""
    text = """\
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

| entidad      | 30d   |
|--------------|-------|
| Banco Nación | 35%   |
| Banco Galicia| 34.50 |
"""
    with caplog.at_level(logging.WARNING, logger="wiki"):
        rows = parse_dataset_markdown(text, filename_slug="plazo_fijo")

    assert len(rows) == 1
    assert rows[0].clave == "Banco Galicia"
    assert rows[0].valor == 34.50
    assert any(r.levelno == logging.WARNING for r in caplog.records)


# ── §7 test_per_row_override ──────────────────────────────────────────────────


def test_per_row_override() -> None:
    """largo row with as_of/fuente columns overrides file defaults."""
    text = """\
---
type: dataset
categoria: dolar
formato: largo
clave: tipo
metricas: { compra: "ARS", venta: "ARS" }
as_of: 2026-06-25
fuente: ámbito
---

| tipo    | compra | venta | as_of      | fuente   |
|---------|--------|-------|------------|----------|
| Oficial | 1000   | 1050  | 2026-06-25 | ámbito   |
| MEP     | 1180   | 1185  | 2026-06-24 | bluelytics |
"""
    rows = parse_dataset_markdown(text, filename_slug="dolar")

    mep_compra = next(r for r in rows if r.clave == "MEP" and r.metrica == "compra")
    assert mep_compra.as_of == date(2026, 6, 24)
    assert mep_compra.fuente == "bluelytics"

    oficial_compra = next(r for r in rows if r.clave == "Oficial" and r.metrica == "compra")
    assert oficial_compra.as_of == date(2026, 6, 25)
    assert oficial_compra.fuente == "ámbito"


def test_matrix_ignores_per_row_override_columns() -> None:
    """Matrix files use file-level as_of/fuente only — no per-row override path."""
    text = """\
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

| entidad      | 30d   |
|--------------|-------|
| Banco Nación | 35.00 |
"""
    rows = parse_dataset_markdown(text, filename_slug="plazo_fijo")
    assert len(rows) == 1
    assert rows[0].as_of == date(2026, 6, 25)
    assert rows[0].fuente == "bna.com.ar"


# ── Additional matrix/error-matrix coverage ───────────────────────────────────


def test_duplicate_key_dims_metrica_last_wins(caplog: object) -> None:
    """Duplicate (clave, dims, metrica) within a file -> last wins, warning logged."""
    text = """\
---
type: dataset
categoria: dolar
formato: largo
clave: tipo
metricas: { compra: "ARS" }
as_of: 2026-06-25
fuente: ámbito
---

| tipo | compra |
|------|--------|
| MEP  | 1180   |
| MEP  | 1190   |
"""
    with caplog.at_level(logging.WARNING, logger="wiki"):
        rows = parse_dataset_markdown(text, filename_slug="dolar")

    assert len(rows) == 1
    assert rows[0].valor == 1190
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_categoria_mismatch_logs_warning_but_parses(caplog: object) -> None:
    """categoria != filename slug -> warning logged (filename authoritative), rows still parsed."""
    text = """\
---
type: dataset
categoria: dolar_blue
formato: largo
clave: tipo
metricas: { compra: "ARS" }
as_of: 2026-06-25
fuente: ámbito
---

| tipo | compra |
|------|--------|
| MEP  | 1180   |
"""
    with caplog.at_level(logging.WARNING, logger="wiki"):
        rows = parse_dataset_markdown(text, filename_slug="dolar")

    assert len(rows) == 1
    # filename slug is authoritative for the join key
    assert rows[0].categoria == "dolar"
    assert any(r.levelno == logging.WARNING for r in caplog.records)
