import pytest

from domain.chat import refusal
from domain.verify import ContextPassage, FakeVerifier, verify_answer
from domain.verify.verifier import Verifier

PAGE = ContextPassage(
    "/wiki/concepts/plazo-fijo-uva.md",
    "# Plazo fijo UVA\n\n## Definición\nUn plazo fijo UVA es un depósito bancario en Argentina "
    "que se realiza en Unidades de Valor Adquisitivo.\n\n## Características clave\n"
    "Tiene un plazo mínimo de 180 días.\n",
)
ANSWER = (
    "Un plazo fijo UVA es un depósito bancario en Argentina que se realiza en Unidades de "
    "Valor Adquisitivo. El plazo mínimo es de 180 días. Rinde un cuarenta por ciento anual garantizado.\n\n"
    "Referencia: /wiki/concepts/plazo-fijo-uva.md"
)


def test_the_unsupported_sentence_is_reported_with_its_offsets():
    report = verify_answer(ANSWER, [PAGE], FakeVerifier(), threshold=0.6)
    assert (report.checked, report.supported, report.unsupported) == (3, 2, 1)
    bad = report.unsupported_sentences()[0]
    assert ANSWER[bad.start:bad.end] == "Rinde un cuarenta por ciento anual garantizado."
    first = report.sentences[0]
    assert first.supported and first.page == PAGE.page and "Definición" in first.passage
    assert report.verifier == "fake" and report.threshold == 0.6


def test_a_sentence_is_supported_when_its_best_probability_reaches_the_threshold():
    verifier = FakeVerifier(lambda passage, sentence: 0.5)
    assert verify_answer("Una oración con contenido real.", [PAGE], verifier, threshold=0.5).unsupported == 0
    assert verify_answer("Una oración con contenido real.", [PAGE], verifier, threshold=0.51).unsupported == 1


def test_the_citation_line_and_generated_texts_are_not_checked():
    table = "| opción | tasa |\n|---|---|\n| Galicia | 32,5 % |"
    answer = f"El plazo mínimo es de 180 días.\n\n{table}\n\nReferencia: x.md\nFuente: ambito.com"
    report = verify_answer(answer, [PAGE], FakeVerifier(), generated=[table])
    assert [s.text for s in report.sentences] == ["El plazo mínimo es de 180 días."]


def test_a_refusal_has_nothing_to_check():
    text = refusal.render(refusal.Refusal(refusal.RefusalCause.NOT_IN_ROSTER, suggestions=[("Plazo fijo UVA", "x")]), "es")
    assert verify_answer(text, [PAGE], FakeVerifier()).checked == 0
    assert verify_answer("Eso no está en mi base de conocimiento.", [PAGE], FakeVerifier()).checked == 0
    assert verify_answer(refusal.FIXED_SENTENCE["es"] + " Pero hay algo parecido.", [PAGE], FakeVerifier()).checked == 0


def test_questions_and_fragments_with_no_content_are_skipped():
    report = verify_answer("¿Querés saber más sobre esto? Sí. Plazo mínimo de 180 días.", [PAGE], FakeVerifier())
    assert [s.text for s in report.sentences] == ["Plazo mínimo de 180 días."]


def test_the_tier_2_warning_and_the_key_offer_line_are_skipped():
    answer = ("El plazo mínimo es de 180 días.\n\n"
              "Datos disponibles en `plazo_fijo` (banco): Galicia, Nación.\n\n"
              "> ⚠️ Esta respuesta proviene de un documento fuente sin página curada del wiki; verificá.")
    assert verify_answer(answer, [PAGE], FakeVerifier()).checked == 1


def test_an_empty_context_leaves_every_sentence_unsupported():
    report = verify_answer(ANSWER, [], FakeVerifier())
    assert report.checked == 3 and report.unsupported == 3
    assert report.sentences[0].page is None and report.sentences[0].probability == 0.0


def test_the_pairs_per_sentence_are_bounded_and_sent_in_one_batch():
    pages = [ContextPassage(f"p{i}", f"texto {i} sobre plazos y días") for i in range(20)]
    verifier = FakeVerifier()
    verify_answer("El plazo mínimo es de 180 días. Otra oración distinta aquí.", pages, verifier,
                  max_passages_per_sentence=3)
    assert len(verifier.calls) == 1 and len(verifier.calls[0]) == 6


def test_a_verifier_that_returns_the_wrong_count_is_an_error():
    class Broken:
        name, max_passage_chars = "broken", 100

        def entailment(self, pairs):
            return [0.1]

    with pytest.raises(ValueError):
        verify_answer("Una oración con contenido real. Otra oración con contenido.", [PAGE], Broken())


def test_the_fake_verifier_satisfies_the_interface():
    assert isinstance(FakeVerifier(), Verifier)



def test_availability_probes_do_not_import_the_heavy_packages():
    from domain.verify import nli

    assert isinstance(nli.is_installed(), bool)
    assert nli.model_is_cached("no-such/model-for-the-test") is False
