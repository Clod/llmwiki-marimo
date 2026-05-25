"""Standalone config for the ingestion pipeline.

Does not import from api/config.py — self-contained so api_new/ can run
without the full api/ dependency tree.

env_file is resolved to an absolute path so the app works regardless of
the working directory from which marimo is launched.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = parent of api_new/
_ENV_FILE = str(Path(__file__).parent.parent / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Match the variable name used in .env
    WIKI_PATH: str = "."

    PDF_BACKEND: str = "opendataloader"   # "opendataloader" | "mistral"
    MISTRAL_API_KEY: str = ""

    # Main LLM — used by read_app; re-declared here as fallback for WIKI_LLM_*
    LLM_BASE_URL: str = "https://openrouter.ai/api/v1"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "anthropic/claude-haiku-4-5"

    # Wiki generation LLM — falls back to LLM_* if blank
    WIKI_LLM_BASE_URL: str = ""
    WIKI_LLM_API_KEY: str = ""
    WIKI_LLM_MODEL: str = ""


settings = Settings()
