"""The vocabulary edits of wiki_config.toml keep the file as written."""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

import pytest

from domain.chat import config_writer as cw
from domain.chat.config import load_config

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "finanzas-argentinas" / "wiki_config.toml"


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    shutil.copy(EXAMPLE, tmp_path / "wiki_config.toml")
    return tmp_path


def text(wiki: Path) -> str:
    return (wiki / "wiki_config.toml").read_text(encoding="utf-8")


def data(wiki: Path) -> dict:
    return tomllib.loads(text(wiki))


def comments(wiki: Path) -> list[str]:
    return [line for line in text(wiki).splitlines() if line.lstrip().startswith("#")]


def test_adding_a_blacklist_term_keeps_every_comment_and_the_rest_of_the_file(wiki):
    before_comments, before = comments(wiki), text(wiki)
    assert cw.add_blacklist_term(wiki, "  acciones ") == "acciones"
    assert data(wiki)["fuera_de_alcance"]["terminos"] == ["cedear", "cedears", "cripto", "bitcoin", "acciones"]
    assert comments(wiki) == before_comments
    changed = [a for a, b in zip(before.splitlines(), text(wiki).splitlines()) if a != b]
    assert len(changed) == 1 and changed[0].startswith("terminos = ")


def test_a_blacklist_term_is_compared_without_case_nor_accents(wiki):
    with pytest.raises(cw.VocabularyEditError) as refused:
        cw.add_blacklist_term(wiki, "CRÍPTO")
    assert refused.value.code == "duplicate"


@pytest.mark.parametrize("term", ["", "   ", "_"])
def test_an_empty_term_is_refused_and_nothing_is_written(wiki, term):
    before = text(wiki)
    with pytest.raises(cw.VocabularyEditError) as refused:
        cw.add_blacklist_term(wiki, term)
    assert refused.value.code == "empty" and text(wiki) == before


def test_removing_a_blacklist_term(wiki):
    cw.remove_blacklist_term(wiki, "Bitcoin")
    assert data(wiki)["fuera_de_alcance"]["terminos"] == ["cedear", "cedears", "cripto"]
    with pytest.raises(cw.VocabularyEditError) as refused:
        cw.remove_blacklist_term(wiki, "bitcoin")
    assert refused.value.code == "missing"


def test_a_hand_written_alias_joins_the_existing_canonical_in_its_own_spelling(wiki):
    cw.add_alias(wiki, "Dólar", "verdes")
    assert data(wiki)["alias_datos"] == {"dolar": ["billete verde", "divisa", "verdes"]}
    cw.add_alias(wiki, "Plazo fijo UVA", "PF UVA")
    assert data(wiki)["alias_datos"]["Plazo fijo UVA"] == ["PF UVA"]
    with pytest.raises(cw.VocabularyEditError):
        cw.add_alias(wiki, "dolar", "Divisa")


def test_removing_the_last_alias_of_a_canonical_removes_the_canonical(wiki):
    cw.remove_alias(wiki, "dolar", "divisa")
    cw.remove_alias(wiki, "DOLAR", "billete verde")
    assert "dolar" not in data(wiki).get("alias_datos", {})


def test_rejecting_a_generated_alias_writes_a_false_synonym_and_drops_it_from_the_effective_map(wiki):
    (wiki / ".llmwiki").mkdir()
    shutil.copy(EXAMPLE.parent / ".llmwiki" / "aliases.generated.toml", wiki / ".llmwiki")
    assert "UVA" in load_config(wiki).data_aliases["Plazo fijo UVA"]
    cw.reject_alias(wiki, "Plazo fijo UVA", "UVA")
    assert data(wiki)["falsos_sinonimos"]["Plazo fijo UVA"] == ["UVA"]
    assert "Plazo fijo UVA" not in load_config(wiki).data_aliases       # its only alias is gone
    assert "UVA" in load_config(wiki).data_aliases["Unidad de Valor Adquisitivo"]
    cw.unreject_alias(wiki, "plazo fijo uva", "uva")
    assert "Plazo fijo UVA" not in data(wiki)["falsos_sinonimos"]
    assert "UVA" in load_config(wiki).data_aliases["Plazo fijo UVA"]


def test_a_missing_file_and_missing_sections_are_created(tmp_path):
    cw.add_blacklist_term(tmp_path, "cripto")
    cw.reject_alias(tmp_path, "CEDEAR", "acciones")
    assert data(tmp_path) == {"fuera_de_alcance": {"terminos": ["cripto"]},
                              "falsos_sinonimos": {"CEDEAR": ["acciones"]}}
    assert load_config(tmp_path).off_limits == ["cripto"]
