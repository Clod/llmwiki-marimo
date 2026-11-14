"""`[images] ignore`, true by default: the ingestion keeps no image of the sources and takes
no image file as a source. With `ignore = false` the image code runs as before (the other
image tests). The PDF tests use the real extractor on the Snow White PDF (skipped without
Java) and a fake model."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from domain.chat.settings_writer import IGNORE, write_settings
from domain.ingestion import describe, extractor, pipeline
from domain.ingestion.describe import Vision, effective_config
from domain.ingestion.describe_pass import describe_wiki_images, image_counts
from domain.ingestion.extractor import check_java, extract
from domain.ingestion.formats import IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS, supported_extensions
from domain.ingestion.images import FIGURES_FILENAME, IMAGES_DIRNAME, desc_name, images_dir
from domain.ingestion.pipeline import ingest_file
from domain.lint.image_checks import image_without_description_check, stale_image_description_check
from domain.tools.db import get_connection
from domain.tools.reindex import reindex_from_disk
from domain.wiki_settings import ImagesConfig, load_images_config
from tests.helpers.fake_vision import FakeVisionClient
from tests.helpers.image_ingest import CHART, COVER, config, fake_extract, ingest
from tests.helpers.pngs import write_png

REPO = Path(__file__).resolve().parents[2]
PDF = REPO / "examples" / "fairy-tales" / "sources" / "Snow White and the Seven Dwarfs.pdf"
needs_java = pytest.mark.skipif(check_java() is None, reason="Java is missing: opendataloader-pdf needs it")

_EXTRACTION = json.dumps({
    "document_summary": "A tale about a princess.",
    "concepts": [{"name": "Snow White", "category": "entity", "insight": "The princess"}],
})
_CONCEPT = "# Snow White\n\nThe princess of the tale.\n\n## Sources\n- [^1]: Snow White.pdf\n"
_OVERVIEW = "# Knowledge Base Overview\n\nTales.\n"


def write_config(path: Path, body: str) -> None:
    (path / "wiki_config.toml").write_text(body, encoding="utf-8")


# ── the key ──────────────────────────────────────────────────────────────────

def test_ignore_is_true_by_default(tmp_path):
    assert ImagesConfig().ignore is True
    assert load_images_config(tmp_path).ignore is True
    write_config(tmp_path, "[images]\ndescribe = true\n")
    assert load_images_config(tmp_path).ignore is True


def test_ignore_false_is_read(tmp_path):
    write_config(tmp_path, "[images]\nignore = false\n")
    assert load_images_config(tmp_path).ignore is False


def test_a_value_that_is_not_a_boolean_is_true(tmp_path):
    write_config(tmp_path, "[images]\nignore = 1\n")
    assert load_images_config(tmp_path).ignore is True


def test_the_supported_extensions_without_images():
    assert supported_extensions() == SUPPORTED_EXTENSIONS
    assert supported_extensions(False) == SUPPORTED_EXTENSIONS - IMAGE_EXTENSIONS
    assert not supported_extensions(False) & IMAGE_EXTENSIONS


def test_with_ignore_nothing_is_described_not_even_an_image_file():
    assert effective_config(ImagesConfig(describe=True), "report.pdf").describe is False
    assert effective_config(ImagesConfig(describe=True), "chart.png").describe is False
    assert effective_config(ImagesConfig(ignore=False), "chart.png").describe is True


# ── the extractor ────────────────────────────────────────────────────────────

@needs_java
def test_extract_without_images_writes_no_link_and_keeps_no_image(tmp_path):
    cache_dir = tmp_path / "cache" / "doc-1"
    write_png(cache_dir / IMAGES_DIRNAME / "old.png", 50, 50, 1)       # left by an earlier extraction
    (cache_dir / FIGURES_FILENAME).write_text("[]", encoding="utf-8")

    pages, _parser = extract(PDF, cache_dir, keep_images=False)

    assert pages and all("![" not in text for _n, text in pages)
    assert not (cache_dir / IMAGES_DIRNAME).exists()
    assert not (cache_dir / FIGURES_FILENAME).exists()


def test_extract_without_images_stores_a_markdown_source_as_it_is(tmp_path):
    workspace = tmp_path / "workspace"
    write_png(workspace / "sources" / "img" / "esquema.png", 100, 80, 3)
    # A config that keeps and describes images: `keep_images=False` wins over it.
    write_config(workspace, "[images]\nignore = false\ndescribe = true\n")
    source = workspace / "sources" / "manual.md"
    source.write_text("# T\n\nTexto.\n\n![esquema](img/esquema.png)\n", encoding="utf-8")
    cache_dir = workspace / ".llmwiki" / "cache" / "local" / "doc-1"
    write_png(cache_dir / IMAGES_DIRNAME / "old.png", 50, 50, 1)

    pages, _parser = extract(source, cache_dir, keep_images=False)

    assert pages == [(1, "# T\n\nTexto.\n\n![esquema](img/esquema.png)")]
    assert not (cache_dir / IMAGES_DIRNAME).exists()


def test_extract_without_images_refuses_an_image_file(tmp_path):
    chart = write_png(tmp_path / "chart.png", 100, 80, 3)
    with pytest.raises(ValueError, match="Unsupported file type: .png"):
        extract(chart, tmp_path / "cache", keep_images=False)


# ── the pipeline, with no [images] section ───────────────────────────────────

@needs_java
def test_a_pdf_with_images_is_ingested_as_before_images_were_kept(tmp_workspace):
    ws = tmp_workspace
    ws.llm.responses = [_EXTRACTION, _CONCEPT, _OVERVIEW]
    source = ws.workspace / "sources" / PDF.name
    shutil.copy(PDF, source)

    result = ingest_file(source, ws.db_path, ws.workspace, ws.llm, "fake", language="en")

    assert result.status == "ingested", result.message
    assert not images_dir(ws.workspace, result.doc_id).exists()
    summary = (ws.workspace / "wiki" / "summaries" / "snow-white-and-the-seven-dwarfs.md").read_text(encoding="utf-8")
    assert "## Figures" not in summary and "![" not in summary
    log = (ws.workspace / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "images," not in log


def test_an_image_file_is_refused_by_the_ingestion(tmp_workspace):
    ws = tmp_workspace
    chart = write_png(ws.workspace / "sources" / "chart.png", 100, 80, 3)

    result = ingest_file(chart, ws.db_path, ws.workspace, ws.llm, "fake")

    assert result.status == "failed"
    assert "ignores images" in result.message
    assert result.message.startswith("Unsupported file type '.png': this wiki ignores images "
                                     "([images] ignore = true in wiki_config.toml). Supported: ")
    assert ".pdf" in result.message and ".png" not in result.message.split("Supported: ")[1]


def test_the_scan_does_not_list_an_image_file(tmp_workspace, monkeypatch):
    ws = tmp_workspace
    write_png(ws.workspace / "sources" / "chart.png", 100, 80, 3)
    (ws.workspace / "sources" / "notes.md").write_text("# Notes\n\nText.\n", encoding="utf-8")
    seen: list[str] = []
    monkeypatch.setattr(pipeline, "ingest_file", lambda fp, *a, **k: seen.append(fp.name) or
                        pipeline.IngestResult(fp, "skipped", "x"))

    pipeline.scan_and_ingest(ws.workspace, ws.db_path, None, "m")

    assert seen == ["notes.md"]


# ── the settings writer ──────────────────────────────────────────────────────

def test_write_settings_accepts_images_ignore(tmp_path):
    assert write_settings(tmp_path, {IGNORE: False}) == [IGNORE]
    assert load_images_config(tmp_path).ignore is False
    assert write_settings(tmp_path, {IGNORE: True}) == [IGNORE]
    assert load_images_config(tmp_path).ignore is True


# ── the reindex, the describe pass and the lint checks (design 2.5) ─────────

def ignore_from_now_on(ws) -> None:
    """Set `ignore = true` and keep the other keys of the config the helper wrote."""
    path = ws.workspace / "wiki_config.toml"
    text = path.read_text(encoding="utf-8")
    assert "ignore = false" in text
    path.write_text(text.replace("ignore = false", "ignore = true"), encoding="utf-8")


def ignore_images(ws) -> None:
    """`ignore = true` over a config that would describe the images."""
    write_config(ws.workspace, '[wiki]\nlanguage = "es"\n\n[images]\nignore = true\ndescribe = true\n'
                 'min_side_px = 8\nmin_bytes = 10\n')


def source_names(ws) -> list[str]:
    with get_connection(ws.db_path) as conn:
        return [r["filename"] for r in conn.execute(
            "SELECT filename FROM documents WHERE source_kind = 'source' ORDER BY filename")]


def test_the_reindex_keeps_no_image_and_skips_an_image_file(tmp_workspace, monkeypatch):
    ws = tmp_workspace
    (ws.workspace / "sources" / "report.pdf").write_bytes(b"%PDF-1.4")
    write_png(ws.workspace / "sources" / "chart.png", 100, 80, 3)
    calls: list[tuple[str, bool]] = []

    def recording_extract(path, cache_dir, *args, keep_images=True, **kwargs):
        calls.append((path.name, keep_images))
        return [(1, "Text of the report.")], "fake"

    monkeypatch.setattr(extractor, "extract", recording_extract)

    report = reindex_from_disk(ws.workspace, ws.db_path)

    assert calls == [("report.pdf", False)]
    assert report.sources_indexed == 1 and source_names(ws) == ["report.pdf"]

    # With `ignore = false` the image file is a source and the PDF keeps its images.
    calls.clear()
    write_config(ws.workspace, "[images]\nignore = false\n")
    reindex_from_disk(ws.workspace, ws.db_path)
    assert calls == [("chart.png", True), ("report.pdf", True)]


@needs_java
def test_the_reindex_of_a_pdf_with_images_writes_no_link_and_keeps_no_image(tmp_workspace):
    ws = tmp_workspace
    shutil.copy(PDF, ws.workspace / "sources" / PDF.name)
    write_png(ws.workspace / "sources" / "chart.png", 100, 80, 3)

    report = reindex_from_disk(ws.workspace, ws.db_path)

    assert report.sources_indexed == 1 and source_names(ws) == [PDF.name]
    assert not list((ws.workspace / ".llmwiki" / "cache" / "local").rglob(IMAGES_DIRNAME))
    assert not list((ws.workspace / ".llmwiki" / "cache" / "local").rglob(FIGURES_FILENAME))
    with get_connection(ws.db_path) as conn:
        texts = [r["content"] for r in conn.execute("SELECT content FROM document_pages")]
    assert texts and all("![" not in text for text in texts)


def test_the_images_of_an_earlier_ingestion_are_still_described(tmp_workspace, monkeypatch):
    """`ignore` acts when a source is ingested: the images an earlier ingestion kept stay,
    and the describing pass depends on `describe` alone."""
    ws = tmp_workspace
    config(ws, describe=False)                       # images kept by an earlier ingestion
    fake_extract(monkeypatch, [CHART, COVER])
    ingest(ws, None)
    config(ws, describe=True)
    ignore_from_now_on(ws)

    counts = image_counts(ws.workspace, ws.db_path)
    assert (counts.kept, counts.enabled, counts.to_describe) == (2, True, 2)
    client = FakeVisionClient()
    assert describe_wiki_images(ws.workspace, ws.db_path, Vision(client, "gpt-test")) == 1
    assert len(client.calls) == 2


def test_the_description_checks_report_the_images_of_an_earlier_ingestion(tmp_workspace, monkeypatch):
    ws = tmp_workspace
    config(ws, describe=True)
    fake_extract(monkeypatch, [CHART, COVER])
    doc_id = ingest(ws, Vision(FakeVisionClient(), "gpt-test"))
    (images_dir(ws.workspace, doc_id) / desc_name(COVER["file"])).unlink()     # one image without description
    monkeypatch.setattr(describe, "prompt_text", lambda language: "an improved prompt")   # the other one stale
    config(ws, describe=True)
    ignore_from_now_on(ws)

    assert [i.check for i in image_without_description_check(ws.db_path, ws.workspace)] == [
        "image_without_description"]
    assert [i.check for i in stale_image_description_check(ws.db_path, ws.workspace)] == [
        "stale_image_description"]
