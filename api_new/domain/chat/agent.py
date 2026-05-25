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

# Import the core Agent class from PydanticAI
from pydantic_ai import Agent
# Import the OpenAI chat model wrapper from PydanticAI
from pydantic_ai.models.openai import OpenAIChatModel
# Import the OpenAI provider class, which manages credentials and endpoint URLs
from pydantic_ai.providers.openai import OpenAIProvider

# Import the default system prompt instructions that guide the AI's behavior
from .config import _DEFAULT_SYSTEM_PROMPT
# Import the tool that searches raw source document text chunks (fallback search)
from .tools import search_source_chunks
# Import the tools that allow the agent to interact specifically with our Wiki
from .wiki_tools import file_to_wiki, read_wiki_page, search_wiki_fts


def create_agent(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
) -> Agent[str, str]:
    """Return a configured wiki assistant agent.

    Args:
        base_url: OpenAI-compatible API base URL (e.g. OpenRouter, Local LM Studio, or OpenAI).
        api_key: API key to authenticate with the LLM provider.
        model: Model identifier string (e.g., "gpt-4o", "claude-3-5-sonnet").
        system_prompt: Loaded from wiki_config.toml; falls back to generic default.

    Returns:
        A fully-configured PydanticAI Agent instance.
        The Agent's type signature Agent[str, str] means:
        - Str 1: The type of the runtime dependency (the SQLite database path string).
        - Str 2: The type of the output returned by the agent (plain text response).

    Tools (in priority order per system prompt):
        read_wiki_page       — reads a wiki page from disk by its file path
        search_wiki_fts      — FTS5 search scoped strictly to wiki pages only
        file_to_wiki         — saves a synthesized response as a new/updated concept page
        search_source_chunks — FTS5 search on raw source chunks (fallback when wiki lacks answer)
    """

    # 1. Instantiate the Language Model (LLM) client.
    #    By using the OpenAIProvider, we can direct the agent to any OpenAI-compatible API
    #    endpoint (like OpenRouter, DeepSeek, or local models) by passing custom URLs.
    llm = OpenAIChatModel(
        model,
        provider=OpenAIProvider(base_url=base_url, api_key=api_key),
    )

    # 2. Build and return the configured Agent.
    return Agent(
        # Pass the configured language model
        llm,

        # Define the dependency type. The agent's tools require the path to the
        # SQLite database. This database path is passed at runtime using `deps`.
        deps_type=str,

        # Give the agent a clean name for internal logging/debugging
        name="wiki_assistant",

        # Inject the behavior instructions (System Prompt)
        system_prompt=system_prompt,

        # Register the suite of Python tool functions the agent is allowed to call.
        # PydanticAI automatically inspects these functions, parses their docstrings
        # and arguments, and explains them to the LLM so it knows exactly when to call them!
        tools=[read_wiki_page, search_wiki_fts, file_to_wiki, search_source_chunks],
    )
