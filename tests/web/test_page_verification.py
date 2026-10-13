"""Verification of wiki pages in the web interface: the state line, the marks,
the action on the reading screen, the checkboxes and the task of the ingestion
screen. The verifier is simulated; no model runs."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from domain.tools.db import get_connection
from domain.tools.references import update_references
from domain.tools.wiki_fs import create_page
from domain.verify import FakeVerifier
from services import ingest
from tests.web.conftest import SPANISH, WIKI_ID, fake_agents
from tests.web.test_ingest import lines_of, result
from web.app import create_app
PAGE_UNSUPPORTED_TIP = "Ninguna de las fuentes de esta página respalda esta oración"   # as the Spanish interface the tests run with writes it

SOURCE = "El plazo fijo UVA ajusta el capital por inflación. Tiene un plazo mínimo de 180 días."
GOOD = "El plazo fijo UVA tiene un plazo mínimo de 180 días."
BAD = "Rinde un cuarenta por ciento anual garantizado."
BODY = f"# UVA\n\n{GOOD}\n\n{BAD}\n\n## Fuentes\n- Tale.pdf\n"
TIP_INSTALL = "python3 quickstart.py --with-verifier"


@pytest.fixture
def verifier():
    return FakeVerifier()


@pytest.fixture
def app(settings, verifier):
    return create_app(settings, agents_factory=lambda wiki: fake_agents("x"), verifier=verifier)


@pytest.fixture
def uva(wiki):
    """A concept page that cites a source with text, and the source's id."""
    with get_connection(wiki.db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, user_id, filename, relative_path, path, source_kind, file_type, status, content) "
            "VALUES ('src1', 'local', 'Tale.pdf', 'sources/Tale.pdf', '/sources/', 'source', 'pdf', 'ready', ?)", (SOURCE,))
        conn.execute("INSERT INTO document_pages (document_id, page, content) VALUES ('src1', 1, ?)", (SOURCE,))
        conn.commit()
    info = create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "uva", "UVA", BODY, [], sources=["Tale.pdf"])
    update_references(wiki.db_path, info["id"], (wiki.path / info["path"]).read_text(encoding="utf-8"), "/wiki/concepts/")
    return info


def page(client, name="concepts/uva"):
    return client.get(f"/w/{WIKI_ID}/pages/{name}")


def verify(client, name="concepts/uva"):
    return client.post(f"/w/{WIKI_ID}/verify/{name}")


# ── The reading screen ───────────────────────────────────────────────────────

def test_an_unverified_page_says_so_and_offers_the_action(client, uva):
    html = page(client).text
    assert "Sin verificar" in html and "<mark" not in html
    assert f'action="/w/{WIKI_ID}/verify/concepts/uva"' in html and ">Verificar</button>" in html
    assert "oraciones sin respaldo" not in html


def test_a_page_with_no_sources_says_so_and_has_no_action(client):
    html = page(client, "concepts/prince").text
    assert "Sin fuentes para verificar" in html and ">Verificar</button>" not in html


def test_the_fixed_pages_show_no_verification_line(client):
    html = page(client, "overview").text
    assert "Sin verificar" not in html and "Sin fuentes para verificar" not in html
    assert ">Verificar</button>" not in html


def test_verifying_redirects_to_the_page_and_marks_the_unsupported_sentence(client, uva, verifier):
    response = verify(client)
    assert response.status_code == 303 and response.headers["location"] == f"/w/{WIKI_ID}/pages/concepts/uva"
    html = page(client).text
    assert "1 de 2 oraciones sin respaldo" in html
    assert f'<mark class="unsupported" title="{PAGE_UNSUPPORTED_TIP}">{BAD}</mark>' in html
    assert f'<span class="supported" title="Respaldada por Tale.pdf">{GOOD}</span>' in html
    assert "Sin verificar" not in html
    assert verifier.calls


def test_verifying_changes_nothing_in_the_page(client, uva, wiki):
    file = wiki.path / uva["path"]
    before = file.read_text(encoding="utf-8")
    verify(client)
    assert file.read_text(encoding="utf-8") == before


def test_the_sources_section_is_not_marked(client, uva):
    verify(client)
    html = page(client).text
    assert html.count("<mark") == 1


def test_a_page_edited_outside_the_application_shows_the_outdated_line_without_marks(client, uva, wiki):
    verify(client)
    file = wiki.path / uva["path"]
    file.write_text(file.read_text(encoding="utf-8") + "\nUna oración agregada a mano.\n", encoding="utf-8")
    html = page(client).text
    assert "Verificación desactualizada" in html and "<mark" not in html and "supported" not in html
    assert ">Verificar</button>" in html


def test_a_page_saved_in_the_editor_loses_its_verification(client, uva):
    verify(client)
    client.post(f"/w/{WIKI_ID}/edit/concepts/uva", data={"body": BODY + "\nOtra.\n", "version": ""})
    assert "Sin verificar" in page(client).text


