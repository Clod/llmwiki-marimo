"""header_breadcrumb must name the section a chunk is actually in.

The breadcrumb is what turns a search hit into a citation that can say *where*
inside a document the evidence came from. A breadcrumb naming a neighbouring
section is worse than no breadcrumb at all: it is a confident wrong answer.
"""

from domain.ingestion.chunker import chunk_text


def _para(word: str, n: int) -> str:
    """A paragraph of roughly n estimated tokens (4 chars per token)."""
    return " ".join([word] * (n * 4 // (len(word) + 1)))


def test_chunk_is_named_after_the_section_it_contains_not_the_next_one():
    """A chunk flushed at a heading belongs to the section it holds text from.

    The heading that triggers the flush opens the *following* chunk, so it must
    not appear in the breadcrumb of the one being closed.
    """
    content = (
        "# Doc\n\n"
        "## Section A\n\n"
        + _para("alpha", 400)
        + "\n\n"
        "## Section B\n\n"
        + _para("beta", 400)
    )

    chunks = chunk_text(content, chunk_size=512, overlap=0)

    assert len(chunks) >= 2
    first = chunks[0]
    assert "alpha" in first.content
    assert "beta" not in first.content
    assert first.header_breadcrumb == "Doc > Section A"


def test_chunk_spanning_sections_is_named_after_where_it_starts():
    """When a chunk crosses a heading, the breadcrumb names its opening section.

    A citation points at where the quoted passage begins; naming the last
    section the chunk happens to touch would send the reader past it.
    """
    content = (
        "# Doc\n\n"
        "## Section A\n\n"
        + _para("alpha", 300)
        + "\n\n"
        "## Section B\n\n"
        + _para("beta", 100)
    )

    chunks = chunk_text(content, chunk_size=512, overlap=0)

    assert len(chunks) == 1
    assert "alpha" in chunks[0].content
    assert "beta" in chunks[0].content
    assert chunks[0].header_breadcrumb == "Doc > Section A"


def test_breadcrumb_tracks_the_outline_across_chunks():
    """Each chunk carries the heading path in force where its text begins."""
    content = (
        "# Doc\n\n"
        "## Section A\n\n"
        "### Detail\n\n"
        + _para("alpha", 400)
        + "\n\n"
        "## Section B\n\n"
        + _para("beta", 400)
    )

    chunks = chunk_text(content, chunk_size=512, overlap=0)

    assert [c.header_breadcrumb for c in chunks] == [
        "Doc > Section A > Detail",
        "Doc > Section B",
    ]


def test_frontmatter_before_the_title_does_not_suppress_the_breadcrumb():
    """Every wiki page opens with a YAML block, and it is not body text.

    Treating that preamble as content would settle the breadcrumb before the
    page's own title was ever read, leaving every wiki chunk unlabelled.
    """
    content = (
        "---\n"
        "tags: [instrument]\n"
        "sources: [12 Cauciones Bursátiles.docx]\n"
        "---\n\n"
        "# Caución Bursátil\n\n"
        "## Definición\n" + _para("texto", 100) + "\n\n"
        "## Fuentes\n- 12 Cauciones Bursátiles.docx\n\n"
        "## Véase también\n- [Riesgo de crédito](riesgo-de-credito.md)\n"
    )

    chunks = chunk_text(content, chunk_size=512, overlap=0)

    assert len(chunks) == 1
    assert chunks[0].header_breadcrumb == "Caución Bursátil > Definición"


def test_a_heading_sharing_its_block_with_body_text_still_settles_the_crumb():
    """Generated pages write the section text on the line after the heading.

    That makes heading and body a single block, and a block that carries body
    text has to settle the breadcrumb — otherwise every later section heading
    keeps overwriting it and the chunk ends up named after the last one.
    """
    content = (
        "# Doc\n\n"
        "## Section A\n" + _para("alpha", 100) + "\n\n"
        "## Section Z\n" + _para("zeta", 100)
    )

    chunks = chunk_text(content, chunk_size=512, overlap=0)

    assert len(chunks) == 1
    assert chunks[0].header_breadcrumb == "Doc > Section A"


def test_a_trailing_heading_with_no_text_under_it_does_not_name_the_chunk():
    """Documents often end on a heading whose content the parser dropped.

    That heading contributes no text, so a chunk closing on it is still made of
    the previous section — and must keep saying so.
    """
    content = (
        "# Doc\n\n"
        "## Section A\n" + _para("alpha", 400) + "\n\n"
        "## Section B\n" + _para("beta", 300) + "\n\n"
        "## Dropped table\n"
    )

    chunks = chunk_text(content, chunk_size=512, overlap=128)

    assert len(chunks) >= 2
    last = chunks[-1]
    assert last.content.rstrip().endswith("## Dropped table")
    assert last.header_breadcrumb == "Doc > Section B"


def test_text_before_any_heading_has_an_empty_breadcrumb():
    """Nothing is invented for a chunk that sits above the first heading."""
    chunks = chunk_text(_para("alpha", 100), chunk_size=512, overlap=0)

    assert len(chunks) == 1
    assert chunks[0].header_breadcrumb == ""
