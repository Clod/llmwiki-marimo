"""Finance data tools (Spanish-facing): one typed tool per dataset category.

Each tool reads the rows of one category of the finance manifest
(`requirements.md`) through `LocalMarkdownSource` and returns them as the
markdown table of `format_rows_as_table`, with `as_of` and `fuente`. The
arguments are optional; an omitted argument means every value.

The allowed values of each argument are read from the dataset files when the
agents are built (`make_data_tools`) and written into the JSON schema of the
tool as an `enum` (the `prepare` function of `pydantic_ai.Tool`). A value
outside the allowed values raises `ModelRetry` with the allowed values, so the
model corrects the call instead of receiving "no rows". The check runs inside
the tool too, because the schema does not enforce the `enum` on the arguments.

Composition glue like `agent_tool.py`: the engine never imports this module;
`activate()` hands the tools to `create_agent(extra_tools=...)`.
"""

from __future__ import annotations

import copy
import dataclasses
import re
import unicodedata
from pathlib import Path

from pydantic_ai import ModelRetry, RunContext, Tool

from domain import tracing
from domain.chat.dataset_tools import format_rows_as_table
from domain.chat.guardrail import _is_substantive
from domain.datasets.models import DatasetSource
from domain.datasets.source import LocalMarkdownSource

DATA_TOOL_NAMES = frozenset({
    "tasa_plazo_fijo", "tasa_caucion", "cotizacion_dolar", "precio_accion", "rendimiento_fci",
})

# Tool name -> (dataset category, name of the key argument, has the plazo dimension).
_TOOLS = {
    "tasa_plazo_fijo": ("plazo_fijo", "entidad", True),
    "tasa_caucion": ("caucion", "mercado", True),
    "cotizacion_dolar": ("dolar", "tipo", False),
    "precio_accion": ("acciones", "ticker", False),
    "rendimiento_fci": ("fci_money_market", "fondo", False),
}

_PLAZO = re.compile(r"^(\d+)d$")


def _fold(text: str) -> str:
    """Case- and accent-insensitive form of a key ("Banco Nación" -> "banco nacion")."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


def _workspace(db_path: str) -> Path:
    """Workspace root, given the SQLite DB path (workspace/.llmwiki/index.db)."""
    return Path(db_path).parent.parent


def allowed_values(source: DatasetSource, categoria: str, key_arg: str, with_plazo: bool) -> dict:
    """The allowed values of each argument of a category, read from its rows.

    `{key_arg: [claves in file order], "plazo_dias": [sorted days]}`; the
    second entry only for the categories with the dimension `plazo` (`7d` -> 7).
    """
    rows = source.query(categoria)
    allowed: dict[str, list] = {key_arg: list(dict.fromkeys(r.clave for r in rows))}
    if with_plazo:
        days = {int(m.group(1)) for r in rows if (m := _PLAZO.match(r.dims.get("plazo", "")))}
        allowed["plazo_dias"] = sorted(days)
    return allowed


def _enum_prepare(allowed: dict):
    """A `prepare` function that writes the allowed values into the tool's JSON schema."""
    async def prepare(ctx, tool_def):
        schema = copy.deepcopy(tool_def.parameters_json_schema)
        for arg, values in allowed.items():
            prop = schema["properties"][arg]
            if arg == "plazo_dias":
                # The model sees an integer; the tool also accepts text, so that a
                # call like "7_dias" reaches the tool and is rejected with the
                # allowed values, instead of failing the type check of Pydantic AI.
                prop["anyOf"] = [{"type": "integer"}, {"type": "null"}]
            branch = next((b for b in prop.get("anyOf", []) if b.get("type") != "null"), prop)
            branch["enum"] = list(values)
        return dataclasses.replace(tool_def, parameters_json_schema=schema)
    return prepare


def _read(ctx: RunContext[str], categoria: str, key_arg: str, key: str | None,
          plazo_dias: int | str | None, with_plazo: bool) -> tuple[str, str | None]:
    """The body of the data tools: `(text, rejected)`.

    `rejected` is the reason when an argument is outside the allowed values
    (`text` is then the same reason); otherwise None.
    """
    source = LocalMarkdownSource(_workspace(ctx.deps) / "datasets")
    allowed = allowed_values(source, categoria, key_arg, with_plazo)
    reasons = []
    clave = None
    if key is not None:
        clave = next((k for k in allowed[key_arg] if _fold(k) == _fold(key)), None)
        if clave is None:
            reasons.append(f"{key_arg} must be one of {', '.join(allowed[key_arg])}")
    if isinstance(plazo_dias, str) and plazo_dias.strip().isdigit():
        plazo_dias = int(plazo_dias)                 # "7" is the plazo 7; "7_dias" is not
    if plazo_dias is not None and plazo_dias not in allowed.get("plazo_dias", []):
        reasons.append(f"plazo_dias must be one of {', '.join(map(str, allowed['plazo_dias']))}")
    if reasons:
        reason = "; ".join(reasons)
        return reason, reason
    dims = {"plazo": f"{plazo_dias}d"} if plazo_dias is not None else None
    rows = source.query(categoria, clave=clave, dims=dims)
    if not rows:
        return f"No se encontró ningún valor en '{categoria}' para esa combinación.", None
    table = format_rows_as_table(categoria, rows)
    # The currency of the values, from the dataset's `moneda`: without it, the ARS
    # rates of plazo_fijo were presented as dollar rates (fin-19).
    moneda = source.attributes(categoria).get("moneda")
    return (f"{table}\nMoneda: {moneda}." if moneda else table), None


