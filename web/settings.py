"""The settings of one web application.

`WebSettings.from_env` reads the `.env` of `base/config.py`, and `WIKI_PATH` and `WIKI_HOME` from the environment or, failing that, from the same `.env` (`wiki_locations`).
A test builds a `WebSettings` directly.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

# The example wikis shipped with the application.
EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


@dataclass(frozen=True)
class WebSettings:
    """Where the wikis are, and which model the chat and the ingestion call.

    `wiki_path` is the wiki selected by default; `wiki_home` is the folder the
    picker scans for other wikis. `recent_file` is the recent-wikis list of
    the picker. The `ingest_*` fields fall back to the chat fields when blank.
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
    # The vision model that describes images; blank values fall back to the ingestion
    # model, and that one to the chat model (`config.Settings.vision`).
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_model: str = ""
    # The `.env` the Settings screen writes the models to. None: the screen shows the
    # models without a form (a test that does not edit them, or no file to write).
    env_file: Path | None = None
    # The example wikis the picker offers to copy (`examples/` of the repository). None:
    # the picker shows no example (a test that does not need them).
    examples_dir: Path | None = None

    def with_models(self, values: dict[str, str]) -> WebSettings:
        """These settings with the models of `values` (the `MODEL_KEYS` of `.env`), as in
        `from_env`: the blank ingestion values fall back to the reading ones, and the blank
        vision values to the ingestion ones."""
        read = {k: values.get(f"LLM_{k}", "") for k in ("BASE_URL", "API_KEY", "MODEL")}
        ingest = {k: values.get(f"WIKI_LLM_{k}", "") or read[k] for k in read}
        vision = {k: values.get(f"VISION_{k}", "") or ingest[k] for k in read}
        return replace(self, llm_base_url=read["BASE_URL"], llm_api_key=read["API_KEY"],
                       llm_model=read["MODEL"], ingest_base_url=ingest["BASE_URL"],
                       ingest_api_key=ingest["API_KEY"], ingest_model=ingest["MODEL"],
                       vision_base_url=vision["BASE_URL"], vision_api_key=vision["API_KEY"],
                       vision_model=vision["MODEL"])

    @classmethod
    def from_env(cls) -> WebSettings:
        from config import ENV_FILE, settings
        from domain.wiki_registry import RECENT_FILE, resolve_wiki_home

        wiki_path, wiki_home = wiki_locations(os.environ, read_env(Path(ENV_FILE)))
        return cls(
            wiki_path=wiki_path,
            wiki_home=resolve_wiki_home(wiki_path, wiki_home),
            recent_file=RECENT_FILE,
            llm_base_url=settings.LLM_BASE_URL,
            llm_api_key=settings.LLM_API_KEY,
            llm_model=settings.LLM_MODEL,
            ingest_base_url=settings.WIKI_LLM_BASE_URL or settings.LLM_BASE_URL,
            ingest_api_key=settings.WIKI_LLM_API_KEY or settings.LLM_API_KEY,
            ingest_model=settings.WIKI_LLM_MODEL or settings.LLM_MODEL,
            vision_base_url=settings.vision()[0],
            vision_api_key=settings.vision()[1],
            vision_model=settings.vision()[2],
            env_file=Path(ENV_FILE),
            examples_dir=EXAMPLES_DIR,
        )


# The keys of `.env` the Settings screen edits: the reading model (chat), the ingestion
# model, whose blank values fall back to the reading ones, the vision model, whose blank
# values fall back to the ingestion ones, and the OCR engine.
MODEL_KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
              "WIKI_LLM_BASE_URL", "WIKI_LLM_API_KEY", "WIKI_LLM_MODEL",
              "VISION_BASE_URL", "VISION_API_KEY", "VISION_MODEL", "OCR_ENGINE")
_PLAIN = re.compile(r"[A-Za-z0-9_./:@+=,~-]*")


def read_env(env_file: Path) -> dict[str, str]:
    """The keys of `env_file`, empty when the file is missing."""
    from dotenv import dotenv_values

    values = dotenv_values(env_file) if env_file.is_file() else {}
    return {key: value or "" for key, value in values.items()}


def wiki_locations(environ, env_values: dict[str, str]) -> tuple[str | None, str]:
    """`WIKI_PATH` and `WIKI_HOME`: the environment first, then `.env` (`env_values`).

    `pydantic-settings` reads `.env` only for the fields of `config.Settings`, and these
    two keys are read with `os.environ`: without this function a value written in `.env`
    had no effect. A `WIKI_PATH` taken from `.env` is used only when its folder exists,
    so the placeholder of an unedited copy of `.env.example` (`/path/to/your/wiki`)
    is not opened as a wiki. Returns `(wiki_path or None, wiki_home or "")`.
    """
    wiki_path = environ.get("WIKI_PATH", "").strip() or None
    if wiki_path is None:
        candidate = env_values.get("WIKI_PATH", "").strip()
        if candidate and Path(candidate).expanduser().is_dir():
            wiki_path = candidate
    wiki_home = environ.get("WIKI_HOME", "").strip() or env_values.get("WIKI_HOME", "").strip()
    return wiki_path, wiki_home


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
