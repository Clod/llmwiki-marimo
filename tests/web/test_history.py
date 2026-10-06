"""History routes: the list of commits, the revert as a streamed operation, the
refusals, and the history of one page. No model is used."""

from __future__ import annotations

import html as htmllib
import re
import subprocess
import threading
from pathlib import Path
from html.parser import HTMLParser

import pytest

from domain.rollback import snapshots
from domain.tools.search import search_chunks
from services import wiki as wiki_service
from tests.web.conftest import WIKI_ID, sse_events
from web.render import line_diff

BUSY = "Hay otra operación en curso sobre esta wiki."
H = f"/w/{WIKI_ID}/history"


@pytest.fixture
def commits(client, wiki):
    """Three more commits on the fixture wiki: a page deleted, a page edited, another page deleted.
    Returns the commits of the wiki, newest first."""
    assert client.post(f"/w/{WIKI_ID}/delete/concepts/prince", data={"title": "Prince"}).status_code == 303
    wiki_service.save_page(wiki, "concepts/cinderella", "A new body about the slipper.", None)
    assert client.post(f"/w/{WIKI_ID}/delete/summaries/tale", data={"title": "Tale"}).status_code == 303
    found = wiki_service.history(wiki)
    assert [c.message for c in found[:3]] == ["delete: summaries/tale", "edit: concepts/cinderella",
                                              "delete: concepts/prince"]
    return found


def op_lines(client, response):
    match = re.search(rf'sse-connect="/w/{WIKI_ID}/ops/(\w+)/events"', response.text)
    assert match, response.text
    parsed = sse_events(client.get(f"/w/{WIKI_ID}/ops/{match.group(1)}/events").text)
    assert parsed[-1][0] == "done"
    return [htmllib.unescape(d.removeprefix("<li>").removesuffix("</li>")) for n, d in parsed if n == "line"]


def hits(wiki, term="slipper"):
    return sorted(h["title"] for h in search_chunks(wiki.db_path, term, limit=50))


# ── The list ─────────────────────────────────────────────────────────────────

def test_the_history_screen_lists_the_commits_newest_first_with_their_marks(client, wiki, commits):
    text = client.get(H).text
    positions = [text.index(c.message) for c in commits[:3]]
    assert positions == sorted(positions)
    assert commits[0].date[:10] in text
    assert text.count("pill snapshot") == len(commits)                  # every commit has a snapshot
    assert re.search(r"\d+ páginas?", text)
    assert text.count("Volver a este punto") == len(commits) - 1             # not on the current point
    assert ">actual<" in text


def test_each_point_says_whether_it_has_an_index_copy(client, wiki, commits):
    text = client.get(H).text
    assert text.count("● copia del índice") == len(commits) and "sin copia" not in text
    snapshots._snapshot_file(wiki.path, commits[1].sha).unlink()
    text = client.get(H).text
    assert text.count("pill snapshot") == len(commits) - 1               # the copy mark is gone from that point
    assert text.count("sin copia") == 1 and text.count("● copia del índice") == len(commits) - 1
    row = next(r for r in text.split('<li class="commit">') if commits[1].message in r)
    assert "sin copia" in row


def test_the_confirmation_of_a_revert_sits_inside_its_point_in_the_attention_colour(client, wiki, commits):
    confirm = client.get(f"{H}/commit/{commits[2].sha}/confirm").text
    assert 'class="confirm"' in confirm
    css = (Path(__file__).resolve().parents[2] / "web" / "static" / "css")
    assert "background: var(--color-attention)" in (css / "base.css").read_text().split(".confirm {")[1].split("}")[0]
    # The point that holds the confirmation takes the lighter yellow of the row.
    assert ".commit:has(.confirm) { background: var(--color-attention-tint); }" in (css / "history.css").read_text()
    text = client.get(H).text
    assert f'id="confirm-{commits[2].short}"' in text


def test_the_header_and_the_ingestion_screen_lead_to_the_history(client):
    for url in (f"/w/{WIKI_ID}/ingest", f"/w/{WIKI_ID}/pages/overview"):
        assert f'href="{H}"' in client.get(url).text


def test_a_wiki_without_history_says_so(client, wiki, tmp_path):
    import shutil
    shutil.rmtree(wiki.path / ".git")
    assert "todavía no tiene historial" in client.get(H).text


