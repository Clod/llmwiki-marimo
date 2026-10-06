"""The settings of one web application.

`WebSettings.from_env` reads the same `.env` as the marimo apps
(`base/config.py`) plus `WIKI_PATH` and `WIKI_HOME` (`domain/wiki_registry.py`).
A test builds a `WebSettings` directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WebSettings:
    """Where the wikis are, and which model the chat and the ingestion call.

    `wiki_path` is the wiki selected by default; `wiki_home` is the folder the
    picker scans for other wikis. `recent_file` is the recent-wikis list the
    marimo apps share. The `ingest_*` fields fall back to the chat fields when
    blank, as in `ingest_app`.
    """

    wiki_path: str | None
    wiki_home: Path
    recent_file: Path
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    ingest_base_url: str = ""
    ingest_api_key: str = ""
    ingest_model: str = ""

    @classmethod
    def from_env(cls) -> WebSettings:
        from config import settings
        from domain.wiki_registry import RECENT_FILE, resolve_wiki_home

        wiki_path = os.environ.get("WIKI_PATH", "").strip() or None
        return cls(
            wiki_path=wiki_path,
            wiki_home=resolve_wiki_home(wiki_path),
            recent_file=RECENT_FILE,
            llm_base_url=settings.LLM_BASE_URL,
            llm_api_key=settings.LLM_API_KEY,
            llm_model=settings.LLM_MODEL,
            ingest_base_url=settings.WIKI_LLM_BASE_URL or settings.LLM_BASE_URL,
            ingest_api_key=settings.WIKI_LLM_API_KEY or settings.LLM_API_KEY,
            ingest_model=settings.WIKI_LLM_MODEL or settings.LLM_MODEL,
        )
