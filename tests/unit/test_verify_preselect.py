from domain.verify import ContextPassage, FakeVerifier, verify_answer
from domain.verify.preselect import select_candidates

PASSAGES = [
    "El plazo fijo UVA tiene un plazo mínimo de 180 días.",
    "El dólar MEP se compra con bonos en pesos.",
    "Los CEDEARs representan acciones extranjeras.",
    "La tasa de política monetaria la fija el Banco Central.",
]


def test_every_passage_is_kept_when_there_are_k_or_fewer():
    assert select_candidates(PASSAGES, ["algo"], 4) == [[0, 1, 2, 3]]
    assert select_candidates(PASSAGES, ["algo", "otra"], 9) == [[0, 1, 2, 3]] * 2


def test_the_best_ranked_passage_comes_first():
    chosen = select_candidates(PASSAGES, ["El plazo mínimo es de 180 días."], 2)
    assert chosen[0][0] == 0
    assert len(chosen[0]) <= 2


def test_stemming_matches_inflected_words():
    chosen = select_candidates(PASSAGES, ["Las acciones extranjeras"], 1, language="es")
    assert chosen == [[2]]


def test_a_sentence_with_no_common_word_gets_no_candidate():
    assert select_candidates(PASSAGES, ["Zzz qqq xxx"], 2) == [[]]


def test_k_must_be_positive():
    import pytest
    with pytest.raises(ValueError):
        select_candidates(PASSAGES, ["x"], 0)


def test_verify_answer_with_candidates_sends_fewer_pairs_and_reports_both_counts():
    context = [ContextPassage("/wiki/concepts/x.md", "\n\n".join(
        f"## Tema {i}\n\nTexto sobre el asunto numero{i} con contenido propio{i}." for i in range(12)))]
    # one section per passage is not guaranteed; force small passages
    verifier = FakeVerifier(max_passage_chars=80)
    answer = "Hay contenido propio3 sobre el asunto numero3. Otra cosa distinta sobre asunto numero7."
    report = verify_answer(answer, context, verifier, candidates=2)
    assert report.pairs == sum(len(c) for c in verifier.calls)
    assert 0 < report.pairs <= 2 * report.checked
    assert report.pairs < report.pairs_possible
