"""Tests for the ingest-time alias artifact orchestration (Piece 3 wiring)."""

from domain.chat.vocabulary import read_generated_aliases
from domain.ingestion.alias_generation import update_generated_aliases


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
