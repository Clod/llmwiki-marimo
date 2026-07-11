"""Tests for domain/chat/overlap.py — lexical answer-vs-source verification.

Used on Tier 2 (raw source docs): confirm the answer actually draws on the
retrieved chunk instead of using it as a fig leaf to leak general knowledge.
`coverage` = fraction of the answer's content words that appear in the source;
`is_supported` compares it to a LENIENT threshold (reject only the clearly
off-source, to avoid false-rejecting faithful paraphrases). Deterministic,
no LLM.
"""

from domain.chat.overlap import coverage, is_supported


# A faithful answer that reuses the source's vocabulary (the caución page).
CAUCION_SOURCE = (
    "# Cauciones Bursátiles\n"
    "Una caución es una operación de muy corto plazo garantizada por aforo en "
    "BYMA, con riesgo de crédito prácticamente nulo."
)
CAUCION_ANSWER = (
    "Una caución bursátil es una operación de corto plazo con garantías de aforo, "
    "sin riesgo de crédito individual."
)

# The CEDEARs leak: a general-knowledge answer, grounded only on a tangential
# chunk from a BONDS document that merely name-drops "CEDEARs".
BONDS_CHUNK = (
    "Moneda de exposición al riesgo. Exposición al dólar oficial mayorista, no al "
    "dólar CCL o MEP. No es lo mismo que tener dólares billete ni invertir en CEDEARs."
)
CEDEARS_LEAK = (
    "Los CEDEARs son certificados de depósito argentinos que permiten invertir en "
    "acciones extranjeras como Apple, Google y Amazon, ofreciendo diversificación "
    "internacional y cobertura cambiaria frente a la devaluación del peso."
)


def test_supported_when_answer_reuses_source():
    assert coverage(CAUCION_ANSWER, CAUCION_SOURCE) > 0.5
    assert is_supported(CAUCION_ANSWER, CAUCION_SOURCE) is True


def test_not_supported_when_answer_is_off_source():
    # The leak barely overlaps the bonds chunk -> flagged as unsupported.
    assert coverage(CEDEARS_LEAK, BONDS_CHUNK) < 0.25
    assert is_supported(CEDEARS_LEAK, BONDS_CHUNK) is False


def test_numbers_count_as_content():
    assert is_supported("El dólar MEP está a 1180.", "| MEP | compra | 1180 | ambito |") is True


def test_empty_answer_is_supported():
    # Nothing to support -> don't reject via overlap (handled elsewhere).
    assert coverage("", CAUCION_SOURCE) == 1.0
    assert is_supported("", CAUCION_SOURCE) is True


def test_empty_source_is_not_supported():
    assert is_supported("una respuesta con contenido real y sustantivo", "") is False


def test_threshold_is_tunable():
    # With a strict threshold even the faithful answer can fail; with a lenient
    # one it passes — the caller controls how aggressive verification is.
    cov = coverage(CAUCION_ANSWER, CAUCION_SOURCE)
    assert is_supported(CAUCION_ANSWER, CAUCION_SOURCE, min_coverage=cov + 0.01) is False
    assert is_supported(CAUCION_ANSWER, CAUCION_SOURCE, min_coverage=cov - 0.01) is True
