"""Chat-agent binding for the finance_argentina advisory (Spanish-facing).

Composition glue, deliberately kept out of `advisory.py` so the advisory logic
stays free of PydanticAI. The engine (`domain/chat/agent.py`, `domain/datasets/`)
never imports this module — the composition root (`read_app.py`) calls
`activate()` and passes the result through `create_agent(extra_tools=...,
extra_prompt=...)`. That keeps the engine domain-agnostic.

The tool, its description, and the prompt addendum are all in Spanish: this is an
Argentine-finance overlay (see docs/design_finance_argentina.md).
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic_ai import RunContext

from domain.datasets.source import LocalMarkdownSource
from domain.finance_argentina.advisory import estimate_alternatives, render_markdown
from domain.finance_argentina.requirements import load_default_requirements
from domain.finance_argentina.validator import validate_workspace

logger = logging.getLogger("wiki.domain.finance_argentina.agent_tool")

# Appended to the system prompt only when the finance overlay is active.
FINANCE_PROMPT_ADDENDUM = """

## Inversiones (mercado argentino)
Para preguntas del tipo "tengo $X que no voy a necesitar por Y meses, ¿qué
alternativas tengo y cuánto ganaría con cada una?", usá la herramienta
`estimar_alternativas`. Presentá su tabla tal cual, indicá siempre la fecha
(`as_of`) de cada tasa y la fuente, y nunca inventes un rendimiento. Los
instrumentos de renta variable (acciones, CEDEARs, etc.) la herramienta los marca
como "no estimable": no estimes su ganancia, solo informá de qué dependen.
"""


def _workspace(db_path: str) -> Path:
    """Workspace root, given the SQLite DB path (workspace/.llmwiki/index.db)."""
    return Path(db_path).parent.parent


def estimar_alternativas(
    ctx: RunContext[str],
    monto: float,
    horizonte_meses: float,
    moneda: str = "ARS",
) -> str:
    """Estima y compara alternativas de inversión del mercado argentino.

    Usala cuando el usuario tiene un monto que no necesitará durante un período y
    quiere saber qué alternativas tiene y cuánto ganaría estimado con cada una
    (por ejemplo plazo fijo, FCI money market, caución).

    Args:
        ctx: Contexto de ejecución (deps = ruta de la base SQLite).
        monto: Capital a invertir, en la moneda indicada.
        horizonte_meses: Meses durante los que el dinero puede quedar invertido.
        moneda: Moneda del monto (por defecto "ARS"); solo se comparan
            instrumentos en esa misma moneda.

    Returns:
        Una comparación en español, en dos secciones: alternativas con ganancia
        estimada (ordenadas por ganancia, citando tasa, fecha y fuente) y
        alternativas de renta variable (no estimables, con su factor de
        variabilidad). Nunca inventa un rendimiento.
    """
    workspace = _workspace(ctx.deps)
    source = LocalMarkdownSource(workspace / "datasets")
    requirements = load_default_requirements()
    try:
        result = estimate_alternatives(
            monto, horizonte_meses, requirements, source, workspace, moneda
        )
    except Exception as exc:
        logger.warning("estimar_alternativas failed: %s", exc)
        return f"No se pudo estimar alternativas: {exc}"
    return render_markdown(result)


def activate(workspace: Path) -> tuple[list | None, str | None]:
    """Return `(extra_tools, extra_prompt)` for `create_agent` when finance is ready.

    "Ready" = the shipped requirements manifest validates against `workspace`
    with at least one passing category (a finance-specific gate, stricter than
    "any datasets present"). Otherwise returns `(None, None)` so the chat agent
    is completely unaffected.
    """
    source = LocalMarkdownSource(workspace / "datasets")
    requirements = load_default_requirements()
    report = validate_workspace(requirements, source, workspace)
    if report.passing:
        return [estimar_alternativas], FINANCE_PROMPT_ADDENDUM
    return None, None
