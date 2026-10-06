"""Ingestion route tests: the screen, the streamed operations, the one-operation
lock, the deletions. The ingestion services are replaced; no model, Java or
LibreOffice is used."""

from __future__ import annotations

import threading
import urllib.parse
from types import SimpleNamespace

import pytest

from domain.tools.db import get_connection
from services import ingest
from services.wiki import list_pages
from tests.web.conftest import SPANISH, WIKI_ID, sse_events

BUSY = "Hay otra operación en curso sobre esta wiki."


def lines_of(client, response):
    """The log lines and the end event of the operation a POST started."""
    import re

    match = re.search(rf'sse-connect="/w/{WIKI_ID}/ops/(\w+)/events"', response.text)
    assert match, response.text
    events = client.get(f"/w/{WIKI_ID}/ops/{match.group(1)}/events")
    assert events.status_code == 200
    parsed = sse_events(events.text)
    assert parsed[-1][0] == "done"
    return [data.removeprefix("<li>").removesuffix("</li>") for name, data in parsed if name == "line"]


def result(status):
    return SimpleNamespace(status=status, doc_id=None)


def add_source(wiki, doc_id, filename, status="ready"):
    with get_connection(wiki.db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, user_id, filename, relative_path, path, source_kind, file_type, status) "
            "VALUES (?, 'local', ?, ?, 'sources/', 'source', 'pdf', ?)",
            (doc_id, filename, f"sources/{filename}", status))
        conn.commit()


# ── The screen ───────────────────────────────────────────────────────────────

def test_the_ingest_screen_lists_the_sources_and_the_maintenance_screen_the_rest(client, wiki):
    add_source(wiki, "d1", "Tale.pdf")
    with get_connection(wiki.db_path) as conn:
        conn.execute("UPDATE documents SET stale_since = '2026-10-01 10:00:00' "
                     "WHERE relative_path = 'wiki/concepts/prince.md'")
        conn.commit()
    ingest = client.get(f"/w/{WIKI_ID}/ingest").text
    assert "Tale.pdf" in ingest and 'class="pill ready"' in ingest
    assert f'hx-post="/w/{WIKI_ID}/scan"' in ingest and f'hx-post="/w/{WIKI_ID}/ingest"' in ingest
    for task in ("regenerate", "lint", "reindex"):
        assert f'hx-post="/w/{WIKI_ID}/{task}"' not in ingest
    assert "Páginas obsoletas" not in ingest and "Abrir el historial" not in ingest
    maintain = client.get(f"/w/{WIKI_ID}/maintain").text
    assert "Páginas obsoletas" in maintain and f'/w/{WIKI_ID}/pages/concepts/prince' in maintain
    assert "Java" in maintain and "LibreOffice" in maintain and "model" in maintain
    for task in ("regenerate", "lint", "reindex"):
        assert f'hx-post="/w/{WIKI_ID}/{task}"' in maintain
    assert f'hx-post="/w/{WIKI_ID}/scan"' not in maintain


def test_the_header_has_the_mantener_tab_after_ingestar(client):
    html = client.get(f"/w/{WIKI_ID}/maintain").text
    nav = html[html.index('<nav class="tabs"'):html.index("</nav>")]
    assert nav.index(">Ingestar<") < nav.index(">Mantener<") < nav.index(">Ver relaciones<")
    assert f'href="/w/{WIKI_ID}/maintain" aria-current="page"' in nav


def test_the_maintenance_screen_tells_a_libreoffice_that_cannot_convert_from_a_missing_one(client, monkeypatch):
    from domain.ingestion import extractor

    monkeypatch.setattr(extractor, "check_libreoffice", lambda: "/usr/bin/soffice")
    html = client.get(f"/w/{WIKI_ID}/maintain").text  # the fixture's probe says: cannot convert
    assert "Instalado, pero no convierte documentos" in html

    monkeypatch.setattr(extractor, "_probe_conversion", lambda lo: True)
    monkeypatch.setattr(extractor, "_probe_results", {})
    html = client.get(f"/w/{WIKI_ID}/maintain").text
    assert "Instalado, pero no convierte documentos" not in html and "/usr/bin/soffice" in html

    monkeypatch.setattr(extractor, "check_libreoffice", lambda: None)
    html = client.get(f"/w/{WIKI_ID}/maintain").text
    assert "No está instalado" in html


def test_the_notice_of_a_redirect_is_shown_escaped(client):
    html = client.get(f"/w/{WIKI_ID}/ingest?notice=" + urllib.parse.quote("<b>hola</b>")).text
    assert "&lt;b&gt;hola&lt;/b&gt;" in html


