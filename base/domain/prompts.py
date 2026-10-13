"""The chat prompts, read from files.

Each prompt is a plain-text file `prompts/<name>.<lang>.md` at the root of the repository:
the file is the prompt, with no front-matter, and `load_prompt` returns it unchanged (the
trailing newline of a file is part of the prompt). A language without the file falls back to
English; a missing English file is an error, never an empty prompt (an empty English file is
a prompt that is empty on purpose, as the English language directive).
"""

from __future__ import annotations

from pathlib import Path

from domain.i18n import DEFAULT_LANGUAGE, normalize_language

# base/domain/prompts.py → the repository root.
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def load_prompt(name: str, language: str | None) -> str:
    """The text of prompt `name` in `language`, or in English when that file does not exist."""
    english = PROMPTS_DIR / f"{name}.{DEFAULT_LANGUAGE}.md"
    if not english.is_file():
        raise FileNotFoundError(f"missing prompt file: {english}")
    path = PROMPTS_DIR / f"{name}.{normalize_language(language)}.md"
    return (path if path.is_file() else english).read_text(encoding="utf-8")
