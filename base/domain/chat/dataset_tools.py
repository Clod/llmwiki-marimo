"""query_dataset — generic dataset-lookup tool for the chat agent.

PURPOSE FOR BEGINNERS:
This tool lets the agent answer questions about structured, periodically
refreshed tabular data (rates, prices, stats) — the kind of fact that goes
stale daily and must never be paraphrased by an LLM. It reads normalized rows
via DatasetSource (LocalMarkdownSource today) and renders them as a compact
markdown table, always carrying `valor`+`unidad`, `as_of`, and `fuente` so the
agent can cite an exact value and its freshness date.

The engine has no domain vocabulary: `categoria`/`clave`/`metrica` are opaque
strings supplied by the caller (the LLM, guided by the system prompt's
dataset-routing lines — see domain/chat/agent.py).

Dependency contract: ctx.deps is the SQLite DB path (str), matching the other
chat tools in domain/chat/wiki_tools.py. Workspace is derived the same way:
Path(db_path).parent.parent.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic_ai import RunContext

from domain.datasets.models import DatasetRow
from domain.datasets.source import LocalMarkdownSource

logger = logging.getLogger("wiki.domain.chat.dataset_tools")


def _workspace(db_path: str) -> Path:
    """Workspace root, given the SQLite DB path (workspace/.llmwiki/index.db)."""
    return Path(db_path).parent.parent


def query_dataset(
    ctx: RunContext[str],
    categoria: str,
    clave: str | None = None,
    metrica: str | None = None,
) -> str:
    """Look up current structured dataset values for a category.

    Use this for periodically-refreshed numeric facts (rates, prices, stats)
    rather than recalling them from memory or from concept-page prose — those
    values go stale and must always be quoted verbatim with their date.

    Args:
        ctx: The runtime context containing system dependencies (the database path).
        categoria: Dataset category slug, e.g. "plazo_fijo", "dolar".
        clave: Optional item key to filter by, e.g. "Banco Nación", "MEP".
        metrica: Optional metric name to filter by, e.g. "TNA", "compra".

    Returns:
        A compact markdown table of matching rows (clave, metrica, valor+unidad,
        as_of, fuente), or an honest "no data" message when the category or
        filter combination has no rows. Never invents a value.
    """
    db_path = ctx.deps
    datasets_dir = _workspace(db_path) / "datasets"
    source = LocalMarkdownSource(datasets_dir)

    try:
        rows = source.query(categoria, clave=clave, metrica=metrica)
    except Exception as exc:
        logger.warning("query_dataset failed for categoria=%r: %s", categoria, exc)
        return f"Dataset lookup failed for '{categoria}': {exc}"

    if not rows:
        return f"No dataset rows found for categoria='{categoria}'" + (
            f", clave='{clave}'" if clave else ""
        ) + (f", metrica='{metrica}'" if metrica else "") + "."

    return _format_rows_as_table(categoria, rows)


def _format_rows_as_table(categoria: str, rows: list[DatasetRow]) -> str:
    lines = [f"**{len(rows)} dataset row(s)** for categoria='{categoria}':\n"]
    lines.append("| clave | metrica | valor | unidad | dims | as_of | fuente |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in rows:
        dims_str = ", ".join(f"{k}={v}" for k, v in row.dims.items()) if row.dims else "-"
        lines.append(
            f"| {row.clave} | {row.metrica} | {row.valor} | {row.unidad} | "
            f"{dims_str} | {row.as_of.isoformat()} | {row.fuente} |"
        )
    return "\n".join(lines)