def test_there_is_no_force_option_in_the_interface(client, wiki, commits):
    (wiki.wiki_dir / "concepts" / "cinderella.md").write_text("# by hand\n")
    pages = [client.get(H).text, client.get(f"{H}/commit/{commits[2].sha}/confirm").text]
    assert not any("force" in p.lower() for p in pages)


# ── The confirmation ─────────────────────────────────────────────────────────

def test_the_confirmation_says_what_changes_and_that_sources_do_not(client, commits):
    text = client.get(f"{H}/commit/{commits[2].sha}/confirm").text           # back to before 'delete: concepts/prince'
    assert "agregó 0" in text and "cambió 1" in text and "quitó 1" in text   # summaries/tale deleted... see below
    assert "concepts/cinderella" in text
    assert "sources/</code> no cambia" in text
    assert "El índice de búsqueda vuelve a la copia guardada" in text
    assert f'hx-post="{H}/commit/{commits[2].sha}/revert"' in text


def test_the_confirmation_of_a_point_without_snapshot_announces_the_rebuild(client, wiki, commits):
    snapshots._snapshot_file(wiki.path, commits[2].sha).unlink()
    text = client.get(f"{H}/commit/{commits[2].sha}/confirm").text
    assert "no tiene copia del índice" in text and "Java" in text


def test_the_confirmation_is_refused_when_no_snapshot_and_no_java(client, wiki, commits, monkeypatch):
    from domain.ingestion import extractor
    (wiki.sources_dir / "A.pdf").write_bytes(b"x")
    snapshots._snapshot_file(wiki.path, commits[2].sha).unlink()
    monkeypatch.setattr(extractor, "check_java", lambda: None)
    text = client.get(f"{H}/commit/{commits[2].sha}/confirm").text
    assert "No se puede volver a este punto" in text and "Java, que no está instalado" in text
    assert "hx-post" not in text


