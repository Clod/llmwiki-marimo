"""The scalar settings of a wiki, as the Configuración screen shows and saves them.

Reading is `domain.chat.settings_writer.current` and `defaults`. A save goes through
`domain.chat.settings_writer.write_settings`, which keeps the file as written, and is one
commit of the wiki: `wiki_config.toml` is part of a point of the history, and a file left
uncommitted would stop a revert ("dirty"). The caller holds the wiki's lock.
"""

from __future__ import annotations

from domain.chat import settings_writer
from domain.chat.config import load_config
from domain.chat.settings_writer import SettingsError, parse_form

from services.wiki import Wiki

__all__ = ["SettingsError", "parse_form", "read_settings", "save_settings"]


def read_settings(wiki: Wiki) -> tuple[str, dict, dict]:
    """The wiki's language, the current value of each key and the default of each key."""
    config = load_config(wiki.path)
    return config.language, settings_writer.current(config), settings_writer.defaults(config.language)


def save_settings(wiki: Wiki, changes: dict) -> list[str]:
    """Validate and write `changes`; commit when something changed. Returns the keys written.
    Raises `SettingsError` and writes nothing when a value is invalid."""
    written = settings_writer.write_settings(wiki.path, changes)
    if written:
        from domain.tools.git_ops import auto_commit, init_wiki_repo

        init_wiki_repo(wiki.path)
        auto_commit(wiki.path, "settings: " + ", ".join(written))
    return written
