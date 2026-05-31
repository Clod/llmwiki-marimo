"""Standalone settings for the marimo apps and the ingestion/chat domain.

Self-contained: nothing else in `base/` depends on app wiring, so the pipeline
and chat agent can be driven directly (tests, scripts) with just these settings.

`.env` is resolved to an absolute path off the project root, so the apps read the
same config regardless of the working directory marimo is launched from.

No provider, model, or endpoint is hardcoded — every LLM value comes from the
environment (see `.env.example`). Blanks are left blank on purpose; the caller
that builds an LLM client decides what to require.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = parent of base/
_ENV_FILE = str(Path(__file__).parent.parent / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Default wiki opened on launch; the apps' picker can switch it at runtime.
    WIKI_PATH: str = "."

    PDF_BACKEND: str = "opendataloader"   # "opendataloader" (no key) | "mistral"
    MISTRAL_API_KEY: str = ""

    # Chat LLM (read_app). Also the fallback for the WIKI_LLM_* group below.
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""

    # Wiki-generation LLM (ingest_app). Each value falls back to its LLM_*
    # counterpart when blank — see ingest_app's setup cell.
    WIKI_LLM_BASE_URL: str = ""
    WIKI_LLM_API_KEY: str = ""
    WIKI_LLM_MODEL: str = ""


settings = Settings()


def require_llm_config(base_url: str, api_key: str, model: str, *, purpose: str) -> None:
    """Fail fast with an actionable message if an LLM client can't be built.

    Call this in the apps right before constructing an OpenAI/agent client —
    never at import time, so tests/CI (which don't build real clients) are
    unaffected. The message states exactly *where* (the .env path) and *what*
    (which variables) the user must fix.
    """
    fields = {"BASE_URL": base_url, "API_KEY": api_key, "MODEL": model}
    missing = [name for name, value in fields.items() if not (value or "").strip()]
    if not missing:
        return

    missing_vars = ", ".join(f"LLM_{name}" for name in missing)
    raise RuntimeError(
        f"\nLLM is not configured for {purpose}.\n"
        f"  Missing: {missing_vars}\n"
        f"  Where:   {_ENV_FILE}\n"
        f"  What:    set LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL in that file\n"
        f"           (for ingestion you may set WIKI_LLM_* instead; blank\n"
        f"            WIKI_LLM_* values fall back to the LLM_* ones).\n"
        f"  If the file does not exist, copy .env.example to .env and fill it in.\n"
        f"  .env.example has ready-to-use examples for OpenRouter, Ollama, and LM Studio."
    )
