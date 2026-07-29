"""Tests for domain/chat/scope.py — the deterministic scope gate.

Pure, config-driven word matching used by the hybrid pre-retrieval to decide,
WITHOUT the LLM, whether a question is off-corpus, mentions data we actually
have, and to filter a model's synonym proposals against known false synonyms.
Matching is accent-insensitive, case-insensitive, underscore=space, and by whole
word (so short keys like "cer"/"mep" don't match inside other words).
"""

from domain.chat.scope import (
    advisory_intent,
    drop_false_synonyms,
    is_off_limits,
    mentions_known_data,
)


# ── is_off_limits (blacklist, checked at the top of the flow) ────────────────

OFF_LIMITS = {"cedear", "cedears", "cripto", "bitcoin"}


def test_off_limits_hit():
    assert is_off_limits("¿Qué son los CEDEARs y conviene comprarlos?", OFF_LIMITS) is True


def test_off_limits_accent_and_case_insensitive():
    assert is_off_limits("¿Qué es el BITCOIN?", OFF_LIMITS) is True


def test_off_limits_miss():
    assert is_off_limits("¿A cuánto está el dólar MEP?", OFF_LIMITS) is False


# ── mentions_known_data (the "chequeo contra la lista de datos") ─────────────

VOCAB = {"dolar", "mep", "ccl", "plazo fijo", "banco nacion", "caucion",
         "fci money market", "acciones", "cer"}
ALIASES = {"billete verde", "blue"}


def test_matches_category_and_key():
    # accents (dólar→dolar) and a key (MEP) both match
    assert mentions_known_data("¿A cuánto está el dólar MEP?", VOCAB) is True


def test_matches_category_even_if_specific_key_absent():
    # "plazo fijo" is in the vocab; the missing bank (Comafi) is handled by the
    # tool downstream — the gate only needs to see we DO have this category.
    assert mentions_known_data("¿Qué tasa de plazo fijo ofrece el Banco Comafi?", VOCAB) is True


def test_no_match_off_corpus():
    assert mentions_known_data("¿Cuál es la capital de Francia?", VOCAB) is False


def test_matches_via_alias():
    assert mentions_known_data("¿a cuánto está el billete verde?", VOCAB, ALIASES) is True


def test_whole_word_only_no_substring_false_positive():
    # "cer" (bonos CER) must NOT match inside "cerca"
    assert mentions_known_data("hablemos de algo cercano", {"cer"}) is False


def test_underscore_equals_space():
    # a category slug "plazo_fijo" matches the natural phrase "plazo fijo"
    assert mentions_known_data("me interesa el plazo fijo", {"plazo_fijo"}) is True


# ── advisory_intent (route a generic advisory question to the tools) ─────────


def test_advisory_intent_amount_and_horizon_and_cue():
    # The Case-G question: names no instrument, but is clearly an advisory ask.
    q = "Tengo $1.000.000 que no necesito por 3 meses, ¿qué alternativas tengo y cuánto ganaría?"
    assert advisory_intent(q) is True


def test_advisory_intent_amount_in_words():
    assert advisory_intent("¿Dónde invierto 500 mil pesos por 6 meses?") is True


def test_advisory_intent_needs_a_cue():
    # A number/amount alone is not advisory intent.
    assert advisory_intent("Tengo $1.000.000 en la cuenta") is False


def test_advisory_intent_needs_amount_or_horizon():
    # An advisory cue with no amount/horizon does not trigger the (amount-hungry) tool.
    assert advisory_intent("¿En qué me conviene invertir?") is False


def test_advisory_intent_off_topic_number_is_false():
    assert advisory_intent("¿Cuántos días tiene febrero de 2024?") is False


# ── drop_false_synonyms (filter the model's proposals) ──────────────────────

FALSE_SYN = {"cedears": ["accion", "acciones"]}


def test_drops_forbidden_synonym():
    proposals = ["accion", "certificado de deposito"]
    kept = drop_false_synonyms("¿conviene comprar cedears?", proposals, FALSE_SYN)
    assert kept == ["certificado de deposito"]  # "accion" dropped (CEDEAR ≠ acción)


def test_keeps_proposals_when_no_false_synonym_applies():
    proposals = ["divisa", "billete verde"]
    assert drop_false_synonyms("¿a cuánto el dólar?", proposals, FALSE_SYN) == proposals
