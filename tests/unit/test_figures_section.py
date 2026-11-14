"""The "Figures" section of a summary page: written by the code, not by the model."""

import json

from domain.ingestion.images import document_dir, images_dir, load_figures
from domain.ingestion.wiki_generator import ExtractionResult, build_summary_page, figures_section

FIGURES = [
    {"page": 2, "file": "imageFile2.png", "caption": "A dwarf\nat work."},
    {"page": 3, "file": "imageFile3.png", "caption": ""},
]
META = {"filename": "tale.pdf", "file_type": "pdf", "page_count": 3, "parser": "opendataloader"}


def extraction():
    return ExtractionResult(document_summary="The tale.", concepts=[])


def test_the_section_has_one_thumbnail_per_image_with_page_and_caption_in_english():
    text = figures_section("doc1", FIGURES, "en")
    link = ".llmwiki/cache/local/doc1/images/imageFile2.png"
    assert text.startswith("\n## Figures\n")
    assert f"- [![Imagen, página 2]({link})]({link}) **Page 2.** *A dwarf at work.*" in text
    assert text.rstrip().endswith("**Page 3.**")  # no caption, no empty italics
    assert text.count("![") == 2


def test_the_section_is_in_the_wiki_language():
    text = figures_section("doc1", FIGURES, "es")
    assert "## Figuras" in text and "**Página 2.**" in text and "Figures" not in text


def test_no_figures_no_section():
    assert figures_section("doc1", [], "es") == ""


def test_the_summary_page_has_the_section_after_the_model_text_only_with_figures():
    with_figs = build_summary_page(META, extraction(), "es", figures=FIGURES, doc_id="doc1")
    without = build_summary_page(META, extraction(), "es")
    assert "## Figuras" not in without and "![" not in without
    assert with_figs.index("The tale.") < with_figs.index("## Figuras") < with_figs.index("## Información de la fuente")
    # Without figures the page is what it was before the section existed.
    assert without == build_summary_page(META, extraction(), "es", figures=[], doc_id="doc1")


def test_load_figures_leaves_out_an_image_that_is_not_kept(tmp_path):
    folder = images_dir(tmp_path, "doc1")
    folder.mkdir(parents=True)
    (folder / "imageFile2.png").write_bytes(b"x")
    (document_dir(tmp_path, "doc1") / "figures.json").write_text(json.dumps(FIGURES), encoding="utf-8")
    assert [f["file"] for f in load_figures(tmp_path, "doc1")] == ["imageFile2.png"]
    assert load_figures(tmp_path, "unknown") == []