def _outcome(text: str, rejected: str | None) -> dict:
    """The `substantive` and `rejected` attributes of a K record."""
    if rejected:
        return {"substantive": False, "rejected": rejected}
    return {"substantive": _is_substantive(text)}


def tasa_plazo_fijo(
    ctx: RunContext[str],
    entidad: str | None = None,
    plazo_dias: int | str | None = None,
) -> str:
    """Lee la TNA (tasa nominal anual) de plazo fijo de los bancos, por entidad y por plazo.

    Usala para cualquier pregunta por la tasa actual de un plazo fijo. Sin
    argumentos devuelve todas las entidades y todos los plazos.

    Args:
        ctx: Contexto de ejecución (deps = ruta de la base SQLite).
        entidad: Banco, uno de los valores permitidos (opcional).
        plazo_dias: Plazo, expresado como número de días (por ejemplo 30), uno de
            los valores permitidos (opcional).

    Returns:
        Una tabla con la TNA, la unidad, la fecha (`as_of`) y la fuente de cada
        fila. Nunca inventa un valor.
    """
    text, rejected = _read(ctx, "plazo_fijo", "entidad", entidad, plazo_dias, True)
    tracing.mark("K6", tracing.READING, entidad=entidad, plazo_dias=plazo_dias,
                 **_outcome(text, rejected))
    if rejected:
        raise ModelRetry(rejected)
    return text


def tasa_caucion(
    ctx: RunContext[str],
    mercado: str | None = None,
    plazo_dias: int | str | None = None,
) -> str:
    """Lee la TNA (tasa nominal anual) de una caución bursátil, por mercado y por plazo.

    Usala para cualquier pregunta por la tasa actual de una caución. Sin
    argumentos devuelve todos los mercados y todos los plazos.

    Args:
        ctx: Contexto de ejecución (deps = ruta de la base SQLite).
        mercado: Mercado, uno de los valores permitidos (opcional).
        plazo_dias: Plazo, expresado como número de días (por ejemplo 7), uno de
            los valores permitidos (opcional).

    Returns:
        Una tabla con la TNA, la unidad, la fecha (`as_of`) y la fuente de cada
        fila. Nunca inventa un valor.
    """
    text, rejected = _read(ctx, "caucion", "mercado", mercado, plazo_dias, True)
    tracing.mark("K7", tracing.READING, mercado=mercado, plazo_dias=plazo_dias,
                 **_outcome(text, rejected))
    if rejected:
        raise ModelRetry(rejected)
    return text


def cotizacion_dolar(ctx: RunContext[str], tipo: str | None = None) -> str:
    """Lee la cotización de compra y de venta de un tipo de dólar.

    Usala para cualquier pregunta por el valor actual del dólar. Sin argumentos
    devuelve todos los tipos.

    Args:
        ctx: Contexto de ejecución (deps = ruta de la base SQLite).
        tipo: Tipo de dólar (por ejemplo Oficial, MEP), uno de los valores
            permitidos (opcional).

    Returns:
        Una tabla con la compra y la venta, la unidad, la fecha (`as_of`) y la
        fuente. Nunca inventa un valor.
    """
    text, rejected = _read(ctx, "dolar", "tipo", tipo, None, False)
    tracing.mark("K8", tracing.READING, tipo=tipo, **_outcome(text, rejected))
    if rejected:
        raise ModelRetry(rejected)
    return text


def precio_accion(ctx: RunContext[str], ticker: str | None = None) -> str:
    """Lee el precio actual de una acción, por ticker.

    Usala para cualquier pregunta por el precio actual de una acción. Sin
    argumentos devuelve todos los tickers.

    Args:
        ctx: Contexto de ejecución (deps = ruta de la base SQLite).
        ticker: Ticker de la acción, uno de los valores permitidos (opcional).

    Returns:
        Una tabla con el precio, la unidad, la fecha (`as_of`) y la fuente de
        cada fila. Nunca inventa un valor ni estima una ganancia.
    """
    text, rejected = _read(ctx, "acciones", "ticker", ticker, None, False)
    tracing.mark("K9", tracing.READING, ticker=ticker, **_outcome(text, rejected))
    if rejected:
        raise ModelRetry(rejected)
    return text


def rendimiento_fci(ctx: RunContext[str], fondo: str | None = None) -> str:
    """Lee el rendimiento de un fondo común de inversión de dinero (money market).

    Usala para cualquier pregunta por el rendimiento actual de un FCI money
    market. Sin argumentos devuelve todos los fondos.

    Args:
        ctx: Contexto de ejecución (deps = ruta de la base SQLite).
        fondo: Nombre del fondo, uno de los valores permitidos (opcional).

    Returns:
        Una tabla con el rendimiento, la unidad, la fecha (`as_of`) y la fuente
        de cada fila. Nunca inventa un valor.
    """
    text, rejected = _read(ctx, "fci_money_market", "fondo", fondo, None, False)
    tracing.mark("K10", tracing.READING, fondo=fondo, **_outcome(text, rejected))
    if rejected:
        raise ModelRetry(rejected)
    return text


_FUNCTIONS = {f.__name__: f for f in (
    tasa_plazo_fijo, tasa_caucion, cotizacion_dolar, precio_accion, rendimiento_fci,
)}


def make_data_tools(source: DatasetSource, categories: tuple[str, ...]) -> list[Tool]:
    """The data tools of the categories in `categories`, with the allowed values read from `source`."""
    tools = []
    for name, (categoria, key_arg, with_plazo) in _TOOLS.items():
        if categoria in categories:
            allowed = allowed_values(source, categoria, key_arg, with_plazo)
            tools.append(Tool(_FUNCTIONS[name], prepare=_enum_prepare(allowed)))
    return tools
