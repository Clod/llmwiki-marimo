"""Regression baseline of the domain tools move (task 10-21-domain-tools).

The post-processing of a chat answer (`answer_with_table`, `ensure_citation`,
`domain/chat/postprocess.py`) and the tool names of the agents that
`build_agents` (`services/chat.py`) registers are recorded here for fixed
tool-return messages. The move of the finance extension to
`domain/extensions/finance_argentina/` (step 1) leaves every expected value
identical. Only step 2 (the finance data tools counted as citable evidence)
changes expected values, and only those of `FINANCE_DATA_CASES`.

No test calls the model: the messages are built with the pydantic-ai classes.
"""

import shutil
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from domain.chat.postprocess import answer_with_table, ensure_citation
from services import chat
from services.wiki import open_wiki

ROOT = Path(__file__).resolve().parents[2]
URL = dict(base_url="http://localhost:1234/v1", api_key="fake-key", model="fake-model")

ANSWER = "El valor pedido figura en los datos."


def _messages(tool_name: str, content: str) -> list:
    """One request with one tool return of `tool_name`."""
    return [ModelRequest(parts=[ToolReturnPart(tool_name=tool_name, content=content, tool_call_id="c1")])]


def _rows(categoria: str, *rows: str) -> str:
    """The table a dataset tool returns: the `format_rows_as_table` header and `rows`."""
    return (
        f"**{len(rows)} dataset row(s)** for categoria='{categoria}':\n\n"
        "| clave | metrica | valor | unidad | dims | as_of | fuente |\n"
        "|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\nMoneda: ARS."
    )


ADVISORY = (
    "Para $1,000,000 a ~90 días (ARS), si la tasa actual se mantiene:\n\n"
    "**Alternativas con ganancia estimada** (ordenadas por ganancia)\n\n"
    "| opción | clave | plazo | TEA | ganancia est. | al fecha | fuente |\n"
    "|---|---|---|---|---|---|---|\n"
    "| plazo_fijo | Banco Credicoop | 90d | 41.84% | $90,000 | 2026-06-25 | bcra.gob.ar |\n\n"
    "**Renta variable** (no estimable)\n"
    "| opción | depende de |\n"
    "|---|---|\n"
    "| acciones | precio_mercado |"
)

DOLAR = _rows("dolar", "| MEP | compra | 1180.0 | ARS | - | 2026-06-25 | ambito.com |")
PLAZO_FIJO = _rows("plazo_fijo", "| Banco Nación | TNA | 33.0 | % | plazo=30d | 2026-06-25 | bcra.gob.ar |")
CAUCION = _rows("caucion", "| BYMA | TNA | 29.5 | % | plazo=7d | 2026-06-25 | byma.com.ar |")
ACCIONES = _rows("acciones", "| GGAL | precio | 6800.0 | ARS | - | 2026-06-25 | byma.com.ar |")
FCI = _rows("fci_money_market", "| Delta Pesos | rendimiento | 32.2 | % | - | 2026-06-25 | cafci.org.ar |")

# (tool name, tool return, expected answer_with_table, expected ensure_citation)
GENERIC_CASES = [
    ("query_dataset", DOLAR, ANSWER,
     f"{ANSWER}\n\nFuente: ambito.com\nReferencia: dolar.md"),
    ("read_wiki_page", "[wiki page: wiki/concepts/caucion.md]\n\n# Caución\nUna caución es...", ANSWER,
     f"{ANSWER}\n\nReferencia: caucion.md"),
    ("search_wiki_fts", "[wiki page: wiki/concepts/caucion.md] Una caución es...", ANSWER, ANSWER),
    ("estimar_alternativas", ADVISORY, f"{ANSWER}\n\n{ADVISORY}", ANSWER),
    ("estimar_alternativas", "No hay opciones para ese monto.", ANSWER, ANSWER),
]

# Step 0 and step 1: the finance data tools were not citable evidence, so
# `ensure_citation` left the answer unchanged. Step 2 counts them as evidence:
# each case now gets the `Fuente:` and `Referencia:` lines, as `query_dataset` does.
FINANCE_DATA_CASES = [
    ("cotizacion_dolar", DOLAR, ANSWER, f"{ANSWER}\n\nFuente: ambito.com\nReferencia: dolar.md"),
    ("tasa_plazo_fijo", PLAZO_FIJO, ANSWER, f"{ANSWER}\n\nFuente: bcra.gob.ar\nReferencia: plazo_fijo.md"),
    ("tasa_caucion", CAUCION, ANSWER, f"{ANSWER}\n\nFuente: byma.com.ar\nReferencia: caucion.md"),
    ("precio_accion", ACCIONES, ANSWER, f"{ANSWER}\n\nFuente: byma.com.ar\nReferencia: acciones.md"),
    ("rendimiento_fci", FCI, ANSWER, f"{ANSWER}\n\nFuente: cafci.org.ar\nReferencia: fci_money_market.md"),
]


@pytest.mark.parametrize("tool_name, content, with_table, with_citation", GENERIC_CASES + FINANCE_DATA_CASES)
def test_post_processed_answer(tool_name, content, with_table, with_citation):
    messages = _messages(tool_name, content)
    assert answer_with_table(ANSWER, messages) == with_table
    assert ensure_citation(ANSWER, messages) == with_citation


def test_the_advisory_table_is_not_appended_twice():
    answer = f"{ANSWER}\n\n| opción | clave |\n|---|---|"
    assert answer_with_table(answer, _messages("estimar_alternativas", ADVISORY)) == answer


def _names(agent) -> list[str]:
    return sorted(agent._function_toolset.tools.keys())


FINANCE_TOOLS = sorted([
    "cotizacion_dolar", "estimar_alternativas", "precio_accion", "rendimiento_fci",
    "tasa_caucion", "tasa_plazo_fijo",
])
WIKI_TOOLS = ["read_wiki_page", "search_source_chunks", "search_wiki_fts"]


@pytest.mark.parametrize("example, agent_tools, pre_retrieval_tools", [
    ("finanzas-argentinas", sorted(WIKI_TOOLS + FINANCE_TOOLS), FINANCE_TOOLS),
    ("fairy-tales", WIKI_TOOLS, []),
])
def test_build_agents_tool_names(tmp_path, example, agent_tools, pre_retrieval_tools):
    shutil.copytree(ROOT / "examples" / example, tmp_path / example)
    agents = chat.build_agents(open_wiki(tmp_path / example), **URL)
    assert _names(agents.agent) == agent_tools
    assert _names(agents.agent_pre_retrieval) == pre_retrieval_tools
    assert _names(agents.condenser) == []


def test_an_answer_built_only_from_a_finance_data_tool_gets_its_fuente_line():
    """Acceptance criterion of step 2: the citation of a finance data tool."""
    result = ensure_citation(ANSWER, _messages("cotizacion_dolar", DOLAR))
    assert "Fuente: ambito.com" in result.splitlines()


def test_a_data_tool_that_found_no_value_adds_no_citation():
    messages = _messages("cotizacion_dolar", "No dataset rows found for that combination.")
    assert ensure_citation(ANSWER, messages) == ANSWER
