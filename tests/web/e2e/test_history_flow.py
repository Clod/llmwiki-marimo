"""The rollback flow in a browser: ingest with simulated services, delete a page,
open Historial, go back to the point before the delete, and find the page again.

The ingestion service is replaced (no model, no Java, no LibreOffice); it commits
through `auto_commit`, as the real one does, so the commit gets its index snapshot."""

from __future__ import annotations

from types import SimpleNamespace

from playwright.sync_api import expect

from domain.tools.git_ops import auto_commit, init_wiki_repo
from domain.tools.wiki_fs import create_page
from services import ingest
from tests.web.e2e.server import WIKI_ID


def test_ingest_delete_a_page_then_go_back_to_the_point_before_the_delete(page, live, monkeypatch):
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))

    def fake_ingest(wiki, uploads, client, model, *, full_repair, progress):
        for upload in uploads:
            (wiki.sources_dir / upload.name).write_bytes(upload.contents)
            progress(f"📝 Creating a page for {upload.name}")
            create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "ventanilla-unica", "Ventanilla única",
                        "La ventanilla única reúne los trámites de una inversión en un solo lugar. " * 3, [])
            init_wiki_repo(wiki.path)
            auto_commit(wiki.path, f"ingest: {upload.name}")
        return [SimpleNamespace(status="ingested", doc_id=None) for _ in uploads]

    monkeypatch.setattr(ingest, "ingest_uploads", fake_ingest)
    base = f"{live.url}/w/{WIKI_ID}"

    # 1. Ingest a document.
    page.goto(f"{base}/ingest")
    page.locator("input[type=file]").set_input_files(
        {"name": "Guia.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4"})
    page.get_by_role("button", name="Ingestar", exact=True).click()
    expect(page.locator("#progress .log")).to_contain_text("🏁 Ingesta: 1 ingestados, 0 omitidos, 0 fallidos")

    # 2. Delete a page the ingestion did not create.
    page.goto(f"{base}/pages/concepts/byma")
    page.get_by_role("button", name="Borrar").click()
    page.locator("#confirm-delete").get_by_role("button", name="Borrar la página").click()
    expect(page.get_by_text("Se borró la página «BYMA».")).to_be_visible()
    assert not (live.wiki_dir / "wiki/concepts/byma.md").exists()

    # 3. Historial, reached from the header; newest first, the current point has no button.
    page.locator(".tabs").get_by_role("link", name="Historial").click()
    expect(page).to_have_url(f"{base}/history")
    commits = page.locator("li.commit")
    expect(commits.first.locator(".message")).to_have_text("delete: concepts/byma")
    expect(commits.first.locator(".pill.current")).to_be_visible()
    expect(commits.first.get_by_role("button", name="Volver a este punto")).to_have_count(0)
    ingest_commit = commits.nth(1)
    expect(ingest_commit.locator(".message")).to_have_text("ingest: Guia.pdf")
    expect(ingest_commit.locator(".pill.snapshot")).to_be_visible()

    # 4. Ask to go back: the confirmation says what changes, inside the page.
    ingest_commit.get_by_role("button", name="Volver a este punto").click()
    confirm = ingest_commit.locator(".confirm")
    expect(confirm).to_contain_text("agregó 0")
    expect(confirm).to_contain_text("quitó 1")
    expect(confirm).to_contain_text("sources/ no cambia")
    expect(confirm).to_contain_text("vuelve a la copia guardada")
    confirm.get_by_role("button", name="Cancelar").click()
    expect(confirm).to_have_count(0)
    assert not (live.wiki_dir / "wiki/concepts/byma.md").exists()      # cancelling changed nothing

    ingest_commit.get_by_role("button", name="Volver a este punto").click()
    # Wait for the confirmation's text: a click on a button htmx has not processed yet is lost.
    expect(ingest_commit.locator(".confirm")).to_contain_text("vuelve a la copia guardada")
    ingest_commit.locator(".confirm").get_by_role("button", name="Volver a este punto").click()
    expect(page.locator("#progress .log")).to_contain_text("🏁 La wiki volvió al punto")
    expect(page.locator("#progress .log")).to_contain_text("índice restaurado de su copia")

    # 5. The list shows the revert as a new point; the page is back, readable and searchable.
    expect(page.locator("li.commit").first.locator(".message")).to_contain_text("revert: restore wiki to")
    page.goto(f"{base}/pages/concepts/byma")
    expect(page.locator(".prose")).to_contain_text("BYMA")
    assert (live.wiki_dir / "wiki/concepts/ventanilla-unica.md").exists()          # the ingested page stays
    assert (live.wiki_dir / "sources/Guia.pdf").exists()                            # sources/ untouched
    from domain.tools.search import search_chunks
    assert any(h["filename"] == "byma.md" for h in search_chunks(live.wiki().db_path, "BYMA", limit=20))
    assert dialogs == []


def test_the_history_of_a_page_and_the_comparison(page, live):
    from services import wiki as wiki_service

    wiki = live.wiki()
    wiki_service.save_page(wiki, "concepts/byma", "Primera versión escrita a mano sobre BYMA.", None)
    wiki_service.save_page(wiki, "concepts/byma", "Un texto nuevo y distinto sobre BYMA.", None)
    base = f"{live.url}/w/{WIKI_ID}"

    page.goto(f"{base}/pages/concepts/byma")
    page.locator(".page-head").get_by_role("link", name="Historial").click()
    expect(page).to_have_url(f"{base}/history/page/concepts/byma")
    expect(page.locator("li.commit")).to_have_count(2)
    page.locator("li.commit").nth(1).get_by_role("link", name="Ver esta versión").click()
    expect(page.get_by_text("Solo lectura")).to_be_visible()
    expect(page.locator(".prose")).to_contain_text("Primera versión escrita a mano")
    expect(page.locator("textarea")).to_have_count(0)
    page.get_by_role("button", name="Comparar con la versión actual").click()
    expect(page.locator(".diff-table .row-add").first).to_contain_text("Un texto nuevo y distinto")
    expect(page.locator(".diff-table .row-del").first).to_contain_text("Primera versión")
    page.get_by_role("button", name="Ocultar").click()
    expect(page.locator(".diff-table")).to_have_count(0)


def test_the_history_is_reached_from_the_header_of_the_ingestion_screen(page, live):
    page.goto(f"{live.url}/w/{WIKI_ID}/ingest")
    page.locator("nav.tabs").get_by_role("link", name="Historial").click()
    expect(page).to_have_url(f"{live.url}/w/{WIKI_ID}/history")
    expect(page.get_by_role("heading", name="Historial", exact=True)).to_be_visible()
