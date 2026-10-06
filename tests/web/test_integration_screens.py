"""The sanitizer (fix/known-defects) against the screens of the other branches.

The review dialog's preview, the history's version view and diff, and the relations
screen must keep what they need, and still lose what is active. No model is used.
"""

from __future__ import annotations

import subprocess

from services import wiki as wiki_service
from tests.web.conftest import WIKI_ID

H = f"/w/{WIKI_ID}/history"
RICH = (
    "# Titulo\n\n| a | b |\n|:--|--:|\n| 1 | 2 |\n\n```python\nx = 1 < 2\n```\n\n"
    "Texto con `codigo`, **negrita**, [la tabla](prince.md), [fuente](https://example.com \"Ejemplo\") "
    "y ![pic](pic.png \"t\").\n\n> cita\n\n- uno\n- dos\n\n"
    "<script>alert(1)</script><img src=x onerror=alert(2)>\n"
)
KEPT = ("<h1>Titulo</h1>", "<table>", '<th style="text-align:left">a</th>', '<td style="text-align:right">2</td>',
        '<code class="language-python">', "<code>codigo</code>", "<strong>negrita</strong>", "<blockquote>", "<li>uno</li>",
        'href="https://example.com"', 'src="pic.png"')
LOST = ("<script", "alert", "onerror")


def assert_rendered(html: str) -> None:
    for fragment in KEPT:
        assert fragment in html, fragment
    for fragment in LOST:
        assert fragment not in html, fragment


def test_the_review_dialog_preview_keeps_what_a_draft_needs(client):
    html = client.post(f"/w/{WIKI_ID}/chat/preview", data={"markdown": RICH, "page": "concepts/slipper-story"}).text
    assert_rendered(html)
    assert f'<a href="/w/{WIKI_ID}/pages/concepts/prince">la tabla</a>' in html     # a relative link survives


def test_the_dialog_itself_is_not_sanitized_away_and_escapes_the_draft(client, wiki, monkeypatch):
    """The dialog is a template: the sanitizer is not applied to it, and the draft is escaped in the editor."""
    import domain.chat.wiki_tools as wiki_tools
    from tests.web.test_chat import answered

    monkeypatch.setattr(wiki_tools, "draft_wiki_page", lambda db, ws, title, content, category, **kw:
                        wiki_tools.PageDraft(title, category, "x", "wiki/concepts/x.md", RICH, False))
    answered(client, None)
    html = client.post(f"/w/{WIKI_ID}/chat/draft", data={"conversation_id": "c1", "title": "X"}).text
    assert '<dialog class="review"' in html and "<textarea" in html and 'hx-post="' in html
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_the_version_view_of_a_page_keeps_what_the_page_needs(client, wiki):
    wiki_service.save_page(wiki, "concepts/cinderella", RICH, None)
    wiki_service.save_page(wiki, "concepts/cinderella", "Another body.", None)
    old = wiki_service.page_history(wiki, "concepts/cinderella")[1]
    text = client.get(f"{H}/page/concepts/cinderella/at/{old.sha}").text
    start = text.index('<article class="prose version-text">')
    assert_rendered(text[start:text.index("</article>", start)])


def test_the_diff_keeps_its_rows_and_escapes_the_active_text(client, wiki):
    wiki_service.save_page(wiki, "concepts/cinderella", RICH, None)
    first = wiki_service.page_history(wiki, "concepts/cinderella")[-1]
    text = client.get(f"{H}/page/concepts/cinderella/diff/{first.sha}").text
    assert 'class="row-add"' in text and 'class="row-del"' in text
    assert "<script>alert" not in text and "&lt;script&gt;" in text


def test_the_relations_screen_and_its_data_are_not_touched_by_the_sanitizer(client):
    html = client.get(f"/w/{WIKI_ID}/relations").text
    for needle in ("Mostrar fuentes", "/static/js/force-graph.min.js", "/static/js/relations.js", 'id="stage"'):
        assert needle in html, needle
    data = client.get(f"/w/{WIKI_ID}/relations.json").json()
    assert {n["id"] for n in data["nodes"]} == {"concepts/cinderella", "concepts/prince", "summaries/tale"}
    assert data["edges"]


def test_a_source_of_the_new_formats_is_a_node_and_its_view_url_works(client, wiki):
    """relations-graph x text-formats: the node of a `.md` source opens `/view/<file>`."""
    from domain.tools.db import get_connection

    for name, kind, body in (("notas.md", "md", "# Notas\n\nTexto.\n"), ("memo.txt", "txt", "Memo plano.\n")):
        (wiki.sources_dir / name).write_text(body, encoding="utf-8")
        with get_connection(wiki.db_path) as conn:
            conn.execute(
                "INSERT INTO documents (id, user_id, filename, title, relative_path, path, source_kind, file_type, "
                "status, page_count) VALUES (?, 'local', ?, ?, ?, 'sources/', 'source', ?, 'ready', 1)",
                (f"d-{kind}", name, name, f"sources/{name}", kind))
            conn.commit()
    nodes = {n["id"]: n for n in client.get(f"/w/{WIKI_ID}/relations.json").json()["nodes"]}
    for name in ("notas.md", "memo.txt"):
        node = nodes[f"source:{name}"]
        assert node["kind"] == "source" and node["title"] == name
        assert client.get(f"/w/{WIKI_ID}/view/{node['title']}").status_code == 200


def test_saving_from_the_chat_writes_one_commit_with_the_reviewed_text(client, wiki, monkeypatch):
    """known-defects x save-review: the reviewed text is written as it is, in one commit.
    The Spanish regeneration is covered in tests/unit/test_integration_interactions.py."""
    def count() -> int:
        return int(subprocess.run(["git", "-C", str(wiki.path), "rev-list", "--count", "HEAD"],
                                  capture_output=True, text=True).stdout or 0)

    before = count()
    response = client.post(f"/w/{WIKI_ID}/chat/save", data={"title": "Nota", "markdown": "# Nota\n\nTexto revisado.\n"})
    assert "Se creó la página «Nota»." in response.text
    assert count() == before + 1
    assert "chat: Nota" in subprocess.run(["git", "-C", str(wiki.path), "log", "-1", "--format=%s"],
                                          capture_output=True, text=True).stdout
