"""Per-wiki settings that aren't assistant-specific (currently: content language).

PURPOSE FOR BEGINNERS:
Each wiki can declare its own content language in wiki_config.toml. This module
provides a single neutral loader so neither ingestion nor chat depends on the other
to read that setting. All language resolution (normalization, validation, fallback)
is delegated to domain.i18n so it happens in exactly one place.
"""

import logging
import tomllib
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
