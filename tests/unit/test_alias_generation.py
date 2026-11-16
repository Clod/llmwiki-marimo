"""Tests for the ingest-time alias artifact orchestration (Piece 3 + Piece 4)."""

from domain.chat.vocabulary import read_generated_aliases, write_generated_aliases
from domain.ingestion import alias_generation
from domain.ingestion.alias_generation import (
    regenerate_dataset_aliases,
    update_generated_aliases,
)


def _fake_propose(mapping):
    """A `propose` stand-in that records the terms it was asked about."""
    calls = []

    def propose(terms):
        calls.append(list(terms))
        return mapping

    propose.calls = calls
    return propose


def _set_vocab(monkeypatch, vocab):
    monkeypatch.setattr(alias_generation, "dataset_vocabulary", lambda ws: set(vocab))


def test_update_writes_the_map_and_names_the_new_aliases(tmp_path) -> None:
    # "acciones" is also a covered name: the alias is kept, and named as new for a
    # curator to review, like every other new alias.
    write_generated_aliases(tmp_path, {"Dólar": ["blue"]})
    result = update_generated_aliases(
        tmp_path, new_concepts=[("CEDEAR", ["acciones", "Certificado"]), ("Dólar", ["blue"])],
    )
    assert result.aliases == {"Dólar": ["blue"], "CEDEAR": ["acciones", "Certificado"]}
    assert result.new == (("CEDEAR", "acciones"), ("CEDEAR", "Certificado"))
    assert read_generated_aliases(tmp_path) == result.aliases


def test_update_accumulates_across_calls(tmp_path) -> None:
    update_generated_aliases(tmp_path, [("Dólar", ["blue"])])
    update_generated_aliases(tmp_path, [("Caución", ["caución bursátil"])])
    assert read_generated_aliases(tmp_path) == {
        "Dólar": ["blue"],
        "Caución": ["caución bursátil"],
    }


def test_update_with_no_datasets_dir_is_fine(tmp_path) -> None:
    result = update_generated_aliases(tmp_path, [("Plazo Fijo", ["PF"])])
    assert result.aliases == {"Plazo Fijo": ["PF"]}


# ── Piece 4: dataset-alias regeneration ───────────────────────────────────────

def test_regenerate_writes_dataset_aliases(tmp_path, monkeypatch) -> None:
    _set_vocab(monkeypatch, {"dólar", "plazo fijo"})
    propose = _fake_propose({"dólar": ["billete verde"], "plazo fijo": ["pf"]})
    result = regenerate_dataset_aliases(tmp_path, propose)
    assert result.aliases == {"dólar": ["billete verde"], "plazo fijo": ["pf"]}
    assert read_generated_aliases(tmp_path) == {
        "dólar": ["billete verde"], "plazo fijo": ["pf"],
    }
    assert propose.calls == [["dólar", "plazo fijo"]]  # sorted terms, called once


def test_regenerate_preserves_concept_entries(tmp_path, monkeypatch) -> None:
    # A concept alias already in the artifact must survive the dataset pass.
    write_generated_aliases(tmp_path, {"Caución": ["caución bursátil"]})
    _set_vocab(monkeypatch, {"dólar"})
    propose = _fake_propose({"dólar": ["billete verde"]})
    regenerate_dataset_aliases(tmp_path, propose)
    assert read_generated_aliases(tmp_path) == {
        "Caución": ["caución bursátil"], "dólar": ["billete verde"],
    }


def test_regenerate_names_the_new_dataset_aliases(tmp_path, monkeypatch) -> None:
    _set_vocab(monkeypatch, {"dólar"})
    propose = _fake_propose({"dólar": ["billete verde", "acciones"]})
    result = regenerate_dataset_aliases(tmp_path, propose)
    assert result.aliases == {"dólar": ["billete verde", "acciones"]}
    assert result.new == (("dólar", "billete verde"), ("dólar", "acciones"))


def test_regenerate_ignores_off_vocab_proposals(tmp_path, monkeypatch) -> None:
    # The LLM invents a term not in the dataset vocab → it is not persisted.
    _set_vocab(monkeypatch, {"dólar"})
    propose = _fake_propose({"dólar": ["billete verde"], "bitcoin": ["btc"]})
    result = regenerate_dataset_aliases(tmp_path, propose)
    assert result.aliases == {"dólar": ["billete verde"]}


