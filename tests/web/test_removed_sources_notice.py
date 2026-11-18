"""A source removed from sources/ by hand: when the user chooses the wiki, the
source is deleted from the wiki as the Ingest screen deletes it, and a notice names
what was deleted until the user dismisses it."""

from __future__ import annotations

from services import ingest
from tests.web.conftest import WIKI_ID
from tests.web.test_ingest import _summary_and_citing_page_of, add_source

NOTICE = "removed-sources"


def _choose(client):
    """Choose the wiki as the list of wikis does, and return the first screen."""
    return client.get(f"/w/{WIKI_ID}/?opened=1", follow_redirects=True).text


def test_choosing_the_wiki_deletes_a_source_removed_by_hand(client, wiki):
    add_source(wiki, "d1", "Tale.pdf")
    add_source(wiki, "d2", "Other.pdf")
    (wiki.sources_dir / "Other.pdf").write_bytes(b"%PDF")
    _summary_and_citing_page_of(wiki, "d1", also_cites="d2")
    html = _choose(client)
    assert NOTICE in html
    assert "1 documento se quitó a mano de sources/. Se borró de la wiki:" in html
    assert "<strong>Tale.pdf</strong>" in html and "Páginas borradas: summaries/tale.md." in html
    assert "Páginas conservadas y marcadas como obsoletas: concepts/prince.md." in html
    assert "Solo reconstruir toda la wiki a partir de las fuentes que quedan los quita." in html
    assert [s["filename"] for s in ingest.list_sources(wiki)] == ["Other.pdf"]
    assert not (wiki.path / "wiki" / "summaries" / "tale.md").exists()


def test_a_wiki_with_every_source_on_disk_shows_no_notice(client, wiki):
    add_source(wiki, "d1", "Tale.pdf")
    (wiki.sources_dir / "Tale.pdf").write_bytes(b"%PDF")
    assert NOTICE not in _choose(client)
    assert [s["filename"] for s in ingest.list_sources(wiki)] == ["Tale.pdf"]


def test_dismiss_hides_the_notice(client, wiki):
    add_source(wiki, "d1", "Tale.pdf")
    _choose(client)
    response = client.post(f"/w/{WIKI_ID}/removed/dismiss")
    assert response.status_code == 200 and response.text == ""
    assert NOTICE not in client.get(f"/w/{WIKI_ID}/maintain").text
