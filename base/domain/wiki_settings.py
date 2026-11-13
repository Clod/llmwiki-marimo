"""Per-wiki settings that aren't assistant-specific (currently: content language).

PURPOSE FOR BEGINNERS:
Each wiki can declare its own content language in wiki_config.toml. This module
provides a single neutral loader so neither ingestion nor chat depends on the other
to read that setting. All language resolution (normalization, validation, fallback)
is delegated to domain.i18n so it happens in exactly one place.
"""

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

from domain.i18n import get_locale  # returns a Locale; .code is the normalized code

logger = logging.getLogger(__name__)


def load_wiki_language(wiki_path: Path) -> str:
    """Read [wiki].language from WIKI_PATH/wiki_config.toml and return a normalized code.

    Resolution rules:
    - Absent file, absent [wiki] section, or absent language key → "en" (silent).
    - Malformed TOML → "en" with a logged warning (never crashes ingestion).
    - Unsupported value (e.g. "fr") → "en" with a logged warning (via get_locale).
    - Region variants normalize to the base code: "es-AR", "es_ES", "ES" → "es".

    Args:
        wiki_path: The directory that contains wiki_config.toml.

    Returns:
        A normalized, supported ISO 639-1 language code (e.g. "en" or "es").
    """
    config_file = Path(wiki_path) / "wiki_config.toml"
    if not config_file.exists():
        return get_locale(None).code
    try:
        with open(config_file, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s; using default language", config_file, exc)
        return get_locale(None).code
    raw = data.get("wiki", {}).get("language")
    return get_locale(raw).code  # normalize + validate + fallback in one place


def language_for_db(db_path: str | Path) -> str:
    """The language of the wiki that owns the index at `db_path`.

    The index lives at `<wiki>/.llmwiki/index.db`, so the wiki directory is
    `Path(db_path).parent.parent` — the same derivation the read app uses. For a
    path outside a wiki, `load_wiki_language` finds no config and returns "en".
    """
    return load_wiki_language(Path(db_path).parent.parent)


def write_language(wiki_path: Path, language: str) -> None:
    """Write `wiki_config.toml` with only `[wiki] language` into a wiki being created.

    A folder that already holds a `wiki_config.toml` is left as it is. `language` is
    normalized (`domain.i18n.get_locale`); an unsupported value writes "en".
    """
    wiki_path = Path(wiki_path)
    config_file = wiki_path / "wiki_config.toml"
    if config_file.exists():
        return
    wiki_path.mkdir(parents=True, exist_ok=True)
    config_file.write_text(f'[wiki]\nlanguage = "{get_locale(language).code}"\n', encoding="utf-8")


# ── [images]: the description of the images of the sources ───────────────────

DEFAULT_MIN_SIDE_PX = 64
DEFAULT_MIN_BYTES = 5000
DEFAULT_MAX_PER_DOCUMENT = 40


@dataclass(frozen=True)
class ImagesConfig:
    """The `[images]` section of `wiki_config.toml` (docs/design_image_handling.md, D2).

    `ignore` is true by default: the ingestion keeps no image of the sources and does
    not accept an image file as a source. The other keys apply only with `ignore = false`.
    """

    ignore: bool = True
    describe: bool = False
    min_side_px: int = DEFAULT_MIN_SIDE_PX
    min_bytes: int = DEFAULT_MIN_BYTES
    max_per_document: int = DEFAULT_MAX_PER_DOCUMENT


def _non_negative(value, default: int) -> int:
    """`value` as an integer of 0 or more; `default` when absent or invalid."""
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def images_config_from(section: dict | None) -> ImagesConfig:
    """The `[images]` table as an `ImagesConfig`; an absent or invalid key takes its default."""
    section = section if isinstance(section, dict) else {}
    ignore = section.get("ignore", True)
    describe = section.get("describe", False)
    return ImagesConfig(
        ignore=ignore if isinstance(ignore, bool) else True,
        describe=describe if isinstance(describe, bool) else False,
        min_side_px=_non_negative(section.get("min_side_px"), DEFAULT_MIN_SIDE_PX),
        min_bytes=_non_negative(section.get("min_bytes"), DEFAULT_MIN_BYTES),
        max_per_document=_non_negative(section.get("max_per_document"), DEFAULT_MAX_PER_DOCUMENT),
    )


def load_images_config(wiki_path: Path) -> ImagesConfig:
    """Read `[images]` from WIKI_PATH/wiki_config.toml. An absent file or section, or
    malformed TOML, gives the defaults (`ignore = true`, `describe = false`): never
    crashes ingestion."""
    config_file = Path(wiki_path) / "wiki_config.toml"
    if not config_file.exists():
        return ImagesConfig()
    try:
        with open(config_file, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s; images are not described", config_file, exc)
        return ImagesConfig()
    return images_config_from(data.get("images"))
