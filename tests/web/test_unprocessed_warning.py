"""The warning about the files of sources/ without ingestion: shown once the user
chooses the wiki, until the user dismisses it or a scan or an upload ends."""

from __future__ import annotations

from types import SimpleNamespace

from services import ingest
from tests.web.conftest import WIKI_ID
from tests.web.test_ingest import lines_of

WARNING = "unprocessed-warning"


def _choose(client):
    """Choose the wiki as the list of wikis does, and return the first screen."""
    return client.get(f"/w/{WIKI_ID}/?opened=1", follow_redirects=True).text


def _drop(wiki, *names):
    for name in names:
        (wiki.sources_dir / name).write_text(f"# {name}\n", encoding="utf-8")


def test_choosing_the_wiki_warns_about_the_files_without_ingestion(client, wiki):
    _drop(wiki, "a.md", "b.md")
    html = _choose(client)
    assert WARNING in html
    assert "2 documentos de sources/ sin ingestar: a.md, b.md." in html
    assert f'hx-post="/w/{WIKI_ID}/unprocessed/dismiss"' in html and "Descartar" in html


def test_choosing_a_wiki_with_every_file_ingested_shows_no_warning(client, wiki):
    assert WARNING not in _choose(client)


def test_the_warning_stays_on_the_other_screens_of_the_wiki(client, wiki):
    _drop(wiki, "a.md")
    _choose(client)
    assert WARNING in client.get(f"/w/{WIKI_ID}/maintain").text


def test_the_tab_read_does_not_run_the_check(client, wiki):
    _choose(client)
    _drop(wiki, "a.md")
    assert WARNING not in client.get(f"/w/{WIKI_ID}/", follow_redirects=True).text


def test_dismiss_hides_the_warning_until_the_wiki_is_chosen_again(client, wiki):
    _drop(wiki, "a.md")
    _choose(client)
    response = client.post(f"/w/{WIKI_ID}/unprocessed/dismiss")
    assert response.status_code == 200 and response.text == ""
    assert WARNING not in client.get(f"/w/{WIKI_ID}/maintain").text
    assert WARNING not in client.get(f"/w/{WIKI_ID}/", follow_redirects=True).text
    assert WARNING in _choose(client)


def test_a_scan_ends_the_warning(client, wiki, monkeypatch):
    _drop(wiki, "a.md")
    _choose(client)
    monkeypatch.setattr(ingest, "scan_sources", lambda *a, progress, **k: [])
    lines_of(client, client.post(f"/w/{WIKI_ID}/scan"))
    assert WARNING not in client.get(f"/w/{WIKI_ID}/maintain").text


def test_an_upload_takes_only_its_files_out_of_the_warning(client, wiki, monkeypatch):
    _drop(wiki, "a.md", "b.md")
    _choose(client)
    monkeypatch.setattr(ingest, "ingest_uploads", lambda *a, progress, **k: [
        SimpleNamespace(status="ingested", doc_id=None)])
    lines_of(client, client.post(f"/w/{WIKI_ID}/ingest", files=[("files", ("a.md", b"# a", "text/markdown"))]))
    html = client.get(f"/w/{WIKI_ID}/maintain").text
    assert "1 documento de sources/ sin ingestar: b.md." in html
