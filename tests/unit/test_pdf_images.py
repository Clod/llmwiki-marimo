"""Extraction keeps the images of a PDF and links the stored text to them.

The first tests replace opendataloader-pdf with a fake that writes a known
folder; the last one runs the real converter and skips without Java. No model.
"""

import json
import sys
import types
from pathlib import Path

import pytest

from domain.ingestion import images
from domain.ingestion.extractor import check_java, extract
from domain.ingestion.pdf_extract import extract_pdf

REPO = Path(__file__).resolve().parents[2]
SNOW_WHITE = REPO / "examples" / "fairy-tales" / "sources" / "Snow White and the Seven Dwarfs.pdf"

_ELEMENTS = [
    {"type": "paragraph", "id": 1, "page number": 1, "content": "Text one."},
    {"type": "image", "id": 2, "page number": 1, "source": "doc_images/imageFile1.png"},
    {"type": "image", "id": 3, "page number": 2, "source": "doc_images/imageFile2.png"},
    {"type": "caption", "id": 4, "page number": 2, "linked content id": 3, "content": "A dwarf."},
]


def _fake_converter(monkeypatch, elements=_ELEMENTS, with_images=True):
    def convert(input_path, output_dir, format, quiet):
        out = Path(output_dir)
        (out / "doc.json").write_text(
            json.dumps({"number of pages": 2, "kids": elements}), encoding="utf-8")
        if with_images:
            (out / "doc_images").mkdir()
            (out / "doc_images" / "imageFile1.png").write_bytes(b"one")
            (out / "doc_images" / "imageFile2.png").write_bytes(b"two")

    module = types.ModuleType("opendataloader_pdf")
    module.convert = convert
    monkeypatch.setitem(sys.modules, "opendataloader_pdf", module)


def test_images_are_copied_and_the_text_links_to_the_copy(monkeypatch, tmp_path):
    _fake_converter(monkeypatch)
    folder = tmp_path / "doc-id" / "images"
    pages = extract_pdf("any.pdf", folder)

    assert sorted(p.name for p in folder.iterdir()) == ["imageFile1.png", "imageFile2.png"]
    link = "![Imagen, página 1](.llmwiki/cache/local/doc-id/images/imageFile1.png)"
    assert pages[0][1] == f"Text one.\n\n{link}"
    assert "![Imagen, página 2](.llmwiki/cache/local/doc-id/images/imageFile2.png)" in pages[1][1]


def test_figures_file_keeps_page_file_and_caption(monkeypatch, tmp_path):
    _fake_converter(monkeypatch)
    extract_pdf("any.pdf", tmp_path / "doc-id" / "images")
    figures = json.loads((tmp_path / "doc-id" / "figures.json").read_text(encoding="utf-8"))
    assert figures == [
        {"page": 1, "file": "imageFile1.png", "caption": ""},
        {"page": 2, "file": "imageFile2.png", "caption": "A dwarf."},
    ]


def test_extracting_again_replaces_the_images_folder(monkeypatch, tmp_path):
    folder = tmp_path / "doc-id" / "images"
    folder.mkdir(parents=True)
    (folder / "old.png").write_bytes(b"old")
    _fake_converter(monkeypatch)
    extract_pdf("any.pdf", folder)
    assert not (folder / "old.png").exists()
    assert (folder / "imageFile1.png").read_bytes() == b"one"


def test_a_pdf_without_images_leaves_no_folder(monkeypatch, tmp_path):
    folder = tmp_path / "doc-id" / "images"
    folder.mkdir(parents=True)
    (folder / "old.png").write_bytes(b"old")
    _fake_converter(monkeypatch, elements=_ELEMENTS[:1], with_images=False)
    pages = extract_pdf("any.pdf", folder)
    assert pages[0][1] == "Text one."
    assert not folder.exists()


def test_without_a_folder_the_images_are_not_linked(monkeypatch):
    _fake_converter(monkeypatch)
    pages = extract_pdf("any.pdf")
    assert "![" not in pages[0][1]


def test_extract_keeps_the_images_in_the_cache_folder(monkeypatch, tmp_path):
    """A PDF reaches the same path as a DOCX once converted: the images go to
    `<cache_dir>/images`."""
    from domain.ingestion import extractor

    _fake_converter(monkeypatch)
    monkeypatch.setattr(extractor, "check_java", lambda: "/usr/bin/java")
    # The pages of the fake converter hold little text: OCR (feat/ocr) is not the subject here.
    monkeypatch.setattr(extractor, "apply_ocr", lambda path, pages, *args, **kwargs: (pages, None))
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"%PDF")
    cache = tmp_path / "cache" / "abc"
    pages, parser = extract(source, cache)
    assert parser == "opendataloader"
    assert (cache / "images" / "imageFile1.png").is_file()
    assert images.image_link("abc", "imageFile1.png", 1) in pages[0][1]


def test_docx_goes_through_the_same_path(monkeypatch, tmp_path):
    from domain.ingestion import extractor

    _fake_converter(monkeypatch)
    monkeypatch.setattr(extractor, "check_java", lambda: "/usr/bin/java")

    def fake_convert(file_path, cache_dir):
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "converted.pdf").write_bytes(b"%PDF")
        return cache_dir / "converted.pdf"

    monkeypatch.setattr(extractor, "convert_office_to_pdf", fake_convert)
    source = tmp_path / "doc.docx"
    source.write_bytes(b"x")
    cache = tmp_path / "cache" / "abc"
    pages, parser = extract(source, cache)
    assert parser == "libreoffice+opendataloader"
    assert (cache / "images" / "imageFile2.png").is_file()
    assert "images/imageFile2.png" in pages[1][1]


def test_image_path_accepts_only_a_plain_file_name(tmp_path):
    folder = images.images_dir(tmp_path, "abc")
    folder.mkdir(parents=True)
    (folder / "a.png").write_bytes(b"a")
    (tmp_path / "secret.txt").write_text("s")
    assert images.image_path(tmp_path, "abc", "a.png") == folder / "a.png"
    for bad in ("../../../../secret.txt", "..", "", "sub/a.png", "missing.png"):
        assert images.image_path(tmp_path, "abc", bad) is None


@pytest.mark.skipif(check_java() is None, reason="Java is missing: opendataloader-pdf needs it")
def test_real_converter_keeps_the_ten_images_of_snow_white(tmp_path):
    folder = tmp_path / "doc-id" / "images"
    pages = extract_pdf(str(SNOW_WHITE), folder)
    links = [m for _, text in pages for m in images.IMAGE_LINK_RE.finditer(text)]
    assert len(links) == 10
    assert len(list(folder.glob("*.png"))) == 10
    assert all((folder / m.group("file")).is_file() for m in links)
    assert [len(re_.findall(text)) for _, text in pages[:6] for re_ in [images.IMAGE_LINK_RE]] == [1, 2, 2, 1, 2, 2]


def test_a_first_extraction_of_a_pdf_without_images_creates_the_document_folder(monkeypatch, tmp_path):
    _fake_converter(monkeypatch, elements=_ELEMENTS[:1], with_images=False)
    extract_pdf("any.pdf", tmp_path / "new-doc" / "images")
    assert json.loads((tmp_path / "new-doc" / "figures.json").read_text(encoding="utf-8")) == []
    assert not (tmp_path / "new-doc" / "images").exists()
