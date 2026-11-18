"""The chat keeps the configuration of the wiki, with its aliases, in the agents it
builds for the first question (`AppState.get_agents`). An operation that writes
aliases or deletes pages drops them, so the next question reads the files again:
measured on 2026-11-18, an alias written by an ingestion was not used until the
server restarted."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import ingest
from tests.web.conftest import WIKI_ID
from tests.web.test_ingest import add_source, lines_of


def _entry(app):
    entry = app.state.web.get_wiki(WIKI_ID)
    app.state.web.get_agents(entry)
    assert entry.agents is not None
    return entry


def test_an_upload_drops_the_agents(app, client, monkeypatch):
    entry = _entry(app)
    monkeypatch.setattr(ingest, "ingest_uploads", lambda *a, progress, **k: [
        SimpleNamespace(status="ingested", doc_id=None)])
    lines_of(client, client.post(f"/w/{WIKI_ID}/ingest", files=[("files", ("a.md", b"# a", "text/markdown"))]))
    assert entry.agents is None


def test_a_scan_drops_the_agents(app, client, monkeypatch):
    entry = _entry(app)
    monkeypatch.setattr(ingest, "scan_sources", lambda *a, progress, **k: [])
    lines_of(client, client.post(f"/w/{WIKI_ID}/scan"))
    assert entry.agents is None


@pytest.mark.parametrize("keep_file", [True, False])
def test_a_source_deletion_drops_the_agents(app, client, wiki, keep_file):
    entry = _entry(app)
    add_source(wiki, "d1", "Tale.pdf")
    (wiki.sources_dir / "Tale.pdf").write_bytes(b"%PDF")
    data = {"filename": "Tale.pdf"} | ({} if keep_file else {"also_delete_file": "true"})
    lines_of(client, client.post(f"/w/{WIKI_ID}/sources/d1/delete", data=data))
    assert entry.agents is None
