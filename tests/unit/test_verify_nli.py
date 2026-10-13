import pytest

from domain.verify import nli

pytestmark = pytest.mark.skipif(
    not nli.is_available(), reason="transformers or the NLI model is not in the local cache",
)


def test_the_local_model_separates_a_supported_sentence_from_an_invented_one():
    verifier = nli.LocalNliVerifier()
    passage = "## Características clave\nTiene un plazo mínimo de 180 días."
    supported, invented = verifier.entailment([
        (passage, "El plazo mínimo es de 180 días."),
        (passage, "Rinde un 40 % anual."),
    ])
    assert supported > 0.5 > invented


def test_an_empty_batch_returns_an_empty_list():
    assert nli.LocalNliVerifier().entailment([]) == []
