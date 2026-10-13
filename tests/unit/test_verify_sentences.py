from domain.verify.sentences import split_sentences


def texts(answer):
    sentences = split_sentences(answer)
    for s in sentences:
        assert answer[s.start:s.end] == s.text
    return [s.text for s in sentences]


def test_splits_spanish_and_english_sentences():
    assert texts("Primera oración. Segunda oración! ¿Tercera? Cuarta.") == [
        "Primera oración.", "Segunda oración!", "¿Tercera?", "Cuarta."]
    assert texts("It rises. It falls. Then it rises again.") == [
        "It rises.", "It falls.", "Then it rises again."]


def test_abbreviations_do_not_end_a_sentence():
    assert texts("Banco Galicia S.A. ofrece plazo fijo. El Dr. Pérez opina distinto.") == [
        "Banco Galicia S.A. ofrece plazo fijo.", "El Dr. Pérez opina distinto."]
    assert texts("Hay varios, p. ej. el plazo fijo. Otro es la caución.") == [
        "Hay varios, p. ej. el plazo fijo.", "Otro es la caución."]


def test_numbers_with_separators_stay_whole():
    assert texts("Vale 1.187,5 pesos. Rinde 32,5 % anual.") == [
        "Vale 1.187,5 pesos.", "Rinde 32,5 % anual."]
    assert texts("Subió en 2024. Luego bajó.") == ["Subió en 2024.", "Luego bajó."]


def test_each_list_item_is_split_after_its_marker():
    answer = "- Primer ítem sin punto\n- Segundo ítem. Con dos oraciones.\n1. Tercero\n"
    assert texts(answer) == ["Primer ítem sin punto", "Segundo ítem.", "Con dos oraciones.", "Tercero"]


def test_a_table_row_is_one_sentence_and_the_separator_is_dropped():
    answer = "| Banco | Tasa |\n|---|---|\n| Galicia | 32,5 % |\n"
    assert texts(answer) == ["| Banco | Tasa |", "| Galicia | 32,5 % |"]


def test_headings_and_code_fences_are_dropped():
    assert texts("# Título\n\nTexto del cuerpo.\n```\ncodigo. no cuenta.\n```\n") == ["Texto del cuerpo."]


def test_a_wrapped_paragraph_is_one_sentence():
    assert texts("Una oración que sigue\nen la línea siguiente. Otra.") == [
        "Una oración que sigue\nen la línea siguiente.", "Otra."]


def test_an_address_or_url_is_not_split():
    assert texts("Ver ambito.com para más datos. Fin.") == ["Ver ambito.com para más datos.", "Fin."]


def test_empty_text_has_no_sentences():
    assert split_sentences("") == []
    assert split_sentences("\n\n") == []
