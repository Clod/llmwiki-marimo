"""Tests for domain/wiki_settings.py — load_wiki_language."""

import logging

import pytest

from domain.wiki_settings import load_wiki_language


# ── Absent / missing config ───────────────────────────────────────────────────


def test_absent_config_file_returns_en(tmp_path) -> None:
    """No wiki_config.toml → default "en", no warning."""
    assert load_wiki_language(tmp_path) == "en"


def test_absent_wiki_section_returns_en(tmp_path) -> None:
    """Config file exists but has no [wiki] section → "en"."""
    (tmp_path / "wiki_config.toml").write_text('[assistant]\nsystem_prompt = "hello"\n', encoding="utf-8")
    assert load_wiki_language(tmp_path) == "en"


def test_absent_language_key_returns_en(tmp_path) -> None:
    """[wiki] section present but no language key → "en"."""
    (tmp_path / "wiki_config.toml").write_text("[wiki]\n", encoding="utf-8")
    assert load_wiki_language(tmp_path) == "en"


# ── Supported values ──────────────────────────────────────────────────────────


def test_language_en_returns_en(tmp_path) -> None:
    (tmp_path / "wiki_config.toml").write_text('[wiki]\nlanguage = "en"\n', encoding="utf-8")
    assert load_wiki_language(tmp_path) == "en"


def test_language_es_returns_es(tmp_path) -> None:
    (tmp_path / "wiki_config.toml").write_text('[wiki]\nlanguage = "es"\n', encoding="utf-8")
    assert load_wiki_language(tmp_path) == "es"


# ── Normalization (region variants) ──────────────────────────────────────────


@pytest.mark.parametrize("raw", ["ES", "es-AR", "es_ES"])
def test_region_variants_normalize_to_es(tmp_path, raw: str) -> None:
    (tmp_path / "wiki_config.toml").write_text(f'[wiki]\nlanguage = "{raw}"\n', encoding="utf-8")
    assert load_wiki_language(tmp_path) == "es"


# ── Unsupported language ──────────────────────────────────────────────────────


def test_unsupported_language_returns_en_with_warning(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "wiki_config.toml").write_text('[wiki]\nlanguage = "fr"\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_wiki_language(tmp_path)
    assert result == "en"
    assert caplog.records, "Expected a warning for unsupported language 'fr'"


# ── Malformed TOML ────────────────────────────────────────────────────────────


def test_malformed_toml_returns_en_with_warning(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "wiki_config.toml").write_text("{not valid toml", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_wiki_language(tmp_path)
    assert result == "en"
    assert caplog.records, "Expected a warning for malformed TOML"
