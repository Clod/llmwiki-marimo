"""The finance data tools (K6 to K10): enums read from the data, rejection, trace, registration.

Real dataset files in a temporary workspace; the model is simulated.
"""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from domain import tracing
from domain.chat.agent import create_agent
from domain.finance_argentina.agent_tool import FINANCE_PROMPT_ADDENDUM, activate
from domain.finance_argentina.data_tools import (
    DATA_TOOL_NAMES,
    cotizacion_dolar,
    precio_accion,
    rendimiento_fci,
    tasa_caucion,
    tasa_plazo_fijo,
)
from services import chat
from services.wiki import open_wiki

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DATASETS = ROOT / "examples" / "finanzas-argentinas" / "datasets"
URL = dict(base_url="http://localhost:1234/v1", api_key="fake-key", model="fake-model")


@pytest.fixture
def ws(tmp_path):
    shutil.copytree(EXAMPLE_DATASETS, tmp_path / "datasets")
    return tmp_path


def ctx(ws):
    return SimpleNamespace(deps=str(ws / ".llmwiki" / "index.db"))


def tool_names(agent):
    return set(agent._function_toolset.tools.keys())


def schemas(agent, ws):
    """The JSON schema of each tool, as the model receives it."""
    model = TestModel(call_tools=[])
    with agent.override(model=model):
        agent.run_sync("hola", deps=str(ws / ".llmwiki" / "index.db"))
    return {t.name: t.parameters_json_schema for t in model.last_model_request_parameters.function_tools}


def enum_of(schema, arg):
    prop = schema["properties"][arg]
    branches = prop.get("anyOf", [prop])
    return next(b["enum"] for b in branches if "enum" in b)


def finance_agent(ws):
    tools, prompt = activate(ws)
    return create_agent(**URL, workspace=ws, extra_tools=tools, extra_prompt=prompt,
                        include_dataset_tool=False)


# ── The schema ────────────────────────────────────────────────────────────────

def test_enums_come_from_the_data(ws):
    s = schemas(finance_agent(ws), ws)
    assert enum_of(s["tasa_caucion"], "plazo_dias") == [1, 7, 14, 30]
    # The model sees an integer: the text branch that the tool accepts is not shown.
    assert [b.get("type") for b in s["tasa_caucion"]["properties"]["plazo_dias"]["anyOf"]] == ["integer", "null"]
    assert enum_of(s["tasa_caucion"], "mercado") == ["BYMA", "MAV"]
    assert enum_of(s["tasa_plazo_fijo"], "plazo_dias") == [30, 60, 90, 180, 365]
    assert len(enum_of(s["tasa_plazo_fijo"], "entidad")) == 8
    assert "Banco Nación" in enum_of(s["tasa_plazo_fijo"], "entidad")
    assert "MEP" in enum_of(s["cotizacion_dolar"], "tipo")
    assert len(enum_of(s["precio_accion"], "ticker")) == 10
    assert "Delta Pesos" in enum_of(s["rendimiento_fci"], "fondo")


def test_a_new_row_appears_in_the_enum(ws):
    path = ws / "datasets" / "caucion.md"
    path.write_text(path.read_text().replace(
        "| MAV     | 27.50 | 29.00 | 30.00 | 31.00 |",
        "| MAV     | 27.50 | 29.00 | 30.00 | 31.00 |\n| ROFEX   | 26.00 | 28.00 | 29.00 | 30.00 |"))
    assert enum_of(schemas(finance_agent(ws), ws)["tasa_caucion"], "mercado") == ["BYMA", "MAV", "ROFEX"]


# ── A call ────────────────────────────────────────────────────────────────────

def test_a_valid_call_returns_the_row_with_as_of_and_fuente(ws):
    text = tasa_caucion(ctx(ws), mercado="BYMA", plazo_dias=7)
    assert "| BYMA | TNA | 29.5 |" in text
    assert "2026-06-25" in text and "byma.com.ar" in text and "plazo=7d" in text
    assert "MAV" not in text


