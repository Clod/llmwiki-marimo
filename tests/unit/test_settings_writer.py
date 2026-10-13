"""The edits of the scalar settings of wiki_config.toml keep the file as written."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from domain.chat import settings_writer as sw
from domain.chat.config import DEFAULT_VERIFY_CANDIDATES, DEFAULT_VERIFY_THRESHOLD, load_config
from domain.prompts import load_prompt

_DEFAULT_SYSTEM_PROMPT = load_prompt("system_prompt", "en")

CONFIG = '''# Comment on top, kept.
[wiki]
language = "es"  # the language

# The assistant.
[assistant]
system_prompt = """
Old prompt.
"""
suggested_prompts = ["one", "two"]  # inline comment

[pre_retrieval]
enabled = true
# the coverage
min_coverage = 0.3

[fuera_de_alcance]
terminos = ["cripto"]
'''


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    (tmp_path / "wiki_config.toml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


def text(wiki: Path) -> str:
    return (wiki / "wiki_config.toml").read_text(encoding="utf-8")


def data(wiki: Path) -> dict:
    return tomllib.loads(text(wiki))


def test_one_key_changed_leaves_the_rest_of_the_file_byte_identical(wiki):
    before = text(wiki)
    assert sw.write_settings(wiki, {"pre_retrieval.min_coverage": 0.5}) == ["pre_retrieval.min_coverage"]
    changed = [(a, b) for a, b in zip(before.splitlines(), text(wiki).splitlines()) if a != b]
    assert changed == [("min_coverage = 0.3", "min_coverage = 0.5")]
    assert len(before.splitlines()) == len(text(wiki).splitlines())


def test_a_value_equal_to_the_current_one_writes_nothing(wiki):
    before = text(wiki)
    assert sw.write_settings(wiki, {"pre_retrieval.enabled": True, "assistant.suggested_prompts": ["one", "two"],
                                    "assistant.system_prompt": "Old prompt.\n"}) == []
    assert text(wiki) == before


def test_the_system_prompt_and_the_prompts_are_replaced_and_the_inline_comment_stays(wiki):
    sw.write_settings(wiki, {"assistant.system_prompt": "New line 1.\nNew \"line\" 2 \\ end.",
                             "assistant.suggested_prompts": ["x", "y", "z"]})
    assert data(wiki)["assistant"]["system_prompt"].strip() == 'New line 1.\nNew "line" 2 \\ end.'
    assert data(wiki)["assistant"]["suggested_prompts"] == ["x", "y", "z"]
    assert "# inline comment" in text(wiki) and "# The assistant." in text(wiki)
    assert load_config(wiki).system_prompt == 'New line 1.\nNew "line" 2 \\ end.'


def test_the_other_sections_and_the_language_are_not_touched(wiki):
    sw.write_settings(wiki, {"verify.threshold": 0.8, "pre_retrieval.enabled": False})
    parsed = data(wiki)
    assert parsed["wiki"] == {"language": "es"} and parsed["fuera_de_alcance"] == {"terminos": ["cripto"]}
    assert "# Comment on top, kept." in text(wiki) and "# the coverage" in text(wiki)


def test_a_missing_section_is_created(wiki):
    sw.write_settings(wiki, {"verify.threshold": 0.8, "verify.candidates": 9})
    assert data(wiki)["verify"] == {"threshold": 0.8, "candidates": 9}
    config = load_config(wiki)
    assert (config.verify_threshold, config.verify_candidates) == (0.8, 9)


def test_a_missing_file_is_created(tmp_path):
    sw.write_settings(tmp_path, {"pre_retrieval.condense_question": False})
    assert data(tmp_path) == {"pre_retrieval": {"condense_question": False}}


def test_a_default_that_is_absent_stays_absent(wiki):
    before = text(wiki)
    sw.write_settings(wiki, {"verify.threshold": DEFAULT_VERIFY_THRESHOLD, "verify.candidates": DEFAULT_VERIFY_CANDIDATES,
                             "pre_retrieval.condense_question": True})
    assert text(wiki) == before and "verify" not in data(wiki)


def test_the_default_system_prompt_is_absent_when_the_file_has_none(tmp_path):
    (tmp_path / "wiki_config.toml").write_text('[wiki]\nlanguage = "en"\n', encoding="utf-8")
    sw.write_settings(tmp_path, {"assistant.system_prompt": _DEFAULT_SYSTEM_PROMPT,
                                 "assistant.suggested_prompts": sw.defaults("en")["assistant.suggested_prompts"]})
    assert "assistant" not in data(tmp_path)


def test_the_default_prompts_follow_the_language_of_the_wiki():
    assert sw.defaults("es")["assistant.suggested_prompts"] != sw.defaults("en")["assistant.suggested_prompts"]


@pytest.mark.parametrize("changes, key, code", [
    ({"assistant.system_prompt": "  \n "}, "assistant.system_prompt", "empty"),
    ({"verify.threshold": 1.5}, "verify.threshold", "out_of_range"),
    ({"pre_retrieval.min_coverage": -0.1}, "pre_retrieval.min_coverage", "out_of_range"),
    ({"verify.candidates": 0}, "verify.candidates", "not_positive"),
    ({"verify.candidates": True}, "verify.candidates", "not_positive"),
    ({"wiki.language": "en"}, "wiki.language", "unknown"),
])
def test_an_invalid_value_is_refused_and_nothing_is_written(wiki, changes, key, code):
    before = text(wiki)
    with pytest.raises(sw.SettingsError) as raised:
        sw.write_settings(wiki, {**changes, "pre_retrieval.min_coverage": changes.get("pre_retrieval.min_coverage", 0.9)})
    assert raised.value.errors[key] == code
    assert text(wiki) == before


def test_the_form_values_are_parsed():
    values, errors = sw.parse_form({"assistant.system_prompt": "P", "assistant.suggested_prompts": " a \n\n b\r\n",
                                    "pre_retrieval.enabled": True, "pre_retrieval.min_coverage": "0,25",
                                    "pre_retrieval.condense_question": False, "verify.threshold": "0.6",
                                    "verify.candidates": "7"})
    assert errors == {} and values == {
        "assistant.system_prompt": "P", "assistant.suggested_prompts": ["a", "b"], "pre_retrieval.enabled": True,
        "pre_retrieval.min_coverage": 0.25, "pre_retrieval.condense_question": False,
        "verify.threshold": 0.6, "verify.candidates": 7}


def test_the_form_reports_what_is_not_a_number():
    _, errors = sw.parse_form({"verify.threshold": "abc", "verify.candidates": "2.5", "pre_retrieval.min_coverage": "2"})
    assert errors == {"verify.threshold": "not_a_number", "verify.candidates": "not_positive",
                      "pre_retrieval.min_coverage": "out_of_range"}