def test_a_changed_source_makes_the_verification_outdated(client, uva, wiki):
    verify(client)
    with get_connection(wiki.db_path) as conn:
        conn.execute("UPDATE document_pages SET content = 'Texto nuevo de la fuente.' WHERE document_id = 'src1'")
        conn.commit()
    assert "Verificación desactualizada" in page(client).text


def test_the_threshold_of_wiki_config_reclassifies_the_stored_sentences(client, uva, wiki):
    verify(client)
    (wiki.path / "wiki_config.toml").write_text("[verify]\nthreshold = 0.0\n", encoding="utf-8")
    html = page(client).text
    assert "0 de 2 oraciones sin respaldo" in html and "<mark" not in html


def test_a_busy_wiki_refuses_the_verification_with_a_notice(client, uva, app):
    entry = app.state.web.get_wiki(WIKI_ID)
    entry.busy.acquire()
    try:
        response = verify(client)
    finally:
        entry.busy.release()
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]
    assert "Hay otra operación en curso sobre esta wiki." in client.get(response.headers["location"]).text
    assert "Sin verificar" in page(client).text


def test_verifying_a_page_that_does_not_exist_is_a_404(client):
    assert verify(client, "concepts/nope").status_code == 404


def test_without_a_local_verifier_the_action_is_disabled_with_the_install_tooltip(settings, uva, monkeypatch):
    from domain.verify import nli
    monkeypatch.setattr(nli, "is_available", lambda: False)
    plain = TestClient(create_app(settings, agents_factory=lambda wiki: fake_agents("x")), follow_redirects=False)
    html = page(plain).text
    assert re.search(r"<button class=\"btn-link\" disabled title=\"[^\"]*" + re.escape(TIP_INSTALL), html)
    response = verify(plain)
    assert response.status_code == 303 and "notice=" in response.headers["location"]


# ── The ingestion screen ─────────────────────────────────────────────────────

def test_the_checkbox_is_offered_on_ingest_scan_and_regenerate_and_the_task_on_maintain(client, uva):
    ingest = client.get(f"/w/{WIKI_ID}/ingest").text
    assert ingest.count("Verificar las páginas generadas") == 2          # the upload form and the scan
    assert "disabled" not in re.search(r'<input type="checkbox" name="verify"[^>]*>', ingest).group(0)
    maintain = client.get(f"/w/{WIKI_ID}/maintain").text
    assert maintain.count("Verificar las páginas generadas") == 2        # the confirmations of regenerate and of lint
    assert "Verificar todas las páginas" in maintain
    assert "¿Verificar la 1 página que tiene fuentes?" in maintain


def test_without_a_local_verifier_the_checkboxes_and_the_task_are_disabled(settings, uva, monkeypatch):
    from domain.verify import nli
    monkeypatch.setattr(nli, "is_available", lambda: False)
    plain = TestClient(create_app(settings, agents_factory=lambda wiki: fake_agents("x")), follow_redirects=False,
                       cookies=SPANISH)
    ingest = plain.get(f"/w/{WIKI_ID}/ingest").text
    maintain = plain.get(f"/w/{WIKI_ID}/maintain").text
    assert len(re.findall(r'<input type="checkbox" name="verify"[^>]* disabled', ingest)) == 2
    assert len(re.findall(r'<input type="checkbox" name="verify"[^>]* disabled', maintain)) == 2
    assert ingest.count(TIP_INSTALL) == 2 and maintain.count(TIP_INSTALL) == 3
    assert re.search(r'<button class="btn task-action" type="button" aria-controls="confirm-verify" disabled', maintain)


@pytest.mark.parametrize("route, service", [("ingest", "ingest_uploads"), ("scan", "scan_sources"),
                                            ("regenerate", "regenerate_summaries")])
def test_the_checkbox_passes_the_verifier_to_the_service_and_unchecked_does_not(client, monkeypatch, verifier, route, service):
    seen = []

    def fake(*args, progress, **kwargs):
        seen.append(kwargs.get("verifier"))
        return [result("ingested")]

    monkeypatch.setattr(ingest, service, fake)
    files = {"files": [("files", ("Tale.pdf", b"%PDF", "application/pdf"))]} if route == "ingest" else {}
    for checked in (True, False):
        data = {"verify": "true"} if checked else {}
        response = client.post(f"/w/{WIKI_ID}/{route}", data=data, **files)
        lines_of(client, response)
    assert seen == [verifier, None]


def test_a_forced_checkbox_without_a_verifier_runs_the_ingestion_without_it(settings, monkeypatch):
    from domain.verify import nli
    monkeypatch.setattr(nli, "is_available", lambda: False)
    plain = TestClient(create_app(settings, agents_factory=lambda wiki: fake_agents("x")), follow_redirects=False)
    seen = []
    monkeypatch.setattr(ingest, "scan_sources", lambda *a, progress, **k: seen.append(k.get("verifier")) or [])
    lines_of(plain, plain.post(f"/w/{WIKI_ID}/scan", data={"verify": "true"}))
    assert seen == [None]
