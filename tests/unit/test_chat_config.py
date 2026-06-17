"""Tests for domain/chat/config.py — load_config."""

from domain.chat.config import _DEFAULT_PROMPTS, load_config
from domain.i18n import get_locale


def test_load_config_missing_file_returns_defaults(tmp_path) -> None:
    cfg = load_config(tmp_path)  # no wiki_config.toml present
    assert cfg.suggested_prompts == _DEFAULT_PROMPTS
    # Regression guard: a missing config file means an English wiki.
    assert cfg.language == "en"


def test_load_config_default_prompts_copied_when_key_absent(tmp_path) -> None:
    """L1: when the TOML omits suggested_prompts, the returned list must be a
    copy of the default, not the shared module-level list."""
    (tmp_path / "wiki_config.toml").write_text(
        '[assistant]\nsystem_prompt = "custom"\n', encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.suggested_prompts == _DEFAULT_PROMPTS

    # Mutating the result must not corrupt the shared module default.
    cfg.suggested_prompts.append("MUTATED")
    assert "MUTATED" not in _DEFAULT_PROMPTS


def test_load_config_reads_suggested_prompts_from_toml(tmp_path) -> None:
    (tmp_path / "wiki_config.toml").write_text(
        '[assistant]\nsuggested_prompts = ["one", "two"]\n', encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.suggested_prompts == ["one", "two"]


def test_load_config_assistant_only_no_wiki_section_defaults_to_english(tmp_path) -> None:
    """A wiki_config.toml with an [assistant] section but no [wiki] section
    resolves to English — language and prompts both stay the default."""
    (tmp_path / "wiki_config.toml").write_text(
        '[assistant]\nsystem_prompt = "custom"\n', encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.language == "en"
    assert cfg.language != "es"
    assert cfg.suggested_prompts == _DEFAULT_PROMPTS


def test_load_config_spanish_wiki_no_suggested_prompts_uses_localized_defaults(
    tmp_path,
) -> None:
    """[wiki].language = "es" with no [assistant].suggested_prompts → cfg.language
    is "es" and the default suggested prompts are the localized (Spanish) ones."""
    (tmp_path / "wiki_config.toml").write_text(
        '[wiki]\nlanguage = "es"\n', encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.language == "es"
    assert cfg.suggested_prompts == list(get_locale("es").suggested_prompts)


def test_load_config_custom_suggested_prompts_respected_regardless_of_language(
    tmp_path,
) -> None:
    """A user-supplied suggested_prompts list always wins, even for a non-English
    wiki — the localized default only applies when the key is absent."""
    (tmp_path / "wiki_config.toml").write_text(
        '[wiki]\nlanguage = "es"\n\n'
        '[assistant]\nsuggested_prompts = ["custom one", "custom two"]\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.language == "es"
    assert cfg.suggested_prompts == ["custom one", "custom two"]