def test_omitted_arguments_return_every_row(ws):
    assert tasa_caucion(ctx(ws)).count("| TNA |") == 8
    assert tasa_caucion(ctx(ws), plazo_dias=30).count("| TNA |") == 2
    assert tasa_plazo_fijo(ctx(ws), entidad="Brubank").count("| TNA |") == 5
    assert cotizacion_dolar(ctx(ws)).count("\n| ") >= 10
    assert precio_accion(ctx(ws)).count("| precio |") == 10
    assert rendimiento_fci(ctx(ws)).count("| rendimiento |") == 7


def test_keys_match_without_case_or_accents(ws):
    text = tasa_plazo_fijo(ctx(ws), entidad="banco nacion", plazo_dias=30)
    assert "| Banco Nación | TNA | 33.0 |" in text
    assert "| MEP | compra | 1180" in cotizacion_dolar(ctx(ws), tipo="mep")


def test_a_value_outside_the_data_raises_model_retry_with_the_allowed_values(ws):
    with pytest.raises(ModelRetry) as exc:
        tasa_caucion(ctx(ws), plazo_dias=5)
    assert "plazo_dias must be one of 1, 7, 14, 30" in str(exc.value)
    with pytest.raises(ModelRetry) as exc:
        precio_accion(ctx(ws), ticker="XYZ")
    assert "ticker must be one of" in str(exc.value) and "YPFD" in str(exc.value)


def test_the_retry_reaches_the_model_through_the_agent(ws):
    seen = []

    def model_fn(messages, info: AgentInfo):
        seen.append(messages[-1].parts[0])
        if len(seen) == 1:
            return ModelResponse(parts=[ToolCallPart("tasa_caucion", {"plazo_dias": 5})])
        return ModelResponse(parts=[TextPart("ok")])

    agent = finance_agent(ws)
    with agent.override(model=FunctionModel(model_fn)):
        agent.run_sync("tasa", deps=str(ws / ".llmwiki" / "index.db"))
    assert "plazo_dias must be one of 1, 7, 14, 30" in str(seen[1].content)


def test_the_answer_of_a_tool_states_the_currency_of_the_dataset(ws):
    """fin-19: the ARS rates of plazo_fijo were presented as dollar rates, because the
    table did not say the currency. The currency comes from the dataset's `moneda`."""
    assert "Moneda: ARS" in tasa_plazo_fijo(ctx(ws), entidad="Banco Nación", plazo_dias=30)
    path = ws / "datasets" / "plazo_fijo.md"
    path.write_text(path.read_text(encoding="utf-8").replace("moneda: ARS", "moneda: USD"), encoding="utf-8")
    assert "Moneda: USD" in tasa_plazo_fijo(ctx(ws), entidad="Banco Nación", plazo_dias=30)


# ── The trace ─────────────────────────────────────────────────────────────────

def records(ws):
    path = ws / ".llmwiki" / "traces" / tracing.SPAN_FILE
    return {
        s["name"]: {k.removeprefix("llmwiki."): v for k, v in s["attributes"].items()
                    if k.startswith("llmwiki.")}
        for s in (json.loads(line) for line in path.read_text().splitlines() if line.strip())
        if "llmwiki.node" in s["attributes"]
    }


def test_each_call_writes_its_record(ws, monkeypatch):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    with tracing.root("turn", ws):
        tasa_plazo_fijo(ctx(ws), entidad="Brubank", plazo_dias=30)
        tasa_caucion(ctx(ws), mercado="BYMA")
        cotizacion_dolar(ctx(ws), tipo="MEP")
        precio_accion(ctx(ws), ticker="YPFD")
        rendimiento_fci(ctx(ws), fondo="Delta Pesos")
    rec = records(ws)
    assert (rec["K6"]["entidad"], rec["K6"]["plazo_dias"], rec["K6"]["substantive"]) == ("Brubank", 30, True)
    assert rec["K7"]["mercado"] == "BYMA" and rec["K7"]["substantive"] is True
    assert rec["K8"]["tipo"] == "MEP" and rec["K9"]["ticker"] == "YPFD" and rec["K10"]["fondo"] == "Delta Pesos"
    assert all("rejected" not in rec[k] for k in ("K6", "K7", "K8", "K9", "K10"))


