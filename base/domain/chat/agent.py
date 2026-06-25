"""PydanticAI wiki assistant agent factory.

PURPOSE FOR BEGINNERS:
An "Agent" is a smart AI wrapper. Instead of just sending a prompt to an LLM
and getting text back, an Agent can "think", use pre-defined "tools" (Python functions),
access database dependencies, and automatically decide when and how to call those tools
to solve a user's request.

This module acts as an "Agent Factory" (a function that sets up and builds the Agent).
It configures the AI model, loads the instructions (system prompt), registers the
tools the AI can use, and returns the fully-prepared Agent instance.
"""

# Standard library import for typing the optional workspace path parameter.
from pathlib import Path

# Import the core Agent class from PydanticAI
from pydantic_ai import Agent
# Import the OpenAI chat model wrapper from PydanticAI
from pydantic_ai.models.openai import OpenAIChatModel
# Import the OpenAI provider class, which manages credentials and endpoint URLs
from pydantic_ai.providers.openai import OpenAIProvider

# Import the default system prompt instructions that guide the AI's behavior
from .config import _DEFAULT_SYSTEM_PROMPT
# Import the dataset-lookup tool, conditionally registered when the workspace
# has an active datasets/ directory (see _DATASET_PROMPT_ADDENDUM below).
from .dataset_tools import query_dataset
# Import the tool that searches raw source document text chunks (fallback search)
from .tools import search_source_chunks
# Import the read-only tools that let the agent interact with our Wiki.
# Note: the agent has NO write tool — creating/updating wiki pages is the user's
# explicit action via the "Save to wiki" form in read_app.py (save_to_wiki).
from .wiki_tools import read_wiki_page, search_wiki_fts
# Appends the wiki's chat answer-language directive to the system prompt.
from domain.i18n import apply_chat_directive
# Activation trigger for the dataset capability (§4 of datasets-format.md):
# presence of a datasets/ directory with at least one valid dataset file.
from domain.datasets.source import has_active_datasets


# Appended to the system prompt only when the dataset capability is active
# (see has_active_datasets below). Engine-generic — no domain vocabulary,
# `categoria` values are workspace content.
_DATASET_PROMPT_ADDENDUM = """\

## Structured datasets — current values

This wiki also has structured datasets (periodically-refreshed tabular \
values such as rates, prices, or stats) — distinct from the prose wiki \
pages. For a question about a **current value** in a known category, call \
query_dataset(categoria, clave?, metrica?) instead of recalling a number \
from a wiki page or from memory: dataset values change over time and must \
always be quoted verbatim with their as_of date and fuente, exactly like any \
other citation. If query_dataset returns no rows, say so plainly — never \
estimate or recall a dataset value from memory."""


def create_agent(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
    language: str = "en",
    workspace: Path | None = None,
    extra_tools: list | None = None,
    extra_prompt: str | None = None,
) -> Agent[str, str]:
    """Return a configured wiki assistant agent.

    Args:
        base_url: OpenAI-compatible API base URL (e.g. OpenRouter, Local LM Studio, or OpenAI).
        api_key: API key to authenticate with the LLM provider.
        model: Model identifier string (e.g., "gpt-4o", "claude-3-5-sonnet").
        system_prompt: Loaded from wiki_config.toml; falls back to generic default.
        language: ISO 639-1 code for this wiki's content language (from
            WikiAssistantConfig.language). Appends the chat answer-language
            directive to system_prompt; a no-op for English ("en").
        workspace: Absolute path to the active wiki's workspace root (the
            same directory containing wiki/, sources/, .llmwiki/). Optional
            and defaults to None so every existing call site is unaffected.
            When given and `workspace/datasets/` contains at least one valid
            dataset file, query_dataset is additionally registered and the
            system prompt gains dataset-routing lines. Otherwise this is a
            complete no-op — byte-identical tools and prompt to before this
            parameter existed (datasets-format.md §4).
        extra_tools: Optional domain-overlay tool functions to register in
            addition to the built-in ones. Injected by the composition root so
            the engine stays domain-agnostic (it never imports an overlay).
            Defaults to None — no-op.
        extra_prompt: Optional text appended to the system prompt when overlay
            tools are active (e.g. the finance_argentina routing lines, in
            Spanish). Defaults to None — no-op.

    Returns:
        A fully-configured PydanticAI Agent instance.
        The Agent's type signature Agent[str, str] means:
        - Str 1: The type of the runtime dependency (the SQLite database path string).
        - Str 2: The type of the output returned by the agent (plain text response).

    Tools (in priority order per system prompt) — all read-only:
        read_wiki_page       — reads a wiki page from disk by its file path
        search_wiki_fts      — FTS5 search scoped strictly to wiki pages only
        search_source_chunks — FTS5 search on raw source chunks (fallback when wiki lacks answer)
        query_dataset        — current structured dataset values (only when workspace has datasets/)

    The agent has no write tool by design: saving a chat response as a wiki page is the
    user's explicit action via the "Save to wiki" form (read_app.py → save_to_wiki).
    The system prompt instructs the agent to propose a title/category and point the
    user at that form rather than persisting anything itself.
    """

    # 1. Instantiate the Language Model (LLM) client.
    #    By using the OpenAIProvider, we can direct the agent to any OpenAI-compatible API
    #    endpoint (like OpenRouter, DeepSeek, or local models) by passing custom URLs.
    llm = OpenAIChatModel(
        model,
        provider=OpenAIProvider(base_url=base_url, api_key=api_key),
    )

    # Append the wiki's chat answer-language directive (no-op for English, so the
    # English agent's system prompt stays byte-identical to before this feature).
    effective_prompt = apply_chat_directive(system_prompt, language)

    # Dataset capability is opt-in per workspace (datasets-format.md §4): dormant
    # unless workspace/datasets/ exists and has >=1 valid dataset file. When
    # workspace is None (existing call sites) this is always False, so default
    # behavior is unaffected.
    tools = [read_wiki_page, search_wiki_fts, search_source_chunks]
    if workspace is not None and has_active_datasets(workspace):
        tools.append(query_dataset)
        effective_prompt = f"{effective_prompt}{_DATASET_PROMPT_ADDENDUM}"

    # Domain-overlay tools (e.g. finance_argentina) are injected by the caller
    # (the composition root / read_app), keeping this engine module
    # domain-agnostic — it never imports any overlay. Both default to None, so a
    # caller that passes neither gets byte-identical tools and prompt.
    if extra_tools:
        tools.extend(extra_tools)
    if extra_prompt:
        effective_prompt = f"{effective_prompt}{extra_prompt}"

    # 2. Build and return the configured Agent.
    return Agent(
        # Pass the configured language model
        llm,

        # Define the dependency type. The agent's tools require the path to the
        # SQLite database. This database path is passed at runtime using `deps`.
        deps_type=str,

        # Give the agent a clean name for internal logging/debugging
        name="wiki_assistant",

        # Inject the behavior instructions (System Prompt), localized for chat
        # answer language and, when active, extended with dataset routing.
        system_prompt=effective_prompt,

        # Register the suite of Python tool functions the agent is allowed to call.
        # PydanticAI automatically inspects these functions, parses their docstrings
        # and arguments, and explains them to the LLM so it knows exactly when to call them!
        tools=tools,
    )
