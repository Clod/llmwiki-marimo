"""Phase 3 of the image design: the images of a Markdown source (D10) and the image files
used as sources (D11). The real extractor reads the files (neither needs Java); the vision
model is a fake client. No model, no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domain.ingestion import pipeline
from domain.ingestion.describe import Vision
from domain.ingestion.images import document_dir, image_link, images_dir, read_figures
from domain.ingestion.pipeline import ingest_file
from domain.tools.db import get_connection
from domain.tools.reindex import reindex_from_disk
from domain.verify import FakeVerifier
from domain.verify.page import verify_page
from services.ingest import Upload
from tests.helpers.fake_vision import FakeVisionClient, answer
from tests.helpers.image_ingest import EXTRACTION, config, stored_pages
from tests.helpers.pngs import write_png


@pytest.fixture
def ws(tmp_workspace):
    return tmp_workspace


def run(ws, path: Path, vision: Vision | None, language: str = "es"):
    ws.llm.response_content = EXTRACTION
    result = ingest_file(path, ws.db_path, ws.workspace, ws.llm, "fake", language=language, vision=vision)
    assert result.status == "ingested", result.message
    return result.doc_id


def keep_images(ws) -> None:
    """A wiki config with only `[images] ignore = false`: the images are kept, and no other
    key of `[images]` is set."""
    (ws.workspace / "wiki_config.toml").write_text("[images]\nignore = false\n", encoding="utf-8")


def manual(ws, body: str) -> Path:
    folder = ws.workspace / "sources" / "manual"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "instalacion.md"
    path.write_text(body, encoding="utf-8")
    return path


# ── D10: Markdown sources ────────────────────────────────────────────────────

def test_a_local_image_of_a_markdown_source_is_kept_and_described(ws):
    config(ws)
    write_png(ws.workspace / "sources" / "manual" / "img" / "esquema.png", 100, 80, 3)
    path = manual(ws, "# Instalación\n\nConectar los equipos.\n\n![esquema de la red](img/esquema.png)\n\nFin.\n")
    client = FakeVisionClient()
    doc_id = run(ws, path, Vision(client, "gpt-test"))

    assert (images_dir(ws.workspace, doc_id) / "esquema.png").is_file()
    (figure,) = read_figures(document_dir(ws.workspace, doc_id))
    assert (figure["page"], figure["file"], figure["caption"]) == (1, "esquema.png", "esquema de la red")
    assert figure["description"] == "esquema.png.desc.json" and figure["width"] == 100
    (page,) = stored_pages(ws, doc_id)
    link = image_link(doc_id, "esquema.png", 1)
    assert f"Conectar los equipos.\n\n{link}\n\n<!-- image-description: esquema.png -->" in page
    assert page.endswith("<!-- /image-description -->\n\nFin.")
    assert len(client.calls) == 1
    assert "![esquema de la red](img/esquema.png)" in path.read_text(encoding="utf-8")   # the source is not modified


def test_the_rule_two_a_url_is_not_downloaded(ws):
    config(ws)
    path = manual(ws, "# T\n\nTexto previo.\n\n![x](https://example.invalid/grafico.png)\n")
    client = FakeVisionClient()
    doc_id = run(ws, path, Vision(client, "gpt-test"))
    (page,) = stored_pages(ws, doc_id)
    assert "![x](https://example.invalid/grafico.png) [Imagen no disponible]" in page
    assert client.calls == [] and not images_dir(ws.workspace, doc_id).exists()


def test_the_rule_one_a_link_cannot_leave_the_sources_folder(ws):
    config(ws)
    secret = write_png(ws.workspace / "secret" / "clave.png", 100, 100, 5)
    assert secret.is_file()
    path = manual(ws, "# T\n\nTexto previo.\n\n![llave](../../secret/clave.png)\n\n![otra](/etc/hosts.png)\n")
    client = FakeVisionClient()
    doc_id = run(ws, path, Vision(client, "gpt-test"))
    (page,) = stored_pages(ws, doc_id)
    assert page.count("[Imagen no disponible]") == 2 and "secret" not in page and "hosts" not in page
    assert client.calls == [] and not images_dir(ws.workspace, doc_id).exists()


@pytest.mark.parametrize("target", ["img/dibujo.svg", "img/falta.png", "img/nota.txt"])
def test_the_rule_three_only_raster_files_and_the_file_must_exist(ws, target):
    config(ws)
    (ws.workspace / "sources" / "manual" / "img").mkdir(parents=True)
    for name in ("dibujo.svg", "nota.txt"):
        (ws.workspace / "sources" / "manual" / "img" / name).write_text("<svg onload='x()'/>", encoding="utf-8")
    path = manual(ws, f"# T\n\nTexto.\n\n![z]({target})\n")
    doc_id = run(ws, path, Vision(FakeVisionClient(), "gpt-test"))
    (page,) = stored_pages(ws, doc_id)
    assert "[Imagen no disponible]" in page and "dibujo" not in page.replace("dibujo.svg", "")
    assert not images_dir(ws.workspace, doc_id).exists()


def test_the_marker_of_an_english_wiki_is_english(ws):
    config(ws, language="en")
    path = manual(ws, "# T\n\nText.\n\n![x](https://example.invalid/a.png)\n")
    doc_id = run(ws, path, None, language="en")
    assert "[Image not available]" in stored_pages(ws, doc_id)[0]


def test_two_files_with_the_same_name_get_two_kept_files(ws):
    config(ws)
    write_png(ws.workspace / "sources" / "manual" / "a" / "x.png", 90, 90, 1)
    write_png(ws.workspace / "sources" / "manual" / "b" / "x.png", 90, 90, 2)
    path = manual(ws, "# T\n\n![uno](a/x.png)\n\n![dos](b/x.png)\n\n![otra vez](a/x.png)\n")
    doc_id = run(ws, path, Vision(FakeVisionClient(), "gpt-test"))
    files = sorted(p.name for p in images_dir(ws.workspace, doc_id).glob("*.png"))
    assert files == ["x-2.png", "x.png"]
    assert len(read_figures(document_dir(ws.workspace, doc_id))) == 3


def test_a_link_inside_a_code_fence_is_left_alone(ws):
    config(ws)
    write_png(ws.workspace / "sources" / "manual" / "img" / "esquema.png", 100, 80, 3)
    body = "# T\n\nTexto.\n\n```\n![ejemplo](img/esquema.png)\n```\n"
    doc_id = run(ws, manual(ws, body), Vision(FakeVisionClient(), "gpt-test"))
    assert "![ejemplo](img/esquema.png)" in stored_pages(ws, doc_id)[0]
    assert not images_dir(ws.workspace, doc_id).exists()


def test_with_describe_off_a_markdown_source_is_stored_as_it_is(ws):
    config(ws, describe=False)
    write_png(ws.workspace / "sources" / "manual" / "img" / "esquema.png", 100, 80, 3)
    body = "# Instalación\n\nConectar.\n\n![esquema](img/esquema.png)\n\n![x](https://example.invalid/a.png)\n"
    doc_id = run(ws, manual(ws, body), Vision(FakeVisionClient(), "gpt-test"))
    assert stored_pages(ws, doc_id) == [body.strip("\n")]
    assert not images_dir(ws.workspace, doc_id).exists()


def test_a_markdown_source_ingested_again_does_not_describe_again(ws):
    config(ws)
    write_png(ws.workspace / "sources" / "manual" / "img" / "esquema.png", 100, 80, 3)
    path = manual(ws, "# T\n\nTexto.\n\n![e](img/esquema.png)\n")
    client = FakeVisionClient()
    run(ws, path, Vision(client, "gpt-test"))
    path.write_text(path.read_text(encoding="utf-8") + "\nOtra línea.\n", encoding="utf-8")
    run(ws, path, Vision(client, "gpt-test"))
    assert len(client.calls) == 1


# ── D11: image files as sources ──────────────────────────────────────────────

@pytest.fixture
def tools_missing(monkeypatch):
    monkeypatch.setattr(pipeline, "check_java", lambda: None)
    monkeypatch.setattr(pipeline, "check_libreoffice", lambda: None)


def chart(ws, name="grafico-tasas.png") -> Path:
    return write_png(ws.workspace / "sources" / name, 120, 90, 4)


def test_an_image_file_is_one_page_whose_text_is_the_description(ws, tools_missing):
    keep_images(ws)
    client = FakeVisionClient()
    doc_id = run(ws, chart(ws), Vision(client, "gpt-test"))        # no `describe` key: description is forced
    (page,) = stored_pages(ws, doc_id)
    assert page.startswith(image_link(doc_id, "grafico-tasas.png", 1))
    assert "<!-- image-description: grafico-tasas.png -->" in page and "Banco Galicia 34.5 %" in page
    with get_connection(ws.db_path) as conn:
        row = conn.execute("SELECT file_type, parser, page_count FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert (row["file_type"], row["parser"], row["page_count"]) == ("png", "image", 1)
    assert len(client.calls) == 1


def test_the_summary_of_an_image_source_carries_the_notice_from_the_locale(ws, tools_missing):
    keep_images(ws)
    chart_path = chart(ws)
    run(ws, chart_path, Vision(FakeVisionClient(), "gpt-test"), language="es")
    summary = (ws.workspace / "wiki" / "summaries" / "grafico-tasas.md").read_text(encoding="utf-8")
    assert "> El texto de esta fuente es una descripción generada de una imagen\n" in summary
    assert "<details><summary>Descripción generada</summary>" in summary

    chart_path.write_bytes(chart_path.read_bytes() + b"\0")
    run(ws, chart_path, Vision(FakeVisionClient(), "gpt-test"), language="en")
    english = (ws.workspace / "wiki" / "summaries" / "grafico-tasas.md").read_text(encoding="utf-8")
    assert "> The text of this source is a generated description of an image\n" in english


def test_a_text_source_has_no_notice(ws):
    path = manual(ws, "# T\n\nTexto.\n")
    run(ws, path, None)
    summary = (ws.workspace / "wiki" / "summaries" / "instalacion.md").read_text(encoding="utf-8")
    assert "descripción generada de una imagen" not in summary


def test_a_sentence_that_rests_on_an_image_source_has_the_third_state(ws, tools_missing):
    keep_images(ws)
    doc_id = run(ws, chart(ws), Vision(FakeVisionClient(), "gpt-test"))
    from domain.tools.references import update_references
    from domain.tools.wiki_fs import create_page

    info = create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "tasas", "Tasas",
                       "# Tasas\n\nEl Banco Nación paga 35.0 % de tasa nominal anual.\n", [],
                       sources=["grafico-tasas.png"], language="es")
    update_references(ws.db_path, info["id"], (ws.workspace / info["path"]).read_text(encoding="utf-8"), "/wiki/concepts/")
    outcome = verify_page(ws.db_path, ws.workspace, info["path"], FakeVerifier(), threshold=0.4, candidates=5,
                          language="es")
    (sentence,) = outcome.report.sentences
    assert sentence.supported and sentence.supported_by_image
    assert sentence.page == "sources/grafico-tasas.png#image:grafico-tasas.png"
    assert doc_id


def test_a_failed_description_leaves_the_link_and_the_marker(ws, tools_missing):
    keep_images(ws)
    doc_id = run(ws, chart(ws), Vision(FakeVisionClient([TimeoutError("x")]), "gpt-test"))
    (page,) = stored_pages(ws, doc_id)
    assert page.endswith("\n\n[Imagen sin describir, página 1]") and "image-description" not in page


def test_a_decorative_image_source_has_only_its_link(ws, tools_missing):
    keep_images(ws)
    client = FakeVisionClient([json.dumps({"type": "logo", "decorative": True, "description": "logo",
                                           "visible_text": [], "values": []})])
    doc_id = run(ws, chart(ws), Vision(client, "gpt-test"))
    assert stored_pages(ws, doc_id) == [image_link(doc_id, "grafico-tasas.png", 1)]


def test_the_size_filters_do_not_apply_to_an_image_source(ws, tools_missing):
    config(ws, min_side_px=5000, min_bytes=10_000_000)
    doc_id = run(ws, chart(ws), Vision(FakeVisionClient(), "gpt-test"))
    assert "image-description" in stored_pages(ws, doc_id)[0]


def test_reindex_rebuilds_the_text_of_an_image_source_with_no_model_call(ws, tools_missing):
    client = FakeVisionClient()
    config(ws, describe=False, language="es")      # the language the index is rebuilt in
    doc_id = run(ws, chart(ws), Vision(client, "gpt-test"))
    before = stored_pages(ws, doc_id)[0]
    reindex_from_disk(ws.workspace, ws.db_path)
    with get_connection(ws.db_path) as conn:
        new_id = conn.execute("SELECT id FROM documents WHERE source_kind = 'source'").fetchone()["id"]
    assert stored_pages(ws, new_id)[0].replace(new_id, "ID") == before.replace(doc_id, "ID")
    assert len(client.calls) == 1


def test_image_extensions_are_accepted_as_uploads_and_scanned(ws, tools_missing):
    keep_images(ws)
    for name in ("a.PNG", "b.jpg", "c.jpeg", "d.webp", "e.gif"):
        assert Upload(name, b"x").name == name
    chart(ws)
    (ws.workspace / "sources" / "dibujo.svg").write_text("<svg/>", encoding="utf-8")
    seen = []
    import pytest as _pytest  # noqa: F401  (the scan is exercised below with a recording ingest)
    original = pipeline.ingest_file
    pipeline.ingest_file = lambda fp, *a, **k: seen.append(fp.name) or pipeline.IngestResult(fp, "skipped", "x")
    try:
        pipeline.scan_and_ingest(ws.workspace, ws.db_path, None, "m")
    finally:
        pipeline.ingest_file = original
    assert seen == ["grafico-tasas.png"]


def test_an_image_source_answer_shape_is_the_one_of_the_prompt(ws, tools_missing):
    keep_images(ws)
    client = FakeVisionClient([answer(values=[])])
    doc_id = run(ws, chart(ws), Vision(client, "gpt-test"))
    assert "**Valores:**" not in stored_pages(ws, doc_id)[0]


# ── an image a Markdown source references is not a source of its own ───────

def scan(ws, monkeypatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(pipeline, "ingest_file", lambda fp, *a, **k: seen.append(
        fp.relative_to(ws.workspace / "sources").as_posix()) or pipeline.IngestResult(fp, "skipped", "x"))
    pipeline.scan_and_ingest(ws.workspace, ws.db_path, None, "m")
    return sorted(seen)


@pytest.mark.parametrize("describe", [True, False])
def test_the_scan_skips_an_image_a_markdown_source_references(ws, monkeypatch, describe):
    config(ws, describe=describe)
    write_png(ws.workspace / "sources" / "manual" / "img" / "esquema.png", 100, 80, 3)
    manual(ws, "# T\n\nTexto.\n\n![esquema](img/esquema.png)\n")
    assert scan(ws, monkeypatch) == ["manual/instalacion.md"]


def test_a_loose_image_is_still_an_image_source(ws, monkeypatch):
    keep_images(ws)
    write_png(ws.workspace / "sources" / "grafico.png", 100, 80, 3)
    write_png(ws.workspace / "sources" / "manual" / "img" / "esquema.png", 100, 80, 4)
    manual(ws, "# T\n\n![e](img/esquema.png)\n")
    assert scan(ws, monkeypatch) == ["grafico.png", "manual/instalacion.md"]


def test_a_link_that_leaves_sources_does_not_make_the_scan_skip_a_file(ws, monkeypatch):
    keep_images(ws)
    write_png(ws.workspace / "outside" / "clave.png", 100, 80, 3)
    write_png(ws.workspace / "sources" / "clave.png", 100, 80, 4)
    manual(ws, "# T\n\n![k](../../outside/clave.png)\n\n![k2](https://example.invalid/clave.png)\n")
    assert scan(ws, monkeypatch) == ["clave.png", "manual/instalacion.md"]
