"""Tests for domain/chat/agent.py — conditional query_dataset registration.

Spec: .trellis/spec/backend/datasets-format.md §4 (Activation / Optionality),
§7 test_optionality_guard. No `datasets/` directory -> the agent must register
exactly the 3 default tools and the system prompt must be byte-identical to
today. A `datasets/` directory with at least one valid dataset file ->
query_dataset is additionally registered and the prompt gains routing lines.
"""

from pydantic_ai import RunContext

from domain.chat.agent import create_agent

_DEFAULT_TOOLS = {"read_wiki_page", "search_wiki_fts", "search_source_chunks"}

_VALID_DATASET = """\
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


def _agent_tool_names(agent) -> set:
    return set(agent._function_toolset.tools.keys())


# ── §7 test_optionality_guard ─────────────────────────────────────────────────


def test_no_datasets_dir_registers_exactly_default_tools(tmp_path) -> None:
    """No datasets/ directory at all -> exactly the 3 default tools."""
    base_prompt = "Base system prompt for testing."
    agent = create_agent(
        base_url="http://localhost:1234/v1",
        api_key="fake-key",
        model="fake-model",
        system_prompt=base_prompt,
        workspace=tmp_path,
    )
    assert _agent_tool_names(agent) == _DEFAULT_TOOLS


def test_no_datasets_dir_prompt_is_byte_identical(tmp_path) -> None:
    """No datasets/ directory -> prompt unchanged from the no-workspace call."""
    base_prompt = "Base system prompt for testing."
    agent_without_workspace = create_agent(
        base_url="http://localhost:1234/v1",
        api_key="fake-key",
        model="fake-model",
        system_prompt=base_prompt,
    )
    agent_with_empty_workspace = create_agent(
        base_url="http://localhost:1234/v1",
        api_key="fake-key",
        model="fake-model",
        system_prompt=base_prompt,
        workspace=tmp_path,
    )
    (prompt_without,) = agent_without_workspace._system_prompts
    (prompt_with,) = agent_with_empty_workspace._system_prompts
    assert prompt_with == prompt_without == base_prompt


def test_default_workspace_none_registers_exactly_default_tools() -> None:
    """workspace omitted entirely (existing call sites) -> unchanged behavior."""
    base_prompt = "Base system prompt for testing."
    agent = create_agent(
        base_url="http://localhost:1234/v1",
        api_key="fake-key",
        model="fake-model",
        system_prompt=base_prompt,
    )
    assert _agent_tool_names(agent) == _DEFAULT_TOOLS


def test_empty_datasets_dir_registers_exactly_default_tools(tmp_path) -> None:
    """datasets/ exists but has no valid dataset files -> inert, same as absent."""
    (tmp_path / "datasets").mkdir()
    base_prompt = "Base system prompt for testing."
    agent = create_agent(
        base_url="http://localhost:1234/v1",
        api_key="fake-key",
        model="fake-model",
        system_prompt=base_prompt,
        workspace=tmp_path,
    )
    assert _agent_tool_names(agent) == _DEFAULT_TOOLS


# ── Activation ─────────────────────────────────────────────────────────────────


def test_datasets_dir_with_valid_file_registers_query_dataset(tmp_path) -> None:
    """datasets/ with at least one valid dataset file -> query_dataset added."""
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    (datasets_dir / "plazo_fijo.md").write_text(_VALID_DATASET, encoding="utf-8")

    base_prompt = "Base system prompt for testing."
    agent = create_agent(
        base_url="http://localhost:1234/v1",
        api_key="fake-key",
        model="fake-model",
        system_prompt=base_prompt,
        workspace=tmp_path,
    )
    assert _agent_tool_names(agent) == _DEFAULT_TOOLS | {"query_dataset"}


def test_datasets_dir_with_valid_file_extends_prompt(tmp_path) -> None:
    """Activation adds dataset-routing lines to the prompt (no longer byte-identical)."""
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    (datasets_dir / "plazo_fijo.md").write_text(_VALID_DATASET, encoding="utf-8")

    base_prompt = "Base system prompt for testing."
    agent = create_agent(
        base_url="http://localhost:1234/v1",
        api_key="fake-key",
        model="fake-model",
        system_prompt=base_prompt,
        workspace=tmp_path,
    )
    (effective_prompt,) = agent._system_prompts
    assert effective_prompt != base_prompt
    assert base_prompt in effective_prompt
    assert "query_dataset" in effective_prompt


# ── Generic overlay seam (extra_tools / extra_prompt) ────────────────────────────


def _dummy_tool(ctx: RunContext[str], x: str) -> str:
    """A dummy domain-overlay tool for seam testing."""
    return x


def test_extra_tools_are_registered() -> None:
    """extra_tools are added on top of the built-in tools (no workspace needed)."""
    agent = create_agent(
        base_url="http://localhost:1234/v1", api_key="fake-key", model="fake-model",
        system_prompt="Base.", extra_tools=[_dummy_tool],
    )
    assert _agent_tool_names(agent) == _DEFAULT_TOOLS | {"_dummy_tool"}


def test_extra_prompt_is_appended() -> None:
    """extra_prompt is appended to the system prompt verbatim."""
    agent = create_agent(
        base_url="http://localhost:1234/v1", api_key="fake-key", model="fake-model",
        system_prompt="Base.", extra_prompt="\n\nOVERLAY ROUTING.",
    )
    (prompt,) = agent._system_prompts
    assert prompt == "Base.\n\nOVERLAY ROUTING."


def test_no_extra_is_byte_identical() -> None:
    """extra_tools/extra_prompt default None -> exactly default tools + base prompt."""
    base = "Base."
    agent = create_agent(
        base_url="http://localhost:1234/v1", api_key="fake-key", model="fake-model",
        system_prompt=base,
    )
    assert _agent_tool_names(agent) == _DEFAULT_TOOLS
    (prompt,) = agent._system_prompts
    assert prompt == base
