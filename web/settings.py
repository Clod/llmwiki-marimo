"""The settings of one web application.

`WebSettings.from_env` reads the same `.env` as the marimo apps
(`base/config.py`) plus `WIKI_PATH` and `WIKI_HOME` (`domain/wiki_registry.py`).
A test builds a `WebSettings` directly.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
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
    # The `.env` the Settings screen writes the two models to. None: the screen shows the
    # models without a form (a test that does not edit them, or no file to write).
    env_file: Path | None = None

    def with_models(self, values: dict[str, str]) -> WebSettings:
        """These settings with the models of `values` (the `MODEL_KEYS` of `.env`), the
        blank ingestion values falling back to the reading ones as in `from_env`."""
        read = {k: values.get(f"LLM_{k}", "") for k in ("BASE_URL", "API_KEY", "MODEL")}
        ingest = {k: values.get(f"WIKI_LLM_{k}", "") or read[k] for k in read}
        return replace(self, llm_base_url=read["BASE_URL"], llm_api_key=read["API_KEY"],
                       llm_model=read["MODEL"], ingest_base_url=ingest["BASE_URL"],
                       ingest_api_key=ingest["API_KEY"], ingest_model=ingest["MODEL"])

    @classmethod
    def from_env(cls) -> WebSettings:
        from config import ENV_FILE, settings
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
            env_file=Path(ENV_FILE),
        )


# The keys of `.env` the Settings screen edits: the reading model (chat) and the
# ingestion model, whose blank values fall back to the reading ones.
MODEL_KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
              "WIKI_LLM_BASE_URL", "WIKI_LLM_API_KEY", "WIKI_LLM_MODEL")
_PLAIN = re.compile(r"[A-Za-z0-9_./:@+=,~-]*")


def read_models(env_file: Path) -> dict[str, str]:
    """The `MODEL_KEYS` written in `env_file`, blank when the file or the key is missing."""
    from dotenv import dotenv_values

    values = dotenv_values(env_file) if env_file.is_file() else {}
    return {key: values.get(key) or "" for key in MODEL_KEYS}


def _env_line(key: str, value: str) -> str:
    if _PLAIN.fullmatch(value):
        return f"{key}={value}"
    return f'{key}="' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_models(env_file: Path, values: dict[str, str]) -> None:
    """Write `values` (keys of `MODEL_KEYS`) to `env_file`: each key replaces its line, or is
    added at the end; the other lines and the comments stay. A new file is readable by its
    owner only, since it holds API keys."""
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.is_file() else []
    written = set()
    for i, line in enumerate(lines):
        # Every line of a key is replaced: when a key appears twice, the last one counts.
        match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in values:
            lines[i] = _env_line(match.group(1), values[match.group(1)])
            written.add(match.group(1))
    lines += [_env_line(key, value) for key, value in values.items() if key not in written]
    if not env_file.exists():
        os.close(os.open(env_file, os.O_CREAT | os.O_WRONLY, 0o600))
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
