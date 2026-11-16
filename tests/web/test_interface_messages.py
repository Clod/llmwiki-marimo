"""The messages of base/ and the notices of a redirect follow the interface language."""

from __future__ import annotations

from domain.messages import M_
from tests.web.conftest import WIKI_ID
from web.i18n import translate_message, use_language


def test_a_base_message_is_translated_and_any_other_text_stays():
    with use_language("es"):
        assert translate_message(M_("🔎 Verifying %(n)s pages…", n=4)) == "🔎 Verificando 4 páginas…"
        assert translate_message("a plain line") == "a plain line"
    with use_language("en"):
        assert translate_message(M_("🔎 Verifying %(n)s pages…", n=4)) == "🔎 Verifying 4 pages…"


def test_the_console_of_an_operation_shows_the_lines_of_base_in_the_interface_language(app):
    """The operation thread runs in a copy of the context of the request that started it,
    so the progress lines of base/ come out in that language."""
    state = app.state.web
    entry = state.get_wiki(WIKI_ID)

    def work(progress):
        progress(M_("🗄 Creating a fresh index"))
        return M_("✅ No issues found.")

    with use_language("es"):
        op_id = state.start_operation(entry, work)
    lines = []
    while True:
        line = state.operations[op_id].lines.get(timeout=5)
        if line is None:
            break
        lines.append(line)
    assert lines == ["🗄 Creando un índice nuevo", "✅ No se encontraron problemas."]


def test_a_notice_travels_as_a_key_and_is_shown_in_the_language_of_the_screen(client, en_client):
    url = f"/w/{WIKI_ID}/maintain?notice=stale-deleted&n=2"
    assert "Se borraron 2 páginas obsoletas." in client.get(url).text
    assert "Deleted 2 stale pages." in en_client.get(url).text


def test_an_unknown_notice_key_shows_nothing(client):
    html = client.get(f"/w/{WIKI_ID}/maintain?notice=<b>injected</b>").text
    assert "injected" not in html


def test_the_redirect_after_deleting_the_stale_pages_carries_the_key(client):
    response = client.post(f"/w/{WIKI_ID}/stale/delete", follow_redirects=False)
    assert response.status_code == 303
    assert "notice=stale-deleted" in response.headers["location"] and "&n=" in response.headers["location"]


def test_the_extraction_includes_the_messages_of_base():
    from web.i18n_extract import ids

    assert "🔎 Verifying %(n)s pages…" in ids() and "Deletion failed: %(error)s" in ids()


def test_an_edit_conflict_travels_as_a_key_and_is_shown_in_the_language_of_the_screen(client, en_client):
    response = client.post(f"/w/{WIKI_ID}/edit/concepts/cinderella", data={"body": "Texto.", "version": "999"},
                           follow_redirects=False)
    location = response.headers["location"]
    assert "notice=page-conflict" in location and "version=" in location
    assert "cambió desde que se abrió (versión" in client.get(location).text
    assert "changed since it was opened (version" in en_client.get(location).text


def test_an_unknown_source_document_is_a_404_in_the_language_of_the_screen(client, en_client):
    assert client.get(f"/w/{WIKI_ID}/view/nada.pdf").json()["detail"] == "nada.pdf no es una fuente de esta wiki"
    assert en_client.get(f"/w/{WIKI_ID}/view/nada.pdf").json()["detail"] == "nada.pdf is not a source of this wiki"