def test_regenerate_gate_skips_when_vocab_unchanged(tmp_path, monkeypatch) -> None:
    _set_vocab(monkeypatch, {"dólar"})
    propose = _fake_propose({"dólar": ["billete verde"]})
    regenerate_dataset_aliases(tmp_path, propose)
    second = regenerate_dataset_aliases(tmp_path, propose)
    assert second is None                 # no-op on an unchanged vocabulary
    assert len(propose.calls) == 1        # the LLM was not called again


def test_regenerate_force_reruns_despite_gate(tmp_path, monkeypatch) -> None:
    _set_vocab(monkeypatch, {"dólar"})
    propose = _fake_propose({"dólar": ["billete verde"]})
    regenerate_dataset_aliases(tmp_path, propose)
    result = regenerate_dataset_aliases(tmp_path, propose, force=True)
    assert result is not None
    assert len(propose.calls) == 2


def test_regenerate_reruns_when_vocab_changes(tmp_path, monkeypatch) -> None:
    _set_vocab(monkeypatch, {"dólar"})
    propose = _fake_propose({"dólar": ["billete verde"]})
    regenerate_dataset_aliases(tmp_path, propose)
    _set_vocab(monkeypatch, {"dólar", "plazo fijo"})  # a new data term appears
    propose2 = _fake_propose({"dólar": ["billete verde"], "plazo fijo": ["pf"]})
    result = regenerate_dataset_aliases(tmp_path, propose2)
    assert result is not None
    assert propose2.calls == [["dólar", "plazo fijo"]]


def test_regenerate_empty_vocab_is_noop(tmp_path, monkeypatch) -> None:
    _set_vocab(monkeypatch, set())
    propose = _fake_propose({"x": ["y"]})
    assert regenerate_dataset_aliases(tmp_path, propose) is None
    assert propose.calls == []
    assert read_generated_aliases(tmp_path) == {}


# ── Rejected aliases (wiki_config.toml [falsos_sinonimos]) ───────────────────

def _reject(workspace, canonical, alias) -> None:
    from domain.chat.config_writer import reject_alias
    reject_alias(workspace, canonical, alias)


def test_update_does_not_write_back_an_alias_the_owner_rejected(tmp_path) -> None:
    write_generated_aliases(tmp_path, {"Plazo fijo UVA": ["UVA", "PF UVA"]})
    _reject(tmp_path, "Plazo fijo UVA", "UVA")
    # A later ingestion proposes "UVA" again for the same concept.
    result = update_generated_aliases(
        tmp_path,
        new_concepts=[("Plazo fijo UVA", ["UVA", "Plazo fijo en UVA"])],
    )
    assert result.aliases == {"Plazo fijo UVA": ["PF UVA", "Plazo fijo en UVA"]}
    assert read_generated_aliases(tmp_path) == {"Plazo fijo UVA": ["PF UVA", "Plazo fijo en UVA"]}


def test_a_rejected_alias_of_one_canonical_stays_for_another(tmp_path) -> None:
    _reject(tmp_path, "Plazo fijo UVA", "uva")
    update_generated_aliases(
        tmp_path,
        new_concepts=[("Plazo fijo UVA", ["UVA"]), ("Unidad de Valor Adquisitivo", ["UVA"])],
    )
    assert read_generated_aliases(tmp_path) == {"Unidad de Valor Adquisitivo": ["UVA"]}


def test_regenerate_does_not_write_back_a_rejected_dataset_alias(tmp_path, monkeypatch) -> None:
    _set_vocab(monkeypatch, {"dolar"})
    _reject(tmp_path, "dolar", "verde")
    regenerate_dataset_aliases(tmp_path, _fake_propose({"dolar": ["verde", "divisa"]}))
    assert read_generated_aliases(tmp_path) == {"dolar": ["divisa"]}


def test_the_ingestion_names_each_new_alias_for_review() -> None:
    from domain.ingestion.pipeline import _report_new_aliases

    lines: list[str] = []
    _report_new_aliases(lines.append, [("Plazo fijo UVA", "UVA"), ("Cenicienta", "Culocenizón")])
    assert lines == ["⚠️ New aliases to review in the vocabulary screen: "
                     "«UVA» → «Plazo fijo UVA»; «Culocenizón» → «Cenicienta»"]
    lines.clear()
    _report_new_aliases(lines.append, [])
    assert lines == []
