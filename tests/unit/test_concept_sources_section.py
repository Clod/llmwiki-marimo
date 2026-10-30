"""The code completes the sources section of a concept page (wiki_generator)."""

from domain.ingestion.wiki_generator import ensure_sources_section

PAGE = "# UVA\n\n## Definición\nEl plazo fijo UVA ajusta el capital.\n"


def test_a_missing_section_is_added_with_every_source():
    out = ensure_sources_section(PAGE, ["01 A.docx", "02 B.docx"], "Fuentes")
    assert out.endswith("## Fuentes\n\n- 01 A.docx\n- 02 B.docx\n")


def test_a_source_the_model_wrote_in_another_form_is_not_repeated():
    page = PAGE + "\n## Fuentes\n- 01 A.docx, p. 3\n- 02 b\n"
    assert ensure_sources_section(page, ["01 A.docx", "02 B.docx"], "Fuentes") == page


def test_a_source_the_model_left_out_is_appended_to_its_section():
    page = PAGE + "\n## Fuentes\n- 01 A.docx\n\n## Ver también\n- otra\n"
    out = ensure_sources_section(page, ["01 A.docx", "02 B.docx"], "Fuentes")
    assert "## Fuentes\n- 01 A.docx\n- 02 B.docx\n\n## Ver también" in out


def test_the_sources_of_an_existing_page_are_kept():
    """An updated page keeps the documents of its header, not only the new one."""
    from domain.ingestion import wiki_generator

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**_):
                    msg = type("M", (), {"content": PAGE})
                    return type("R", (), {"choices": [type("C", (), {"message": msg})]})

    concept = wiki_generator.ExtractedConcept(name="UVA", category="instrument", insight="x")
    existing = "---\nsources:\n  - resource: sources/01 A.docx\n---\n" + PAGE
    out = wiki_generator.build_concept_page(concept, "02 B.docx", existing, Client, "m", language="es")
    assert out.endswith("## Fuentes\n\n- 01 A.docx\n- 02 B.docx\n")
