from domain.verify.models import ContextPassage
from domain.verify.passages import split_context, split_passages


def test_a_page_is_split_at_its_headings_and_each_passage_names_its_section():
    page = ContextPassage("/wiki/concepts/x.md", "---\ntitle: X\n---\n# X\n\n## Definición\nUn plazo fijo es un depósito.\n\n## Características\nPlazo mínimo de 180 días.\n")
    passages = split_passages(page, 400)
    assert [p.text for p in passages] == [
        "## Definición\nUn plazo fijo es un depósito.", "## Características\nPlazo mínimo de 180 días."]
    assert {p.page for p in passages} == {"/wiki/concepts/x.md"}


def test_paragraphs_are_packed_up_to_the_limit():
    body = "\n\n".join(f"Párrafo número {i} con algo de texto." for i in range(6))
    passages = split_passages(ContextPassage("p", body), 100)
    assert all(len(p.text) <= 100 for p in passages)
    assert "\n\n".join(p.text for p in passages) == body


def test_a_tool_return_with_no_headings_is_split_at_paragraphs():
    passages = split_passages(ContextPassage("tool", "uno uno\n\ndos dos"), 12)
    assert [p.text for p in passages] == ["uno uno", "dos dos"]


def test_a_long_paragraph_is_split_at_lines_then_sentences_then_the_limit():
    rows = "\n".join(f"| fila {i} | {i * 11} |" for i in range(30))
    assert all(len(p.text) <= 80 for p in split_passages(ContextPassage("t", rows), 80))
    prose = " ".join(f"Oración número {i} del párrafo." for i in range(20))
    assert all(len(p.text) <= 80 for p in split_passages(ContextPassage("t", prose), 80))
    assert all(len(p.text) <= 50 for p in split_passages(ContextPassage("t", "x" * 200), 50))


def test_split_context_keeps_the_order_of_the_pages():
    context = [ContextPassage("a", "alfa"), ContextPassage("b", "beta")]
    assert [(p.page, p.text) for p in split_context(context, 100)] == [("a", "alfa"), ("b", "beta")]