# ── Operations ───────────────────────────────────────────────────────────────

def test_ingest_streams_progress_and_closes_with_a_spanish_summary(client, wiki, monkeypatch):
    seen = {}

    def fake(wiki_, uploads, client_, model, *, full_repair, progress):
        seen.update(names=[u.name for u in uploads], data=[u.contents for u in uploads],
                    full_repair=full_repair, model=model)
        progress("🔍 Validating Tale.pdf")
        progress("✅ Tale.pdf")
        return [result("ingested"), result("skipped"), result("failed")]

    monkeypatch.setattr(ingest, "ingest_uploads", fake)
    response = client.post(f"/w/{WIKI_ID}/ingest", files=[("files", ("Tale.pdf", b"%PDF", "application/pdf"))],
                           data={"full_repair": "true"})
    assert response.status_code == 200
    assert lines_of(client, response) == [
        "🔍 Validating Tale.pdf", "✅ Tale.pdf", "🏁 Ingesta: 1 ingestados, 1 omitidos, 1 fallidos"]
    assert seen == {"names": ["Tale.pdf"], "data": [b"%PDF"], "full_repair": True, "model": "model"}


def test_an_upload_name_cannot_leave_sources_and_other_formats_are_refused(client, monkeypatch):
    seen = {}

    def fake(wiki_, uploads, client_, model, *, full_repair, progress):
        seen["names"] = [u.name for u in uploads]
        return [result("ingested")]

    monkeypatch.setattr(ingest, "ingest_uploads", fake)
    response = client.post(f"/w/{WIKI_ID}/ingest", files=[
        ("files", ("../../evil.pdf", b"x", "application/pdf")),
        ("files", ("notes.xlsx", b"x", "application/vnd.ms-excel"))])
    lines = lines_of(client, response)
    assert seen["names"] == ["evil.pdf"]
    assert ("⚠️ notes.xlsx: formato no admitido; se admiten "
            "DOC, DOCX, MD, ODT, PDF, RTF o TXT") in lines


def test_every_supported_format_is_accepted_in_any_letter_case(client, monkeypatch):
    seen = {}

    def fake(wiki_, uploads, client_, model, *, full_repair, progress):
        seen["names"] = [u.name for u in uploads]
        return [result("ingested")]

    monkeypatch.setattr(ingest, "ingest_uploads", fake)
    names = ["a.pdf", "b.docx", "c.doc", "d.odt", "e.rtf", "f.md", "g.txt", "H.MD", "I.Txt"]
    lines_of(client, client.post(f"/w/{WIKI_ID}/ingest", files=[("files", (n, b"x", "text/plain")) for n in names]))
    assert seen["names"] == names


def test_the_upload_filter_is_the_domain_list():
    from domain.ingestion.formats import SUPPORTED_EXTENSIONS
    from web.routes import ingest as route

    assert set(route._UPLOAD_SUFFIXES) == SUPPORTED_EXTENSIONS
    assert set(route.ACCEPT.split(",")) == SUPPORTED_EXTENSIONS


def test_the_ingest_screen_lists_the_formats_and_says_which_need_libreoffice(client):
    html = client.get(f"/w/{WIKI_ID}/ingest").text
    assert "DOC, DOCX, MD, ODT, PDF, RTF o TXT" in html
    assert 'accept=".doc,.docx,.md,.odt,.pdf,.rtf,.txt"' in html
    assert "ODT, RTF, DOC y DOCX necesitan LibreOffice" in html
    assert "MD y TXT no necesitan ni Java ni LibreOffice" in html


def test_the_configuration_line_still_tells_which_tools_are_missing(client, monkeypatch):
    monkeypatch.setattr(ingest, "tool_status", lambda: {"java": None, "libreoffice": None})
    html = client.get(f"/w/{WIKI_ID}/maintain").text
    assert html.count('class="missing"') >= 2
    assert "No está instalado" in html


def test_empty_dot_and_hidden_upload_names_are_refused_with_a_message(client, monkeypatch):
    seen = {}

    def fake(wiki_, uploads, client_, model, *, full_repair, progress):
        seen["names"] = [u.name for u in uploads]
        return []

    monkeypatch.setattr(ingest, "ingest_uploads", fake)
    response = client.post(f"/w/{WIKI_ID}/ingest", files=[
        ("files", (".hidden.pdf", b"x", "application/pdf")),
        ("files", ("..", b"x", "application/pdf")),
        ("files", ("ok.pdf", b"x", "application/pdf"))])
    lines = lines_of(client, response)
    assert seen["names"] == ["ok.pdf"]
    assert "⚠️ .hidden.pdf: nombre de archivo no válido" in lines


