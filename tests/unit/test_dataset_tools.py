"""Tests for domain/chat/dataset_tools.py — query_dataset agent tool.

Tool functions are tested by calling them directly with a mock RunContext,
matching the convention in test_wiki_tools.py.
"""

from pathlib import Path

from domain.chat.dataset_tools import query_dataset

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


class _Ctx:
    """Minimal RunContext mock — tools only need ctx.deps."""

    def __init__(self, deps: str) -> None:
        self.deps = deps


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    datasets_dir = workspace / "datasets"
    datasets_dir.mkdir(parents=True)
    (datasets_dir / "plazo_fijo.md").write_text(_PLAZO_FIJO, encoding="utf-8")
    (workspace / ".llmwiki").mkdir(parents=True)
    return workspace


def test_query_dataset_returns_markdown_table(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    db_path = str(workspace / ".llmwiki" / "index.db")
    ctx = _Ctx(deps=db_path)

    result = query_dataset(ctx, "plazo_fijo")

    assert "Banco Nación" in result
    assert "35.0" in result  # valor is a float; 35.00 normalizes to 35.0
    assert "%" in result
    assert "2026-06-25" in result
    assert "bna.com.ar" in result


def test_query_dataset_filters_by_clave(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    db_path = str(workspace / ".llmwiki" / "index.db")
    ctx = _Ctx(deps=db_path)

    result = query_dataset(ctx, "plazo_fijo", clave="Banco Nación")

    assert "Banco Nación" in result
    assert "Banco Galicia" not in result


def test_query_dataset_filters_by_metrica(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    db_path = str(workspace / ".llmwiki" / "index.db")
    ctx = _Ctx(deps=db_path)

    result = query_dataset(ctx, "plazo_fijo", metrica="TNA")

    assert "Banco Nación" in result


def test_query_dataset_unknown_categoria_is_honest(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    db_path = str(workspace / ".llmwiki" / "index.db")
    ctx = _Ctx(deps=db_path)

    result = query_dataset(ctx, "nonexistent")

    assert "no data" in result.lower() or "not found" in result.lower() or "no results" in result.lower()


def test_query_dataset_no_datasets_dir_is_honest(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".llmwiki").mkdir(parents=True)
    db_path = str(workspace / ".llmwiki" / "index.db")
    ctx = _Ctx(deps=db_path)

    result = query_dataset(ctx, "plazo_fijo")

    assert isinstance(result, str)
    assert "plazo_fijo" in result
