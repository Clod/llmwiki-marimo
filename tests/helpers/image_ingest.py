"""Helpers of the image tests: a wiki config with `[images]`, a fake extractor that keeps
real PNG files the way `extract_pdf` does, and an ingestion with a fake vision client."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from domain.ingestion import extractor, pipeline
from domain.ingestion.describe import Vision
from domain.ingestion.images import (
    document_dir, enrich_figures, image_link, read_figures, replace_dir,
)
from domain.ingestion.pipeline import ingest_file
from domain.tools.db import get_connection
from tests.helpers.pngs import png_bytes
from tests.helpers.workspace import WorkspaceFixture

EXTRACTION = json.dumps({"document_summary": "A report about rates.", "concepts": []})

CHART = {"page": 3, "file": "imageFile1.png", "w": 120, "h": 90, "shade": 1, "caption": "Rates."}
COVER = {"page": 1, "file": "imageFile2.png", "w": 200, "h": 200, "shade": 2, "caption": ""}


def config(ws: WorkspaceFixture, describe: bool = True, language: str = "es", **keys) -> None:
    keys = {"min_side_px": 8, "min_bytes": 10, **keys}
    lines = "".join(f"{k} = {v}\n" for k, v in keys.items())
    (ws.workspace / "wiki_config.toml").write_text(
        f'[wiki]\nlanguage = "{language}"\n\n[images]\nignore = false\n'
        f'describe = {"true" if describe else "false"}\n{lines}',
        encoding="utf-8")


def fake_extract(monkeypatch, specs: list[dict]):
    """Replace the extractor with one that keeps `specs` as the images of the PDF."""
    def extract(file_path: Path, cache_dir: Path, language: str = "en", progress_cb=None, *,
                keep_images: bool = True):
        doc_id = cache_dir.name
        scratch = cache_dir / "_scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        for spec in specs:
            (scratch / spec["file"]).write_bytes(png_bytes(spec["w"], spec["h"], spec["shade"]))
        replace_dir(scratch, cache_dir / "images")
        shutil.rmtree(scratch)
        (cache_dir / "figures.json").write_text(json.dumps(
            [{"page": s["page"], "file": s["file"], "caption": s.get("caption", "")} for s in specs]),
            encoding="utf-8")
        enrich_figures(cache_dir)
        pages = {}
        for spec in specs:
            pages.setdefault(spec["page"], []).append(image_link(doc_id, spec["file"], spec["page"]))
        return [(n, f"Text of page {n}.\n\n" + "\n\n".join(links)) for n, links in sorted(pages.items())], "fake"

    monkeypatch.setattr(pipeline, "extract", extract)
    monkeypatch.setattr(extractor, "extract", extract)


def ingest(ws: WorkspaceFixture, vision: Vision | None, *, language: str = "es", marker: bytes = b"1"):
    ws.llm.response_content = EXTRACTION
    source = ws.workspace / "sources" / "report.pdf"
    source.write_bytes(b"%PDF " + marker)
    result = ingest_file(source, ws.db_path, ws.workspace, ws.llm, "fake", language=language, vision=vision)
    assert result.status == "ingested", result.message
    return result.doc_id


def stored_pages(ws: WorkspaceFixture, doc_id: str) -> list[str]:
    with get_connection(ws.db_path) as conn:
        return [r["content"] for r in conn.execute(
            "SELECT content FROM document_pages WHERE document_id = ? ORDER BY page", (doc_id,))]


def figures(ws: WorkspaceFixture, doc_id: str) -> dict[str, dict]:
    return {f["file"]: f for f in read_figures(document_dir(ws.workspace, doc_id))}
