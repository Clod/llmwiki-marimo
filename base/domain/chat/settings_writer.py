"""Edits of the scalar settings of `wiki_config.toml`, keeping the file as written.

The keys the Configuración screen edits, named `section.key`:

  - `[assistant] system_prompt`, `suggested_prompts`;
  - `[pre_retrieval] enabled`, `min_coverage`, `condense_question`;
  - `[verify] threshold`, `candidates`, `number_tolerance`;
  - `[images] ignore`, `describe`, `min_side_px`, `min_bytes`, `max_per_document`.

`[wiki] language` is never written here: changing it needs a reindex. The vocabulary
lists are edited by `config_writer`.

The rules of validity are those of `load_config` (`_threshold`, `_candidates`, `_number_tolerance`), except
that `load_config` falls back to the default on an invalid value and this module refuses it.
`tomlkit` rewrites only the keys that change; the comments and the order stay as written.
A key absent from the file whose new value is the default stays absent. A missing file
or section is created.

Pure file I/O, no git.
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from domain.chat import config_writer
from domain.chat.config import (
    DEFAULT_NUMBER_TOLERANCE,
    DEFAULT_VERIFY_CANDIDATES,
    DEFAULT_VERIFY_THRESHOLD,
    WikiAssistantConfig,
)
from domain.i18n import get_locale
from domain.prompts import load_prompt
from domain.wiki_settings import (
    DEFAULT_MAX_PER_DOCUMENT, DEFAULT_MIN_BYTES, DEFAULT_MIN_SIDE_PX, load_wiki_language,
)

SYSTEM_PROMPT = "assistant.system_prompt"
SUGGESTED_PROMPTS = "assistant.suggested_prompts"
ENABLED = "pre_retrieval.enabled"
MIN_COVERAGE = "pre_retrieval.min_coverage"
CONDENSE = "pre_retrieval.condense_question"
THRESHOLD = "verify.threshold"
CANDIDATES = "verify.candidates"
IGNORE = "images.ignore"
DESCRIBE = "images.describe"
MIN_SIDE_PX = "images.min_side_px"
MIN_BYTES = "images.min_bytes"
MAX_PER_DOCUMENT = "images.max_per_document"
NUMBER_TOLERANCE = "verify.number_tolerance"

KEYS = (SYSTEM_PROMPT, SUGGESTED_PROMPTS, ENABLED, MIN_COVERAGE, CONDENSE, THRESHOLD, CANDIDATES,
        NUMBER_TOLERANCE, IGNORE, DESCRIBE, MIN_SIDE_PX, MIN_BYTES, MAX_PER_DOCUMENT)
_BOOLEANS = (ENABLED, CONDENSE, IGNORE, DESCRIBE)
_FRACTIONS = (MIN_COVERAGE, THRESHOLD, NUMBER_TOLERANCE)
_COUNTS = (MIN_SIDE_PX, MIN_BYTES)   # whole numbers, 0 or more


class SettingsError(Exception):
    """Values refused before the file is written.

    `errors` maps each refused key to a code: "empty", "not_a_number", "out_of_range",
    "not_positive" or "unknown". The web layer turns the code into a message.
    """

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__(", ".join(f"{key}: {code}" for key, code in errors.items()))
        self.errors = errors


def defaults(language: str) -> dict:
    """The value each key takes when the file does not set it, for a wiki in `language`."""
    return {
        SYSTEM_PROMPT: load_prompt("system_prompt", language).strip(),
        SUGGESTED_PROMPTS: list(get_locale(language).suggested_prompts),
        ENABLED: False, MIN_COVERAGE: 0.2, CONDENSE: True,
        THRESHOLD: DEFAULT_VERIFY_THRESHOLD, CANDIDATES: DEFAULT_VERIFY_CANDIDATES,
        IGNORE: True, DESCRIBE: False, MIN_SIDE_PX: DEFAULT_MIN_SIDE_PX, MIN_BYTES: DEFAULT_MIN_BYTES,
        MAX_PER_DOCUMENT: DEFAULT_MAX_PER_DOCUMENT,
        NUMBER_TOLERANCE: DEFAULT_NUMBER_TOLERANCE,
    }


def current(config: WikiAssistantConfig) -> dict:
    """The value each key has now, from a loaded config."""
    return {
        SYSTEM_PROMPT: config.system_prompt, SUGGESTED_PROMPTS: list(config.suggested_prompts),
        ENABLED: config.pre_retrieval, MIN_COVERAGE: config.min_coverage, CONDENSE: config.condense_question,
        THRESHOLD: config.verify_threshold, CANDIDATES: config.verify_candidates,
        IGNORE: config.images.ignore, DESCRIBE: config.images.describe, MIN_SIDE_PX: config.images.min_side_px,
        MIN_BYTES: config.images.min_bytes, MAX_PER_DOCUMENT: config.images.max_per_document,
        NUMBER_TOLERANCE: config.number_tolerance,
    }


# ── validation ────────────────────────────────────────────────────────────────

def _error(key: str, value) -> str | None:
    if key == SYSTEM_PROMPT:
        return None if isinstance(value, str) and value.strip() else "empty"
    if key == SUGGESTED_PROMPTS:
        return None if isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value) else "empty"
    if key in _BOOLEANS:
        return None if isinstance(value, bool) else "unknown"
    if key in _FRACTIONS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "not_a_number"
        return None if 0.0 <= value <= 1.0 else "out_of_range"
    if key in (CANDIDATES, MAX_PER_DOCUMENT):
        return None if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else "not_positive"
    if key in _COUNTS:
        return None if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else "negative"
    return "unknown"


def validate(changes: dict) -> dict[str, str]:
    """The refused keys of `changes`, each with its code; empty when every value is valid."""
    return {key: code for key, value in changes.items() if (code := _error(key, value))}


def parse_form(raw: dict) -> tuple[dict, dict[str, str]]:
    """Typed values from the strings of a form (`raw` is keyed by `section.key`; a checkbox is a bool).

    Returns the values that parsed and the errors (unparsable or invalid) of the rest. A decimal comma is accepted.
    """
    values: dict = {}
    errors: dict[str, str] = {}
    for key in KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if key == SUGGESTED_PROMPTS:
            values[key] = [line.strip() for line in str(value).splitlines() if line.strip()]
        elif key in _FRACTIONS:
            try:
                values[key] = float(str(value).strip().replace(",", "."))
            except ValueError:
                errors[key] = "not_a_number"
        elif key in (CANDIDATES, MAX_PER_DOCUMENT, *_COUNTS):
            try:
                values[key] = int(str(value).strip())
            except ValueError:
                errors[key] = "negative" if key in _COUNTS else "not_positive"
        else:
            values[key] = value
    return values, {**validate(values), **errors}


# ── writing ───────────────────────────────────────────────────────────────────

def _same(key: str, old, new) -> bool:
    if key == SYSTEM_PROMPT:
        return str(old).strip() == new.strip()
    if key == SUGGESTED_PROMPTS:
        return list(old) == list(new)
    return old == new


def _item(key: str, value):
    if key == SYSTEM_PROMPT:
        text = value.strip()
        # A multi-line prompt is written as a multi-line string, as it is hand-written.
        return tomlkit.string("\n" + text + "\n", multiline=True) if "\n" in text else text
    if key == SUGGESTED_PROMPTS:
        return tomlkit.array(list(value))
    return value


def write_settings(wiki_path: Path, changes: dict) -> list[str]:
    """Write `changes` (`section.key` → typed value) into `wiki_config.toml`; return the keys written.

    Raises `SettingsError` and writes nothing when a value is invalid or a key is not editable.
    """
    errors = validate(changes)
    if errors:
        raise SettingsError(errors)
    fallback = defaults(load_wiki_language(wiki_path))
    doc = config_writer._load(wiki_path)
    written: list[str] = []
    for key, value in changes.items():
        section, name = key.split(".", 1)
        table = doc.get(section)
        if table is not None and name in table:
            if _same(key, table[name], value):
                continue
        elif _same(key, fallback[key], value):
            continue
        table = config_writer._table(doc, section)
        table[name] = _item(key, value)
        written.append(key)
    if written:
        config_writer._save(wiki_path, doc)
    return written
