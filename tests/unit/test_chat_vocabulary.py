"""Tests for the shared vocabulary primitives — Piece 2 of the ingest-time vocab.

Pure functions: the padrón builder and the alias validator (normalize / dedupe /
collision detection) that the generator, the linter, and the gate all share.
"""

from domain.chat.vocabulary import (
    GENERATED_ALIASES_REL,
    Collision,
    ValidatedVocabulary,
    build_roster,
    merge_aliases,
    normalize,
    read_generated_aliases,
    validate_aliases,
    write_generated_aliases,
)


# ── normalize (reused from scope) ─────────────────────────────────────────────

def test_normalize_folds_case_accents_underscore() -> None:
    assert normalize("Banco_Nación") == "banco nacion"
    assert normalize("  BLUE ") == "blue"


# ── build_roster (the padrón) ─────────────────────────────────────────────────

def test_build_roster_unions_and_normalizes() -> None:
    roster = build_roster(["Dólar", "Plazo Fijo"], ["MEP", ""])
    assert roster == frozenset({"dolar", "plazo fijo", "mep"})


def test_build_roster_empty() -> None:
    assert build_roster() == frozenset()


# ── validate_aliases ──────────────────────────────────────────────────────────

def test_validate_keeps_clean_aliases_as_proposed() -> None:
    result = validate_aliases(
        {"Dólar": ["billete verde", "blue"]},
        roster=["dolar", "plazo fijo"],
    )
    assert result.aliases == {"Dólar": ["billete verde", "blue"]}
    assert result.collisions == ()


def test_validate_drops_blanks_and_normalized_duplicates() -> None:
    result = validate_aliases(
        {"Dólar": ["blue", "  ", "BLUE", "blue", "divisa"]},
        roster=["dolar"],
    )
    assert result.aliases == {"Dólar": ["blue", "divisa"]}


def test_validate_drops_alias_equal_to_own_canonical() -> None:
    result = validate_aliases({"Dólar": ["dólar", "blue"]}, roster=["dolar"])
    assert result.aliases == {"Dólar": ["blue"]}
    assert result.collisions == ()  # self-reference is not a collision


def test_validate_detects_collision_with_another_canonical() -> None:
    # "acciones" proposed as an alias of CEDEAR, but "acciones" is itself covered.
    result = validate_aliases(
        {"CEDEAR": ["acciones", "certificado de deposito argentino"]},
        roster=["acciones", "dolar", "cedear"],
    )
    assert result.aliases == {"CEDEAR": ["certificado de deposito argentino"]}
    assert result.collisions == (
        Collision(canonical="CEDEAR", alias="acciones", collides_with="acciones"),
    )


def test_validate_drops_canonical_when_all_aliases_removed() -> None:
    result = validate_aliases({"CEDEAR": ["acciones"]}, roster=["acciones", "cedear"])
    assert result.aliases == {}
    assert len(result.collisions) == 1


def test_validate_ignores_non_string_aliases() -> None:
    result = validate_aliases({"Dólar": ["blue", None, 42]}, roster=["dolar"])
    assert result.aliases == {"Dólar": ["blue"]}


def test_validate_empty() -> None:
    assert validate_aliases({}, roster=[]) == ValidatedVocabulary(aliases={}, collisions=())


# ── merge_aliases (generated ⊕ overrides − false_synonyms) ────────────────────

def test_merge_generated_only() -> None:
    merged = merge_aliases({"dolar": ["blue", "blue"]}, overrides={}, false_synonyms={})
    assert merged == {"dolar": ["blue"]}


def test_merge_override_adds_alias_across_key_spellings() -> None:
    # generated "Dólar" and hand-written "dolar" are the same canonical (normalized).
    merged = merge_aliases(
        {"Dólar": ["blue"]},
        overrides={"dolar": ["billete verde"]},
        false_synonyms={},
    )
    # override spelling of the key wins; aliases are unioned.
    assert merged == {"dolar": ["blue", "billete verde"]}


def test_merge_override_introduces_new_canonical() -> None:
    merged = merge_aliases(
        {"dolar": ["blue"]},
        overrides={"caucion": ["caución bursátil"]},
        false_synonyms={},
    )
    assert merged == {"dolar": ["blue"], "caucion": ["caución bursátil"]}


def test_merge_false_synonyms_removes_alias() -> None:
    merged = merge_aliases(
        {"cedear": ["acciones", "certificado"]},
        overrides={},
        false_synonyms={"cedear": ["accion", "acciones"]},
    )
    assert merged == {"cedear": ["certificado"]}


def test_merge_false_synonyms_can_empty_a_canonical() -> None:
    merged = merge_aliases(
        {"cedear": ["acciones"]},
        overrides={},
        false_synonyms={"CEDEAR": ["Acciones"]},  # matched case/accent-insensitively
    )
    assert merged == {}


# ── generated artifact read/write ─────────────────────────────────────────────

def test_write_then_read_roundtrip(tmp_path) -> None:
    alias_map = {
        "CEDEAR": ["Certificado de Depósito Argentino"],
        "dolar": ["blue", 'con "comillas"'],
    }
    write_generated_aliases(tmp_path, alias_map)
    assert (tmp_path / GENERATED_ALIASES_REL).exists()
    assert read_generated_aliases(tmp_path) == alias_map


def test_read_absent_returns_empty(tmp_path) -> None:
    assert read_generated_aliases(tmp_path) == {}


def test_read_malformed_returns_empty(tmp_path) -> None:
    path = tmp_path / GENERATED_ALIASES_REL
    path.parent.mkdir(parents=True)
    path.write_text("this is = = not valid", encoding="utf-8")
    assert read_generated_aliases(tmp_path) == {}
