"""Tests for domain/finance_argentina/agent_tool.py — the activation gate.

`activate(workspace)` returns `(extra_tools, extra_prompt)` only when the shipped
requirements manifest validates against the workspace with >=1 passing category;
otherwise `(None, None)`. Real files in tmp_path; no LLM/network.
"""

from domain.finance_argentina.agent_tool import (
    FINANCE_PROMPT_ADDENDUM,
    activate,
    estimar_alternativas,
)

_DATASET_PLAZO_FIJO = """\
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

| entidad      | 30d   | 90d   |
|--------------|-------|-------|
| Banco Nación | 35.00 | 37.00 |
"""

_CONCEPT_PLAZO_FIJO = """\
---
disponibilidad: a_plazo
plazos_dias: [30, 60, 90]
moneda: ARS
metodo_calculo: interes_simple_vencimiento
metrica_tasa: TNA
---
# Plazo Fijo
"""


def _seed_plazo_fijo(workspace) -> None:
    datasets = workspace / "datasets"
    datasets.mkdir(parents=True)
    (datasets / "plazo_fijo.md").write_text(_DATASET_PLAZO_FIJO, encoding="utf-8")
    concepts = workspace / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    (concepts / "plazo_fijo.md").write_text(_CONCEPT_PLAZO_FIJO, encoding="utf-8")


def test_activate_none_for_empty_workspace(tmp_path) -> None:
    """No finance data -> overlay stays inert."""
    tools, prompt = activate(tmp_path)
    assert tools is None
    assert prompt is None


def test_activate_returns_tool_when_finance_ready(tmp_path) -> None:
    """>=1 passing category -> the advisory tool + Spanish prompt addendum."""
    _seed_plazo_fijo(tmp_path)
    tools, prompt = activate(tmp_path)
    assert tools == [estimar_alternativas]
    assert prompt == FINANCE_PROMPT_ADDENDUM
