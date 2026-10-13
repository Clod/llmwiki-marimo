"""`inject_see_also` writes into a "See also" section the page already has.

The ingestion's last pass (`crosslink_wiki_pages`) runs after the `missing_xref`
repair, which opens the section; the pass then opened a second one
(`wiki/concepts/zapatilla-de-cristal.md`, 2026-10-11: two "## Véase también")."""

from domain.ingestion.wiki_generator import inject_see_also

SUMMARY = {"title": "La Cenicienta", "rel_path": "../summaries/la-cenicienta.md"}

PAGE = """# Zapatilla de cristal

## Definición
La zapatilla de cristal es un objeto de la cenicienta.

## Véase también

- [Hada madrina](hada-madrina.md)

## Fuentes
- La Cenicienta.pdf
"""


def test_the_links_go_into_the_existing_section():
    out = inject_see_also(PAGE, [SUMMARY], language="es")
    assert out.count("## Véase también") == 1
    section = out.split("## Véase también")[1].split("## Fuentes")[0]
    assert "- [Hada madrina](hada-madrina.md)" in section
    assert "- [La Cenicienta](../summaries/la-cenicienta.md)" in section
    assert out.rstrip().endswith("- La Cenicienta.pdf")


def test_an_existing_section_at_the_end_of_the_page_gets_the_links():
    page = "# X\n\nLa cenicienta.\n\n## See also\n\n- [A](a.md)\n"
    out = inject_see_also(page, [SUMMARY], language="en")
    assert out.count("## See also") == 1
    assert out.rstrip().endswith("- [La Cenicienta](../summaries/la-cenicienta.md)")


def test_a_page_without_the_section_still_gets_one_before_the_sources():
    page = PAGE.replace("## Véase también\n\n- [Hada madrina](hada-madrina.md)\n\n", "")
    out = inject_see_also(page, [SUMMARY], language="es")
    assert out.count("## Véase también") == 1
    assert out.index("## Véase también") < out.index("## Fuentes")
