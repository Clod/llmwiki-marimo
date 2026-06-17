"""Tests for domain/i18n.py — Locale registry and language resolution."""

import logging

import pytest

from domain.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    apply_chat_directive,
    get_locale,
    normalize_language,
    with_content_directive,
)


# ── get_locale ────────────────────────────────────────────────────────────────


def test_get_locale_en_code() -> None:
    loc = get_locale("en")
    assert loc.code == "en"


def test_get_locale_es_code() -> None:
    loc = get_locale("es")
    assert loc.code == "es"


def test_get_locale_es_headers_non_empty_and_differ_from_en() -> None:
    en = get_locale("en")
    es = get_locale("es")
    # Spanish headers must be non-empty and different from their English equivalents.
    assert es.h_summary and es.h_summary != en.h_summary
    assert es.h_key_topics and es.h_key_topics != en.h_key_topics
    assert es.h_key_entities and es.h_key_entities != en.h_key_entities
    assert es.h_sources and es.h_sources != en.h_sources
    assert es.h_definition and es.h_definition != en.h_definition
    assert es.h_key_characteristics and es.h_key_characteristics != en.h_key_characteristics
    assert es.h_context and es.h_context != en.h_context
    assert es.h_see_also and es.h_see_also != en.h_see_also


def test_get_locale_en_directives_are_empty_strings() -> None:
    """English directives must be empty so the en path is byte-identical to today."""
    loc = get_locale("en")
    assert loc.content_directive == ""
    assert loc.chat_directive == ""


def test_get_locale_es_directives_non_empty() -> None:
    loc = get_locale("es")
    assert loc.content_directive != ""
    assert loc.chat_directive != ""


# ── SUPPORTED_LANGUAGES ───────────────────────────────────────────────────────


def test_supported_languages_contains_en_and_es() -> None:
    assert SUPPORTED_LANGUAGES == ("en", "es")


# ── normalize_language ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        # Region variants normalize to the base code.
        ("ES", "es"),
        ("es-AR", "es"),
        ("es_ES", "es"),
        # Exact supported codes.
        ("en", "en"),
        ("es", "es"),
    ],
)
def test_normalize_language_supported(value: str, expected: str) -> None:
    assert normalize_language(value) == expected


@pytest.mark.parametrize(
    "value",
    ["fr", "xx", "de"],
)
def test_normalize_language_unsupported_string_falls_back_and_warns(
    value: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Unsupported but stringy values must fall back to DEFAULT_LANGUAGE and log a warning."""
    with caplog.at_level(logging.WARNING, logger="domain.i18n"):
        result = normalize_language(value)
    assert result == DEFAULT_LANGUAGE
    assert caplog.records, f"Expected a warning for unsupported language {value!r}"
    assert any("Unsupported" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", 123],
)
def test_normalize_language_invalid_input_falls_back_silently(value: object) -> None:
    """None, empty string, whitespace-only, and non-string → DEFAULT_LANGUAGE, no warning."""
    result = normalize_language(value)  # type: ignore[arg-type]
    assert result == DEFAULT_LANGUAGE


# ── with_content_directive ────────────────────────────────────────────────────


def test_with_content_directive_en_is_noop() -> None:
    base = "You are an assistant."
    result = with_content_directive(base, "en")
    assert result == base


def test_with_content_directive_es_appends_directive() -> None:
    base = "You are an assistant."
    result = with_content_directive(base, "es")
    es_directive = get_locale("es").content_directive
    assert result == f"{base}\n\n{es_directive}"
    assert result.endswith(es_directive)


# ── apply_chat_directive ──────────────────────────────────────────────────────


def test_apply_chat_directive_en_is_noop() -> None:
    base = "You are a chat assistant."
    result = apply_chat_directive(base, "en")
    assert result == base


def test_apply_chat_directive_es_appends_directive() -> None:
    base = "You are a chat assistant."
    result = apply_chat_directive(base, "es")
    es_directive = get_locale("es").chat_directive
    assert result == f"{base}\n\n{es_directive}"
    assert result.endswith(es_directive)
