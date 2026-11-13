"""OCR and the image handling on the same PDF.

A PDF with one page that has a text layer and an embedded image, and one scanned
page (one image, no text layer). opendataloader-pdf, the OCR engine and the vision
model are fakes: no Java, no OCR program, no model, no network.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from domain.ingestion import extractor, ocr
from domain.ingestion.describe import Vision
from domain.ingestion.extractor import extract_images, keep_image_links
from domain.ingestion.images import document_dir, image_link
from domain.tools.db import get_connection
from tests.helpers.fake_vision import FakeVisionClient
from tests.helpers.image_ingest import config, figures, ingest, stored_pages
from tests.helpers.pngs import png_bytes

TEXT = "A page with a text layer: the annual rate of five banks at thirty days."

_ELEMENTS = [
    {"type": "paragraph", "id": 1, "page number": 1, "content": TEXT},
    {"type": "image", "id": 2, "page number": 1, "source": "report_images/imageFile1.png"},
    {"type": "image", "id": 3, "page number": 2, "source": "report_images/imageFile2.png"},
]


class FakeEngine:
    name, label, supports_pages = "fake", "Fake", True

    def __init__(self):
        self.calls: list[list[int]] = []

    def run(self, pdf_path, pages, language, *, cache_dir, progress=None):
        self.calls.append(list(pages))
        return [(n, f"Texto leído por OCR en la página {n}, con la tasa del plazo fijo.") for n in pages]


def _fake_converter(monkeypatch):
    def convert(input_path, output_dir, format, quiet):
        out = Path(output_dir)
        (out / "report.json").write_text(json.dumps({"number of pages": 2, "kids": _ELEMENTS}), encoding="utf-8")
        (out / "report_images").mkdir()
        (out / "report_images" / "imageFile1.png").write_bytes(png_bytes(120, 90, 1))
        (out / "report_images" / "imageFile2.png").write_bytes(png_bytes(200, 260, 2))

    module = types.ModuleType("opendataloader_pdf")
    module.convert = convert
    monkeypatch.setitem(sys.modules, "opendataloader_pdf", module)
    monkeypatch.setattr(extractor, "check_java", lambda: "/usr/bin/java")


@pytest.fixture
def ws(tmp_workspace):
    return tmp_workspace


def test_a_pdf_with_a_scanned_page_and_an_embedded_image_runs_ocr_and_the_image_handling(ws, monkeypatch):
    config(ws)
    _fake_converter(monkeypatch)
    engine = FakeEngine()
    monkeypatch.setattr(ocr, "get_engine", lambda: engine)
    client = FakeVisionClient()

    doc_id = ingest(ws, Vision(client, "gpt-test"))

    assert engine.calls == [[2]]                       # OCR reads the scanned page only
    page1, page2 = stored_pages(ws, doc_id)
    assert TEXT in page1 and image_link(doc_id, "imageFile1.png", 1) in page1
    assert "Texto leído por OCR en la página 2" in page2
    assert image_link(doc_id, "imageFile2.png", 2) in page2  # the scanned page keeps its image link
    assert "<!-- image-description: imageFile1.png -->" in page1
    assert "<!-- image-description: imageFile2.png -->" in page2
    assert len(client.calls) == 2                      # one description per kept image
    assert {f["description"] for f in figures(ws, doc_id).values()} == {
        "imageFile1.png.desc.json", "imageFile2.png.desc.json"}
    with get_connection(ws.db_path) as conn:
        parser = conn.execute("SELECT parser FROM documents WHERE id = ?", (doc_id,)).fetchone()["parser"]
    assert parser == "opendataloader+ocr-fake"


def test_keep_image_links_appends_only_the_links_a_page_lost():
    link = image_link("d1", "imageFile1.png", 2)
    before = [(1, f"Text.\n\n{link}"), (2, link)]
    after = [(1, f"Text.\n\n{link}"), (2, "OCR text.")]
    assert keep_image_links(before, after) == [(1, f"Text.\n\n{link}"), (2, f"OCR text.\n\n{link}")]


def test_the_missing_image_repair_extracts_the_images_of_a_scanned_pdf_with_ocr_off(tmp_path, monkeypatch):
    _fake_converter(monkeypatch)
    monkeypatch.setattr(ocr, "get_engine", lambda: pytest.fail("the repair needs no OCR engine"))
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF")
    cache = document_dir(tmp_path, "abc")
    extract_images(source, cache)
    assert sorted(p.name for p in (cache / "images").glob("*.png")) == ["imageFile1.png", "imageFile2.png"]
