"""Tests for domain/chat/intent.py and the `[intent]` edits of config_writer.

The lists of the intent predicates: the `[intent]` section of `wiki_config.toml`,
or the base lists (the language for `collection`, the active domains for
`advisory`). The first edit of a list writes the whole base list.
"""

import shutil
from pathlib import Path

import pytest

from domain.chat import config_writer
from domain.chat.config import load_config
from domain.chat.config_writer import VocabularyEditError
from domain.chat.intent import base_collection, base_lists, load_intent

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def test_each_language_has_its_base_collection_list():
    assert "cada" in base_collection("es") and "each" not in base_collection("es")
    assert "each" in base_collection("en") and "cada" not in base_collection("en")
    assert base_collection("it") == ()


def test_a_key_of_the_section_replaces_its_base_list(tmp_path):
    lists = load_intent(tmp_path, "es", {"collection": ["compara los"]})
    assert lists.collection == ("compara los",)
    assert lists.written == {"collection": True, "advisory": False}
    assert lists.language == "es"


def test_without_the_section_the_base_lists_apply(tmp_path):
    lists = load_intent(tmp_path, "es", {})
    assert lists.collection == base_collection("es")
    assert lists.written == {"collection": False, "advisory": False}


def test_a_wiki_without_an_advisory_domain_has_no_advisory_list(tmp_path):
    lists = load_intent(tmp_path, "es", {})
    assert lists.advisory == () and lists.advisory_shapes == ()


def test_the_finance_wiki_takes_the_advisory_list_of_its_domain(tmp_path):
    workspace = tmp_path / "fin"
    shutil.copytree(EXAMPLES / "finanzas-argentinas", workspace)
    lists = load_intent(workspace, "es", {})
    assert "alternativ" in lists.advisory and lists.advisory_shapes
    assert base_lists(workspace, "es")["advisory"] == lists.advisory


def test_load_config_keeps_the_section_as_written(tmp_path):
    (tmp_path / "wiki_config.toml").write_text('[intent]\ncollection = ["compara los"]\n', encoding="utf-8")
    assert load_config(tmp_path).intent_section == {"collection": ["compara los"]}


def test_the_first_edit_writes_the_base_list_with_the_edit(tmp_path):
    (tmp_path / "wiki_config.toml").write_text("# A comment.\n", encoding="utf-8")
    config_writer.add_intent_cue(tmp_path, "collection", "compara los", ("cada", "todos los"))
    assert load_config(tmp_path).intent_section["collection"] == ["cada", "todos los", "compara los"]
    assert (tmp_path / "wiki_config.toml").read_text().startswith("# A comment.\n")


def test_removing_the_last_expression_keeps_an_empty_list(tmp_path):
    config_writer.remove_intent_cue(tmp_path, "collection", "cada", ("cada",))
    assert load_config(tmp_path).intent_section["collection"] == []
    assert load_intent(tmp_path, "es", load_config(tmp_path).intent_section).collection == ()


def test_a_duplicate_or_a_missing_expression_is_refused(tmp_path):
    with pytest.raises(VocabularyEditError) as dup:
        config_writer.add_intent_cue(tmp_path, "collection", "Cada", ("cada",))
    assert dup.value.code == "duplicate"
    with pytest.raises(VocabularyEditError) as missing:
        config_writer.remove_intent_cue(tmp_path, "collection", "nada", ("cada",))
    assert missing.value.code == "missing"