def test_a_rejected_call_writes_the_reason(ws, monkeypatch):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    with tracing.root("turn", ws):
        with pytest.raises(ModelRetry):
            tasa_caucion(ctx(ws), plazo_dias=5)
    k7 = records(ws)["K7"]
    assert k7["plazo_dias"] == 5 and k7["substantive"] is False
    assert "plazo_dias must be one of 1, 7, 14, 30" in k7["rejected"]


def test_a_text_plazo_reaches_the_tool_and_is_rejected_with_the_allowed_values(ws, monkeypatch):
    """"7_dias" is not stopped by the type check of Pydantic AI: the tool rejects
    it, writes K7 with `rejected`, and the model receives the allowed values."""
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    seen = []

    def model_fn(messages, info: AgentInfo):
        seen.append(messages[-1].parts[0])
        if len(seen) == 1:
            return ModelResponse(parts=[ToolCallPart("tasa_caucion", {"plazo_dias": "7_dias"})])
        return ModelResponse(parts=[TextPart("ok")])

    agent = finance_agent(ws)
    with tracing.root("turn", ws), agent.override(model=FunctionModel(model_fn)):
        agent.run_sync("tasa", deps=str(ws / ".llmwiki" / "index.db"))
    assert "plazo_dias must be one of 1, 7, 14, 30" in str(seen[1].content)
    k7 = records(ws)["K7"]
    assert k7["plazo_dias"] == "7_dias" and "plazo_dias must be one of" in k7["rejected"]


def test_a_plazo_written_with_digits_is_accepted(ws):
    assert "plazo=7d" in tasa_caucion(ctx(ws), mercado="BYMA", plazo_dias="7")


# ── Registration ──────────────────────────────────────────────────────────────

def test_finance_wiki_registers_k6_to_k10_and_no_query_dataset(tmp_path):
    shutil.copytree(EXAMPLE_DATASETS, tmp_path / "wiki" / "datasets")
    wiki = open_wiki(tmp_path / "wiki")
    agents = chat.build_agents(wiki, **URL)
    for agent in (agents.agent, agents.agent_pre_retrieval):
        names = tool_names(agent)
        assert DATA_TOOL_NAMES <= names and "estimar_alternativas" in names
        assert "query_dataset" not in names
        (prompt,) = agent._system_prompts
        assert "Structured datasets" not in prompt and FINANCE_PROMPT_ADDENDUM in prompt
    assert {"read_wiki_page", "search_wiki_fts", "search_source_chunks"} <= tool_names(agents.agent)
    assert not ({"read_wiki_page"} & tool_names(agents.agent_pre_retrieval))


def test_a_wiki_without_the_finance_overlay_keeps_query_dataset(tmp_path):
    datasets = tmp_path / "tales" / "datasets"
    datasets.mkdir(parents=True)
    (datasets / "tabla.md").write_text(
        "---\ntype: dataset\ncategoria: tabla\nformato: largo\nclave: k\nmetricas: { v: \"u\" }\n"
        "as_of: 2026-06-25\nfuente: f\n---\n\n| k | v |\n|---|---|\n| a | 1 |\n")
    agents = chat.build_agents(open_wiki(tmp_path / "tales"), **URL)
    names = tool_names(agents.agent)
    assert "query_dataset" in names and not (DATA_TOOL_NAMES & names)
    assert "Structured datasets" in agents.agent._system_prompts[0]


def test_a_failing_category_gets_no_tool(ws):
    (ws / "datasets" / "acciones.md").unlink()
    tools, _ = activate(ws)
    names = {t.name for t in tools if hasattr(t, "name")}
    assert "precio_accion" not in names
    assert {"tasa_plazo_fijo", "tasa_caucion", "cotizacion_dolar", "rendimiento_fci"} <= names


def test_the_prompt_rules(ws):
    for phrase in ("plazo_dias", "as_of", "fuente", "TNA", "tasa_plazo_fijo", "rendimiento_fci"):
        assert phrase in FINANCE_PROMPT_ADDENDUM
