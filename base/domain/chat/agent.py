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

from urllib.parse import urlparse

# Import the core Agent class from PydanticAI
from pydantic_ai import Agent
# Import the OpenAI chat model wrapper from PydanticAI
from pydantic_ai.models.openai import OpenAIChatModel
# Import the OpenAI provider class, which manages credentials and endpoint URLs
from pydantic_ai.providers.openai import OpenAIProvider
# Import ModelSettings to pin a deterministic sampling temperature.
from pydantic_ai.settings import ModelSettings

# Import the default system prompt instructions that guide the AI's behavior
from .config import _DEFAULT_SYSTEM_PROMPT
# Import the tool that searches raw source document text chunks (fallback search)
from .tools import search_source_chunks
# Import the read-only tools that let the agent interact with our Wiki.
# Note: the agent has NO write tool — creating/updating wiki pages is the user's
# explicit action via the "Save to wiki" form in read_app.py (save_to_wiki).
from .wiki_tools import read_wiki_page, search_wiki_fts
# Appends the wiki's chat answer-language directive to the system prompt.
from domain.i18n import apply_chat_directive


def _make_provider(base_url: str, api_key: str):
    """Pick the provider that profiles the model name correctly.

    OpenRouter namespaces models as ``vendor/model`` (e.g. ``openai/gpt-4o``).
    The generic ``OpenAIProvider`` mis-profiles the ``openai/*`` namespace as a
    *reasoning* model and then silently drops sampling parameters — including our
    ``temperature=0`` — which makes grounding non-deterministic (the eval flaps).
    OpenRouter's dedicated provider resolves vendor-prefixed profiles correctly,
    so route OpenRouter endpoints through it; everything else (OpenAI proper,
    LM Studio, Ollama, any OpenAI-compatible URL) keeps the generic provider.
    """
    host = (urlparse(base_url).hostname or "").lower()
    if host == "openrouter.ai" or host.endswith(".openrouter.ai"):
        # Imported lazily so the dependency is only touched on the OpenRouter path.
        from pydantic_ai.providers.openrouter import OpenRouterProvider

        return OpenRouterProvider(api_key=api_key)
    return OpenAIProvider(base_url=base_url, api_key=api_key)


def create_agent(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
    language: str = "en",
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

    Returns:
        A fully-configured PydanticAI Agent instance.
        The Agent's type signature Agent[str, str] means:
        - Str 1: The type of the runtime dependency (the SQLite database path string).
        - Str 2: The type of the output returned by the agent (plain text response).

    Tools (in priority order per system prompt) — all read-only:
        read_wiki_page       — reads a wiki page from disk by its file path
        search_wiki_fts      — FTS5 search scoped strictly to wiki pages only
        search_source_chunks — FTS5 search on raw source chunks (fallback when wiki lacks answer)

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
        provider=_make_provider(base_url, api_key),
    )

    # Append the wiki's chat answer-language directive (no-op for English, so the
    # English agent's system prompt stays byte-identical to before this feature).
    effective_prompt = apply_chat_directive(system_prompt, language)

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
        # answer language.
        system_prompt=effective_prompt,

        # Register the suite of Python tool functions the agent is allowed to call.
        # PydanticAI automatically inspects these functions, parses their docstrings
        # and arguments, and explains them to the LLM so it knows exactly when to call them!
        tools=[read_wiki_page, search_wiki_fts, search_source_chunks],

        # Pin temperature=0 (greedy decoding). A grounding/traceability agent
        # wants the single most-likely, corpus-grounded continuation — not
        # sampled variation. At higher temperatures the model intermittently
        # skips the retrieval tools or omits citations; temperature 0 makes the
        # retrieve-then-cite behavior deterministic and reproducible (and stops
        # reliability from depending on the provider's default sampling).
        model_settings=ModelSettings(temperature=0.0),
    )
