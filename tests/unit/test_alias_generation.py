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


def test_update_writes_validated_map_and_drops_collision(tmp_path) -> None:
    # "acciones" is a covered concept, so proposing it as an alias of CEDEAR is a
    # collision → dropped; "Certificado" survives and is persisted.
    result = update_generated_aliases(
        tmp_path,
        concept_names=["Acciones", "CEDEAR"],
        new_concepts=[("CEDEAR", ["acciones", "Certificado"])],
    )
    assert result.aliases == {"CEDEAR": ["Certificado"]}
    assert len(result.collisions) == 1
    assert read_generated_aliases(tmp_path) == {"CEDEAR": ["Certificado"]}


def test_update_accumulates_across_calls(tmp_path) -> None:
    update_generated_aliases(tmp_path, ["Dólar"], [("Dólar", ["blue"])])
    update_generated_aliases(
        tmp_path, ["Dólar", "Caución"], [("Caución", ["caución bursátil"])]
    )
    assert read_generated_aliases(tmp_path) == {
        "Dólar": ["blue"],
        "Caución": ["caución bursátil"],
    }


def test_update_with_no_datasets_dir_is_fine(tmp_path) -> None:
    # No datasets/ folder → dataset vocab is empty; the roster falls back to names.
    result = update_generated_aliases(tmp_path, ["Plazo Fijo"], [("Plazo Fijo", ["PF"])])
    assert result.aliases == {"Plazo Fijo": ["PF"]}


# ── Piece 4: dataset-alias regeneration ───────────────────────────────────────

def test_regenerate_writes_dataset_aliases(tmp_path, monkeypatch) -> None:
    _set_vocab(monkeypatch, {"dólar", "plazo fijo"})
    propose = _fake_propose({"dólar": ["billete verde"], "plazo fijo": ["pf"]})
    result = regenerate_dataset_aliases(tmp_path, [], propose)
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
    regenerate_dataset_aliases(tmp_path, ["Caución"], propose)
    assert read_generated_aliases(tmp_path) == {
        "Caución": ["caución bursátil"], "dólar": ["billete verde"],
    }


def test_regenerate_drops_collision_with_concept(tmp_path, monkeypatch) -> None:
    # "acciones" is a covered concept → proposing it as an alias of dólar collides.
    _set_vocab(monkeypatch, {"dólar"})
    propose = _fake_propose({"dólar": ["billete verde", "acciones"]})
    result = regenerate_dataset_aliases(tmp_path, ["Acciones"], propose)
    assert result.aliases == {"dólar": ["billete verde"]}
    assert len(result.collisions) == 1


def test_regenerate_ignores_off_vocab_proposals(tmp_path, monkeypatch) -> None:
    # The LLM invents a term not in the dataset vocab → it is not persisted.
    _set_vocab(monkeypatch, {"dólar"})
    propose = _fake_propose({"dólar": ["billete verde"], "bitcoin": ["btc"]})
    result = regenerate_dataset_aliases(tmp_path, [], propose)
    assert result.aliases == {"dólar": ["billete verde"]}


def test_regenerate_gate_skips_when_vocab_unchanged(tmp_path, monkeypatch) -> None:
    _set_vocab(monkeypatch, {"dólar"})
    propose = _fake_propose({"dólar": ["billete verde"]})
    regenerate_dataset_aliases(tmp_path, [], propose)
    second = regenerate_dataset_aliases(tmp_path, [], propose)
    assert second is None                 # no-op on an unchanged vocabulary
    assert len(propose.calls) == 1        # the LLM was not called again


def test_regenerate_force_reruns_despite_gate(tmp_path, monkeypatch) -> None:
    _set_vocab(monkeypatch, {"dólar"})
    propose = _fake_propose({"dólar": ["billete verde"]})
    regenerate_dataset_aliases(tmp_path, [], propose)
    result = regenerate_dataset_aliases(tmp_path, [], propose, force=True)
    assert result is not None
    assert len(propose.calls) == 2


def test_regenerate_reruns_when_vocab_changes(tmp_path, monkeypatch) -> None:
    _set_vocab(monkeypatch, {"dólar"})
    propose = _fake_propose({"dólar": ["billete verde"]})
    regenerate_dataset_aliases(tmp_path, [], propose)
    _set_vocab(monkeypatch, {"dólar", "plazo fijo"})  # a new data term appears
    propose2 = _fake_propose({"dólar": ["billete verde"], "plazo fijo": ["pf"]})
    result = regenerate_dataset_aliases(tmp_path, [], propose2)
    assert result is not None
    assert propose2.calls == [["dólar", "plazo fijo"]]


def test_regenerate_empty_vocab_is_noop(tmp_path, monkeypatch) -> None:
    _set_vocab(monkeypatch, set())
    propose = _fake_propose({"x": ["y"]})
    assert regenerate_dataset_aliases(tmp_path, [], propose) is None
    assert propose.calls == []
    assert read_generated_aliases(tmp_path) == {}