@pytest.mark.parametrize("route, service, label", [
    ("scan", "scan_sources", "Escaneo"),
    ("regenerate", "regenerate_summaries", "Regeneración"),
])
def test_scan_and_regenerate_close_with_a_spanish_summary(client, monkeypatch, route, service, label):
    def fake(wiki_, client_, model, *, progress):
        progress("working")
        return [result("ingested"), result("ingested")]

    monkeypatch.setattr(ingest, service, fake)
    lines = lines_of(client, client.post(f"/w/{WIKI_ID}/{route}"))
    assert lines == ["working", f"🏁 {label}: 2 ingestados, 0 omitidos, 0 fallidos"]


def test_lint_closes_with_a_spanish_summary(client, monkeypatch):
    monkeypatch.setattr(ingest, "lint_and_repair", lambda *a, progress, **k: 3)
    assert lines_of(client, client.post(f"/w/{WIKI_ID}/lint")) == ["🏁 Revisión y reparación: 3 problema(s) encontrado(s)"]


def test_an_error_inside_an_operation_reaches_the_log(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("Java is not installed")

    monkeypatch.setattr(ingest, "scan_sources", boom)
    assert lines_of(client, client.post(f"/w/{WIKI_ID}/scan")) == ["❌ Java is not installed"]


def test_an_unknown_operation_is_a_404(client):
    assert client.get(f"/w/{WIKI_ID}/ops/nope/events").status_code == 404


def test_the_closing_lines_in_the_page_are_not_english():
    """Defect A.3.3: with the Spanish interface the closing line of an operation is Spanish. (The
    English line exists now, for the English interface: `tests/web/test_i18n.py`.)"""
    from web.routes.ingest import summary_line

    line = summary_line("Ingesta", [result("ingested"), result("skipped"), result("failed")])
    assert line == "🏁 Ingesta: 1 ingestados, 1 omitidos, 1 fallidos"


# ── One operation per wiki ───────────────────────────────────────────────────

def test_one_operation_runs_at_a_time_per_wiki(client, wiki, monkeypatch, agents):
    started, release = threading.Event(), threading.Event()

    def slow(wiki_, uploads, client_, model, *, full_repair, progress):
        progress("start")
        started.set()
        assert release.wait(10)
        return [result("ingested")]

    monkeypatch.setattr(ingest, "ingest_uploads", slow)
    first = client.post(f"/w/{WIKI_ID}/ingest", files=[("files", ("A.pdf", b"x", "application/pdf"))])
    assert started.wait(10)

    # Every writer is refused while the first operation runs.
    for route in ("scan", "regenerate", "lint"):
        assert BUSY in client.post(f"/w/{WIKI_ID}/{route}").text
    second = client.post(f"/w/{WIKI_ID}/ingest", files=[("files", ("B.pdf", b"x", "application/pdf"))])
    assert BUSY in second.text and "sse-connect" not in second.text
    stale = client.post(f"/w/{WIKI_ID}/stale/delete")
    assert urllib.parse.unquote(stale.headers["location"]).endswith(BUSY)
    source = client.post(f"/w/{WIKI_ID}/sources/x/delete", data={"filename": "X.pdf"})
    assert BUSY in source.text and "sse-connect" not in source.text
    saved = client.post(f"/w/{WIKI_ID}/chat/save", data={"title": "T", "markdown": "# T\n\nText.\n"})
    assert BUSY in saved.text

    release.set()
    assert lines_of(client, first)[-1] == "🏁 Ingesta: 1 ingestados, 0 omitidos, 0 fallidos"
    # The lock is released at the end: a new operation starts.
    monkeypatch.setattr(ingest, "scan_sources", lambda *a, progress, **k: [])
    assert "sse-connect" in client.post(f"/w/{WIKI_ID}/scan").text


# ── Deletions ────────────────────────────────────────────────────────────────

def _summary_and_citing_page_of(wiki, doc_id):
    """The summary page `tale` derives from `doc_id`, and the concept `prince` cites it."""
    import uuid
    with get_connection(wiki.db_path) as conn:
        conn.execute("UPDATE documents SET source_document_id = ? WHERE relative_path = 'wiki/summaries/tale.md'",
                     (doc_id,))
        prince = conn.execute("SELECT id FROM documents WHERE relative_path = 'wiki/concepts/prince.md'").fetchone()[0]
        conn.execute("INSERT INTO document_references (id, source_document_id, target_document_id, reference_type) "
                     "VALUES (?, ?, ?, 'cites')", (str(uuid.uuid4()), prince, doc_id))
        conn.commit()


def test_source_delete_streams_its_steps_and_removes_the_file(client, wiki):
    add_source(wiki, "d1", "Tale.pdf")
    (wiki.sources_dir / "Tale.pdf").write_bytes(b"%PDF")
    _summary_and_citing_page_of(wiki, "d1")
    response = client.post(f"/w/{WIKI_ID}/sources/d1/delete",
                           data={"filename": "Tale.pdf", "also_delete_file": "true"})
    assert lines_of(client, response) == [
        "🗑 Borrando la fuente «Tale.pdf»",
        "Borrada la página de resumen summaries/tale",
        "1 página marcada como obsoleta: concepts/prince",
        "Archivo quitado de sources/",
        "🏁 Se borró la fuente «Tale.pdf».",
    ]
    assert ingest.list_sources(wiki) == [] and not (wiki.sources_dir / "Tale.pdf").exists()


def test_source_delete_keeps_the_file_by_default(client, wiki):
    add_source(wiki, "d1", "Tale.pdf")
    (wiki.sources_dir / "Tale.pdf").write_bytes(b"%PDF")
    lines = lines_of(client, client.post(f"/w/{WIKI_ID}/sources/d1/delete", data={"filename": "Tale.pdf"}))
    assert lines == ["🗑 Borrando la fuente «Tale.pdf»", "🏁 Se borró la fuente «Tale.pdf»."]
    assert (wiki.sources_dir / "Tale.pdf").exists()


def test_source_delete_of_an_unknown_source_says_why(client):
    lines = lines_of(client, client.post(f"/w/{WIKI_ID}/sources/nope/delete", data={"filename": "X.pdf"}))
    assert lines[-1].startswith("❌ No se pudo borrar la fuente «X.pdf»")


def test_source_delete_in_english(en_client, wiki):
    add_source(wiki, "d1", "Tale.pdf")
    lines = lines_of(en_client, en_client.post(f"/w/{WIKI_ID}/sources/d1/delete", data={"filename": "Tale.pdf"}))
    assert lines == ["🗑 Deleting the source “Tale.pdf”", "🏁 Deleted the source “Tale.pdf”."]


def test_stale_delete_removes_only_the_stale_pages(client, wiki):
    with get_connection(wiki.db_path) as conn:
        conn.execute("UPDATE documents SET stale_since = '2026-10-01 10:00:00' "
                     "WHERE relative_path = 'wiki/concepts/prince.md'")
        conn.commit()
    response = client.post(f"/w/{WIKI_ID}/stale/delete")
    assert response.status_code == 303
    location = urllib.parse.unquote(response.headers["location"])
    assert location.startswith(f"/w/{WIKI_ID}/maintain?") and location.endswith("Se borró 1 página obsoleta.")
    pages = list_pages(wiki)
    assert "concepts/prince" not in pages and "concepts/cinderella" in pages


# ── No model configured ──────────────────────────────────────────────────────

@pytest.fixture
def unconfigured(settings):
    import dataclasses

    from fastapi.testclient import TestClient

    from web.app import create_app

    blank = dataclasses.replace(settings, llm_api_key="", ingest_api_key="")
    return TestClient(create_app(blank), follow_redirects=False, cookies=SPANISH)


def test_the_ingest_screen_opens_without_a_model_configured(unconfigured):
    assert unconfigured.get(f"/w/{WIKI_ID}/ingest").status_code == 200


def test_an_operation_without_a_model_says_what_to_configure(unconfigured):
    files = [("files", ("A.pdf", b"x", "application/pdf"))]
    for response in (unconfigured.post(f"/w/{WIKI_ID}/ingest", files=files),
                     unconfigured.post(f"/w/{WIKI_ID}/scan"),
                     unconfigured.post(f"/w/{WIKI_ID}/regenerate"),
                     unconfigured.post(f"/w/{WIKI_ID}/lint")):
        assert response.status_code == 200
        assert "Falta configurar el modelo: define LLM_API_KEY en el archivo .env" in response.text


# ── Rebuild the index ────────────────────────────────────────────────────────

def test_reindex_streams_progress_and_closes_with_a_spanish_summary(client, wiki, monkeypatch):
    from domain.ingestion import extractor

    monkeypatch.setattr(extractor, "extract", lambda path, cache_dir: ([(1, "Texto de la fuente.")], "fake"))
    (wiki.sources_dir / "Cuento.pdf").write_bytes(b"%PDF-1.4")
    lines = lines_of(client, client.post(f"/w/{WIKI_ID}/reindex"))
    assert "📄 Indexing source Cuento.pdf" in lines
    assert lines[-1] == "🏁 Índice reconstruido: 1 fuentes, 3 páginas, 2 referencias"
    assert [s["filename"] for s in ingest.list_sources(wiki)] == ["Cuento.pdf"]
    assert (wiki.path / ".llmwiki" / "index.db.bak").is_file()


def test_reindex_reports_the_sources_that_could_not_be_read(client, wiki, monkeypatch):
    from domain.ingestion import extractor

    def extract(path, cache_dir):
        if path.name == "Roto.pdf":
            raise RuntimeError("corrupt PDF")
        return [(1, "Texto.")], "fake"

    monkeypatch.setattr(extractor, "extract", extract)
    (wiki.sources_dir / "Roto.pdf").write_bytes(b"x")
    (wiki.sources_dir / "Cuento.pdf").write_bytes(b"x")
    assert lines_of(client, client.post(f"/w/{WIKI_ID}/reindex"))[-1].endswith("1 fuentes, 3 páginas, 2 referencias; 1 fuentes con error")


def test_reindex_that_extracts_nothing_keeps_the_index_and_says_why(client, wiki, monkeypatch):
    from domain.ingestion import extractor

    def extract(path, cache_dir):
        raise RuntimeError("LibreOffice produced no PDF output")

    monkeypatch.setattr(extractor, "extract", extract)
    (wiki.sources_dir / "Cuento.pdf").write_bytes(b"x")
    lines = lines_of(client, client.post(f"/w/{WIKI_ID}/reindex"))
    assert lines[-1] == ("❌ No se pudo extraer el texto de ninguna fuente, así que el índice actual queda como estaba. "
                         "Primer error: Cuento.pdf: LibreOffice produced no PDF output")
    assert not (wiki.path / ".llmwiki" / "index.db.bak").exists()


def test_reindex_without_java_says_so_and_keeps_the_index(client, wiki, monkeypatch):
    from domain.ingestion import extractor

    def extract(path, cache_dir):
        raise extractor.JavaNotInstalledError(path.name)

    monkeypatch.setattr(extractor, "extract", extract)
    (wiki.sources_dir / "Cuento.pdf").write_bytes(b"x")
    lines = lines_of(client, client.post(f"/w/{WIKI_ID}/reindex"))
    assert lines[-1].startswith("❌ ") and "Java" in lines[-1]
    assert not any(line.startswith("🏁") for line in lines)
    assert not (wiki.path / ".llmwiki" / "index.db.bak").exists()
    assert "concepts/prince" in list_pages(wiki)


def test_reindex_needs_no_model_and_takes_the_wiki_lock(unconfigured, settings, wiki, monkeypatch):
    """The rebuild calls no model, so it runs with none configured; it shares the lock of every writer."""
    from domain.ingestion import extractor

    started, release = threading.Event(), threading.Event()

    def extract(path, cache_dir):
        started.set()
        assert release.wait(10)
        return [(1, "x")], "fake"

    monkeypatch.setattr(extractor, "extract", extract)
    app_wiki = unconfigured.app.state.web.get_wiki(WIKI_ID).wiki
    (app_wiki.sources_dir / "Cuento.pdf").write_bytes(b"x")
    first = unconfigured.post(f"/w/{WIKI_ID}/reindex")
    assert "sse-connect" in first.text and started.wait(10)
    assert BUSY in unconfigured.post(f"/w/{WIKI_ID}/reindex").text
    assert urllib.parse.unquote(unconfigured.post(f"/w/{WIKI_ID}/stale/delete").headers["location"]).endswith(BUSY)
    release.set()
    assert lines_of(unconfigured, first)[-1].startswith("🏁 Índice reconstruido: 1 fuentes")


def test_the_maintenance_screen_offers_the_rebuild_with_an_in_page_confirmation(client):
    html = client.get(f"/w/{WIKI_ID}/maintain").text
    assert "Reconstruir el índice" in html and 'id="confirm-reindex"' in html
    assert f'hx-post="/w/{WIKI_ID}/reindex"' in html


def test_the_sources_panel_route_returns_the_panel_alone(client):
    html = client.get(f"/w/{WIKI_ID}/sources").text
    assert html.lstrip().startswith('<section class="panel sources-panel" id="sources-panel"')
    assert 'hx-trigger="operation-done from:body"' in html and "<html" not in html
