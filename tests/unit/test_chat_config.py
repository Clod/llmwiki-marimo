"""Tests for domain/chat/config.py — load_config."""

from domain.chat.config import _DEFAULT_PROMPTS, load_config


def test_load_config_missing_file_returns_defaults(tmp_path) -> None:
    cfg = load_config(tmp_path)  # no wiki_config.toml present
    assert cfg.suggested_prompts == _DEFAULT_PROMPTS


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
