"""services.vocabulary on a copy of the finance example."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from domain.chat.config import load_config
from domain.chat.scope import is_off_limits
from services import vocabulary as vocab
from services.wiki import open_wiki

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "finanzas-argentinas"


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    monkeypatch.delenv("WIKI_AUTOCOMMIT", raising=False)
    folder = tmp_path / "finanzas"
    shutil.copytree(EXAMPLE, folder, ignore=shutil.ignore_patterns("cache", "traces"))
    return open_wiki(folder)


def last_commit(wiki) -> tuple[str, list[str]]:
    message = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=wiki.path, capture_output=True, text=True).stdout
    files = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=wiki.path,
                           capture_output=True, text=True).stdout.split()
    return message.strip(), files


def test_the_roster_holds_the_datasets_and_the_concept_pages_with_their_origin(wiki):
    entries = {e.name: e for e in vocab.read_vocabulary(wiki).roster}
    assert entries["Plazo fijo UVA"].origin == "concept" and entries["Plazo fijo UVA"].page == "concepts/plazo-fijo-uva"
    assert {e.origin for e in entries.values()} == {"category", "key", "concept"}
    names = [e.name for e in vocab.read_vocabulary(wiki).roster]
    assert len(names) == len({n.casefold() for n in names})


def test_the_aliases_carry_their_origin(wiki):
    by_canonical = {c.canonical: {a.alias: a.origin for a in c.aliases} for c in vocab.read_vocabulary(wiki).aliases}
    assert by_canonical["dolar"] == {"billete verde": "hand", "divisa": "hand"}
    assert by_canonical["Plazo fijo UVA"] == {"UVA": "generated"}


def test_the_lists_and_the_findings(wiki):
    v = vocab.read_vocabulary(wiki)
    assert v.blacklist == ("cedear", "cedears", "cripto", "bitcoin")
    assert v.rejected == (("cedear", "accion"), ("cedear", "acciones"))
    ambiguous = [f for f in v.findings if f.check == "vocab_ambiguous"]
    assert any(f.terms[0] == "uva" for f in ambiguous)


def test_a_blacklist_term_is_committed_and_refuses_the_next_question(wiki):
    vocab.add_blacklist_term(wiki, "futuros")
    assert is_off_limits("¿Conviene operar futuros?", load_config(wiki.path).off_limits)
    message, files = last_commit(wiki)
    assert message == 'vocabulary: blacklist "futuros"' and "wiki_config.toml" in files


def test_a_covered_name_cannot_go_to_the_blacklist(wiki):
    with pytest.raises(vocab.VocabularyEditError) as refused:
        vocab.add_blacklist_term(wiki, "plazo fijo uva")
    assert refused.value.code == "covered"
    with pytest.raises(vocab.VocabularyEditError):
        vocab.add_blacklist_term(wiki, "Acciones")      # a dataset category of the example


def test_a_hand_alias_needs_a_covered_canonical_and_must_not_name_another_one(wiki):
    with pytest.raises(vocab.VocabularyEditError) as refused:
        vocab.add_alias(wiki, "Bitcoin", "BTC")
    assert refused.value.code == "unknown_canonical"
    with pytest.raises(vocab.VocabularyEditError) as refused:
        vocab.add_alias(wiki, "Plazo fijo UVA", "Plazo fijo tradicional")
    assert refused.value.code == "collision"
    vocab.add_alias(wiki, "Plazo fijo UVA", "PF UVA")
    assert "PF UVA" in load_config(wiki.path).data_aliases["Plazo fijo UVA"]


def test_rejecting_a_generated_alias_removes_it_for_good(wiki):
    vocab.reject_alias(wiki, "Plazo fijo UVA", "UVA")
    v = vocab.read_vocabulary(wiki)
    assert ("Plazo fijo UVA", "UVA") in v.rejected
    assert "Plazo fijo UVA" not in {c.canonical for c in v.aliases}
    assert last_commit(wiki)[0] == 'vocabulary: reject alias "UVA" of "Plazo fijo UVA"'
    vocab.unreject_alias(wiki, "Plazo fijo UVA", "UVA")
    assert "Plazo fijo UVA" in {c.canonical for c in vocab.read_vocabulary(wiki).aliases}


def test_rejecting_a_hand_alias_also_removes_the_hand_copy(wiki):
    vocab.reject_alias(wiki, "dolar", "divisa")
    by_canonical = {c.canonical: [a.alias for a in c.aliases] for c in vocab.read_vocabulary(wiki).aliases}
    assert by_canonical["dolar"] == ["billete verde"]
    assert "divisa" not in (wiki.path / "wiki_config.toml").read_text().split("[falsos_sinonimos]")[0]


def test_a_key_opens_the_whole_dataset_of_its_category_with_its_row_marked(wiki):
    (table,) = vocab.dataset_tables(wiki, key="banco nacion")
    assert table.category == "plazo_fijo" and table.marked == "Banco Nación"
    assert table.path == "datasets/plazo_fijo.md"
    assert table.metrics == ("TNA",) and table.unit == "%" and table.source == "bcra.gob.ar"
    assert table.columns == ("30d", "60d", "90d", "180d", "365d")
    assert len(table.rows) == 8 and dict(table.rows)["Banco Nación"] == ("33", "34", "35", "36", "37")


def test_a_dataset_with_two_metrics_names_its_columns_by_metric(wiki):
    (table,) = vocab.dataset_tables(wiki, category="dolar")
    assert table.columns == ("compra", "venta") and table.marked == "" and len(table.rows) == 5


def test_an_unknown_key_or_category_has_no_table(wiki):
    assert vocab.dataset_tables(wiki, key="Banco Inexistente") == ()
    assert vocab.dataset_tables(wiki, category="../wiki_config") == ()