def test_uncommitted_changes_are_shown_and_the_revert_is_refused_with_the_files(client, wiki, commits):
    (wiki.wiki_dir / "concepts" / "cinderella.md").write_text("# by hand\n")
    (wiki.wiki_dir / "concepts" / "note.md").write_text("# note\n")
    assert "wiki/concepts/note.md" in client.get(H).text
    confirm = client.get(f"{H}/commit/{commits[2].sha}/confirm").text
    assert "No se puede volver a este punto" in confirm and "wiki/concepts/cinderella.md" in confirm
    assert "Hay 2 cambios sin guardar" in confirm and "uncommitted" not in confirm
    assert "hx-post" not in confirm
    head = subprocess.run(["git", "-C", str(wiki.path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    refused = client.post(f"{H}/commit/{commits[2].sha}/revert")
    assert "wiki/concepts/note.md" in refused.text and "sse-connect" not in refused.text
    assert (wiki.wiki_dir / "concepts" / "note.md").exists()       # nothing was discarded
    assert subprocess.run(["git", "-C", str(wiki.path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout == head


def test_a_commit_that_does_not_exist_is_a_404(client, commits):
    for sha in ("deadbeef", "HEAD", "--all", "zz"):
        assert client.get(f"{H}/commit/{sha}/confirm").status_code == 404
        assert client.post(f"{H}/commit/{sha}/revert").status_code == 404


# ── The revert ───────────────────────────────────────────────────────────────

def test_the_revert_streams_restores_pages_and_index_and_adds_a_point(client, wiki, commits):
    assert hits(wiki, "royal") == []                                   # the summary with the ball is gone
    deleted_page = wiki.wiki_dir / "concepts" / "prince.md"
    assert not deleted_page.exists()
    response = client.post(f"{H}/commit/{commits[3].sha}/revert")
    lines = op_lines(client, response)
    assert lines[-1].startswith(f"🏁 La wiki volvió al punto {commits[3].short}")
    assert "índice restaurado de su copia" in lines[-1]
    assert deleted_page.exists() and (wiki.wiki_dir / "summaries" / "tale.md").exists()
    assert wiki_service.history(wiki)[0].message == f"revert: restore wiki to {commits[3].short}"
    assert "summaries/tale.md" in hits_paths(wiki) and "concepts/prince.md" in hits_paths(wiki)
    assert "Tale" in hits(wiki, "royal")
    listing = client.get(f"{H}/list").text
    assert f"revert: restore wiki to {commits[3].short}" in listing


def hits_paths(wiki):
    from domain.tools.db import get_connection
    with get_connection(wiki.db_path) as conn:
        return {r["relative_path"].removeprefix("wiki/") for r in conn.execute(
            "SELECT relative_path FROM documents WHERE source_kind='wiki'")}


def test_the_revert_runs_under_the_wikis_lock(client, app, wiki, commits):
    entry = app.state.web.get_wiki(WIKI_ID)
    assert entry.busy.acquire(blocking=False)
    try:
        assert BUSY in client.post(f"{H}/commit/{commits[2].sha}/revert").text
    finally:
        entry.busy.release()


def test_a_refusal_during_the_operation_is_reported_in_spanish_and_changes_nothing(client, wiki, commits, monkeypatch):
    from domain.rollback.revert import IndexUnavailable

    def fail(*a, **k):
        raise IndexUnavailable("nothing_extracted", "no source could be read")

    monkeypatch.setattr(wiki_service, "revert", fail)
    lines = op_lines(client, client.post(f"{H}/commit/{commits[2].sha}/revert"))
    assert "No se cambió nada" in lines[-1]


def test_the_reindex_route_is_reported_in_the_closing_line(client, wiki, commits, monkeypatch):
    from domain.ingestion import extractor
    monkeypatch.setattr(extractor, "check_java", lambda: "/usr/bin/java")
    snapshots._snapshot_file(wiki.path, commits[2].sha).unlink()
    lines = op_lines(client, client.post(f"{H}/commit/{commits[2].sha}/revert"))
    assert "reconstruido desde los archivos" in lines[-1]
    assert any("Este punto no tiene copia del índice" in line for line in lines)


# ── History of one page ──────────────────────────────────────────────────────

def test_the_reading_screen_links_to_the_history_of_the_page(client):
    text = client.get(f"/w/{WIKI_ID}/pages/concepts/cinderella").text
    assert f'href="{H}/page/concepts/cinderella"' in text
    assert text.index(">Editar<") < text.index(">Historial<", text.index(">Editar<"))


def test_the_page_history_lists_its_commits_and_links_each_version(client, commits):
    text = client.get(f"{H}/page/concepts/cinderella").text
    assert "edit: concepts/cinderella" in text and "delete: summaries/tale" not in text
    assert "delete: concepts/prince" in text      # deleting a page strips the links other pages hold to it
    assert f"{H}/page/concepts/cinderella/at/{commits[1].sha}" in text
    assert ">actual<" in text


def test_a_deleted_page_keeps_its_history_and_can_be_read(client, commits):
    text = client.get(f"{H}/page/concepts/prince").text
    assert "ya no existe" in text and "delete: concepts/prince" in text
    older = [c for c in wiki_service.page_history(client.app.state.web.get_wiki(WIKI_ID).wiki, "concepts/prince")]
    version = client.get(f"{H}/page/concepts/prince/at/{older[-1].sha}")
    assert version.status_code == 200 and "Cinderella lost a glass slipper" in version.text


def test_a_version_is_rendered_read_only_with_its_date_and_message(client, wiki, commits):
    first = wiki_service.page_history(wiki, "concepts/cinderella")[-1]
    response = client.get(f"{H}/page/concepts/cinderella/at/{first.sha}")
    text = response.text
    assert "Solo lectura" in text and first.message in text and first.date[:10] in text
    assert "<textarea" not in text and "/edit/" not in text and "Borrar" not in text
    # prince and tale were deleted since: their links open the history of those pages
    assert f'href="{H}/page/concepts/prince"' in text and f'href="{H}/page/summaries/tale"' in text
    assert 'href="prince.md"' not in text
    assert "A new body" not in text


def test_a_version_where_the_page_did_not_exist_is_a_404(client, commits):
    last = commits[-1]
    assert client.get(f"{H}/page/concepts/ghost/at/{last.sha}").status_code == 404


def test_the_comparison_shows_the_lines_that_changed(client, wiki, commits):
    first = wiki_service.page_history(wiki, "concepts/cinderella")[-1]
    text = client.get(f"{H}/page/concepts/cinderella/diff/{first.sha}").text
    assert 'class="row-add"' in text and 'class="row-del"' in text
    assert "A new body about the slipper." in text and "estaban en esta versión" in text
    assert "<script" not in text


def test_the_comparison_of_the_current_version_says_it_is_identical(client, wiki, commits):
    latest = wiki_service.page_history(wiki, "concepts/cinderella")[0]
    assert "idéntica a la actual" in client.get(f"{H}/page/concepts/cinderella/diff/{latest.sha}").text


def test_the_comparison_escapes_the_text_of_the_page(client, wiki, commits):
    wiki_service.save_page(wiki, "concepts/cinderella", "<script>alert(1)</script>", None)
    first = wiki_service.page_history(wiki, "concepts/cinderella")[-1]
    text = client.get(f"{H}/page/concepts/cinderella/diff/{first.sha}").text
    assert "<script>alert" not in text and "&lt;script&gt;" in text


def test_page_ids_that_leave_wiki_are_404(client, commits):
    assert client.get(f"{H}/page/../../.gitignore").status_code in (404,)
    assert client.get(f"{H}/page/concepts/%2e%2e/%2e%2e/x/at/{commits[0].sha}").status_code == 404


# ── Every control has a tooltip ──────────────────────────────────────────────

class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.missing: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        control = tag in ("button", "select", "textarea", "summary") or (tag == "a" and "href" in a and "brand" not in a.get("class", "")) \
            or (tag == "input" and a.get("type") != "hidden")
        if control and "title" not in a and "aria-label" not in a:
            self.missing.append(f"<{tag} {a}>")
        elif control and tag in ("button", "summary") and "title" not in a:
            self.missing.append(f"<{tag} {a}>")


def untitled(text: str) -> list[str]:
    parser = Controls()
    parser.feed(text)
    return parser.missing


def test_every_control_of_the_history_screens_has_a_tooltip(client, wiki, commits):
    first = wiki_service.page_history(wiki, "concepts/cinderella")[-1]
    (wiki.wiki_dir / "concepts" / "note.md").write_text("# n\n")
    urls = [H, f"{H}/commit/{commits[2].sha}/confirm", f"{H}/page/concepts/cinderella",
            f"{H}/page/concepts/cinderella/at/{first.sha}", f"{H}/page/concepts/cinderella/diff/{first.sha}"]
    for url in urls:
        # The links inside the page text are the page's own, as on the reading screen.
        assert untitled(re.sub(r'<article class="prose.*?</article>', "", client.get(url).text, flags=re.S)) == [], url
    (wiki.wiki_dir / "concepts" / "note.md").unlink()
    assert untitled(client.get(f"{H}/commit/{commits[2].sha}/confirm").text) == []


def test_no_browser_dialog_is_used(client, commits):
    for url in (H, f"{H}/commit/{commits[2].sha}/confirm"):
        text = client.get(url).text
        assert "confirm(" not in text and "alert(" not in text and "hx-confirm" not in text


# ── line_diff ────────────────────────────────────────────────────────────────

def test_line_diff_marks_removed_and_added_lines_and_collapses_long_runs():
    old = "\n".join(f"line {i}" for i in range(30))
    new = old.replace("line 15", "LINE 15")
    rows = line_diff(old, new, context=2)
    assert [(r.sign, r.text) for r in rows if r.sign in "+-"] == [("-", "line 15"), ("+", "LINE 15")]
    assert [r.sign for r in rows].count("…") == 2 and len(rows) < 12


def test_line_diff_of_identical_texts_is_empty_and_of_a_missing_text_is_all_removed():
    assert line_diff("a\nb\n", "a\nb\n") == []
    assert [r.sign for r in line_diff("a\nb\n", "")] == ["-", "-"]
    assert [r.sign for r in line_diff("", "a\n")] == ["+"]


def test_threads_are_not_needed_to_serialize_two_reverts(client, wiki, commits):
    """The lock is the wiki's: two reverts in a row both run, one after the other."""
    first = op_lines(client, client.post(f"{H}/commit/{commits[2].sha}/revert"))
    assert first[-1].startswith("🏁")
    again = op_lines(client, client.post(f"{H}/commit/{commits[0].sha}/revert"))
    assert again[-1].startswith("🏁") and threading.active_count() >= 1
