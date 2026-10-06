"""Route tests: the pages, the editor, the source documents and the picker."""

from __future__ import annotations

import re
import subprocess
import urllib.parse

import pytest

from domain.tools.db import get_connection
from services.ingest import wiki_stats
from services.wiki import list_pages, page_version, read_page, save_page
from tests.web.conftest import WIKI_ID


def git_log(wiki) -> str:
    return subprocess.run(["git", "-C", str(wiki.path), "log", "--oneline"], capture_output=True, text=True).stdout


# ── Picker ───────────────────────────────────────────────────────────────────

def test_picker_lists_the_wikis_under_the_home(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Elegí una wiki" in response.text and f'href="/w/{WIKI_ID}/"' in response.text


def test_picker_is_a_table_with_the_language_the_sources_and_the_pages(client, wiki):
    html = client.get("/").text
    row = html[html.index(f'href="/w/{WIKI_ID}/"'):]
    row = row[:row.index("</tr>")]
    stats = wiki_stats(wiki)
    assert ">Idioma</th>" in html and ">Fuentes</th>" in html and ">Páginas</th>" in html
    assert f'<td>{wiki.language}</td>' in row
    assert f'<td class="num">{stats["sources"]}</td>' in row and f'<td class="num">{stats["pages"]}</td>' in row


def test_picker_tags_the_most_recent_wiki_of_the_recent_list(client, settings, wiki, tmp_path):
    assert "última abierta" not in client.get("/").text
    client.post("/open", data={"path": str(wiki.path)})
    html = client.get("/").text
    assert html.count("última abierta") == 1
    row = html[html.index(f'href="/w/{WIKI_ID}/"'):]
    assert "última abierta" in row[:row.index("</tr>")]
    other = tmp_path / "other"
    other.mkdir()
    client.post("/open", data={"path": str(other), "create": "true"})
    html = client.get("/").text
    assert html.count("última abierta") == 1
    assert "última abierta" not in html[html.index(f'href="/w/{WIKI_ID}/"'):].split("</tr>")[0]


def test_listing_the_wikis_creates_nothing_on_disk(client, tmp_path):
    folder = tmp_path / "bare"
    (folder / "wiki").mkdir(parents=True)
    client.post("/open", data={"path": str(folder)})
    assert client.get("/").status_code == 200
    assert not (folder / ".llmwiki").exists() and not (folder / "sources").exists()


def test_listing_the_wikis_never_writes_to_their_index(client, wiki, monkeypatch):
    """The picker reads the counts read only: an index older than the schema would be
    migrated by `open_db`, so the listing must not reach it."""
    import domain.tools.db as db
    index = wiki.path / ".llmwiki" / "index.db"
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    monkeypatch.setattr(db, "open_db", lambda *a, **k: (_ for _ in ()).throw(AssertionError("open_db called")))
    assert f'href="/w/{WIKI_ID}/"' in client.get("/").text
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before


def test_the_picker_still_shows_the_counts(client, wiki):
    html = client.get("/").text
    row = html[html.index(f'href="/w/{WIKI_ID}/"'):].split("</tr>")[0]
    assert '<td class="num">0</td>' in row and '<td class="num">3</td>' in row


def test_a_wiki_home_redirects_to_its_overview(client):
    response = client.get(f"/w/{WIKI_ID}/")
    assert response.status_code == 303
    assert response.headers["location"] == f"/w/{WIKI_ID}/pages/overview"


def test_a_wiki_without_pages_opens_on_ingest(client, tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    client.post("/open", data={"path": str(folder), "create": "true"})
    response = client.get("/w/empty/")
    assert response.status_code == 303 and response.headers["location"] == "/w/empty/ingest"


def test_an_unknown_wiki_is_a_404(client):
    assert client.get("/w/nope/pages/overview").status_code == 404


def test_open_adds_a_folder_to_the_recent_list(client, settings, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    response = client.post("/open", data={"path": str(other), "create": "true"})
    assert response.status_code == 303 and response.headers["location"] == "/w/other/"
    assert str(other) in settings.recent_file.read_text()
    assert "other" in client.get("/").text


def test_open_opens_a_wiki_already_in_the_list(client, wiki):
    response = client.post("/open", data={"path": str(wiki.path)})
    assert response.headers["location"] == f"/w/{WIKI_ID}/"


def test_open_quotes_a_folder_name_with_spaces(client, tmp_path):
    folder = tmp_path / "Ciencia de datos"
    folder.mkdir()
    assert client.post("/open", data={"path": str(folder), "create": "true"}).headers["location"] == "/w/Ciencia%20de%20datos/"


def test_the_picker_offers_the_folder_browser_under_the_list_and_no_path_field(client):
    html = client.get("/").text
    assert html.index('class="wikis"') < html.index('hx-get="/browse"')
    assert "Abrir otra carpeta…" in html and 'name="path"' not in html


def test_the_browser_lists_the_subfolders_marks_the_wikis_and_hides_dot_folders(client, tmp_path):
    tmp_path = tmp_path / "browsed"
    (tmp_path / "b-plain").mkdir(parents=True)
    (tmp_path / "A-wiki" / "wiki").mkdir(parents=True)
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "a-file.txt").write_text("x")
    html = client.get("/browse", params={"path": str(tmp_path)}).text
    assert '<dialog class="browse"' in html
    names = re.findall(r'class="browse-dir"[^>]*>([^<]+)</button>', html)
    assert names == ["..", "A-wiki", "b-plain"]
    wiki_row = html[html.index(">A-wiki<"):].split("</li>")[0]
    assert ">wiki</span>" in wiki_row and ">wiki</span>" not in html[html.index(">b-plain<"):].split("</li>")[0]
    assert f'name="path" value="{tmp_path}"' in html and 'action="/open"' in html


def test_the_browser_says_whether_the_folder_shown_is_a_wiki(client, wiki, tmp_path):
    assert "Esta carpeta es una wiki." in client.get("/browse/panel", params={"path": str(wiki.path)}).text
    assert "todavía no tiene una wiki" in client.get("/browse/panel", params={"path": str(tmp_path)}).text


def test_the_browser_falls_back_to_the_home_with_the_reason(client, settings, tmp_path):
    for path, reason in [("some/folder", "Escribí una ruta absoluta."),
                         (str(tmp_path / "missing"), "No hay una carpeta en esa ruta.")]:
        html = client.get("/browse/panel", params={"path": path}).text
        assert reason in html and f'name="path" value="{settings.wiki_home.resolve()}"' in html


def test_the_browser_creates_a_folder_and_shows_it(client, tmp_path):
    html = client.post("/browse/mkdir", data={"parent": str(tmp_path), "name": " nueva wiki "}).text
    assert (tmp_path / "nueva wiki").is_dir()
    assert f'name="path" value="{tmp_path / "nueva wiki"}"' in html


@pytest.mark.parametrize("name, reason", [
    ("", "Escribí un nombre para la carpeta."),
    ("a/b", "no puede contener"),
    (".oculta", "no puede contener"),
    ("existe", "Ya existe una carpeta con ese nombre."),
])
def test_the_browser_refuses_a_bad_folder_name(client, tmp_path, name, reason):
    (tmp_path / "existe").mkdir()
    before = sorted(p.name for p in tmp_path.iterdir())
    html = client.post("/browse/mkdir", data={"parent": str(tmp_path), "name": name}).text
    assert reason in html and sorted(p.name for p in tmp_path.iterdir()) == before


def test_open_creates_nothing_in_a_plain_folder_without_the_confirmation(client, settings, tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    response = client.post("/open", data={"path": str(folder)})
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert list(folder.iterdir()) == []
    assert not settings.recent_file.exists() or str(folder) not in settings.recent_file.read_text()


def test_open_with_the_confirmation_creates_the_wiki_before_listing_it(client, settings, tmp_path):
    folder = tmp_path / "nueva"
    folder.mkdir()
    response = client.post("/open", data={"path": str(folder), "create": "true"})
    assert response.headers["location"] == "/w/nueva/"
    assert (folder / "wiki").is_dir() and (folder / "sources").is_dir() and (folder / ".llmwiki" / "index.db").is_file()
    assert str(folder) in settings.recent_file.read_text()


def test_the_browser_asks_before_creating_a_wiki_in_a_plain_folder(client, wiki, tmp_path):
    plain = client.get("/browse/panel", params={"path": str(tmp_path)}).text
    assert "Crear una wiki aquí…" in plain and 'id="browse-create"' in plain and 'name="create" value="true"' in plain
    assert ">Abrir esta carpeta<" not in plain
    a_wiki = client.get("/browse/panel", params={"path": str(wiki.path)}).text
    assert ">Abrir esta carpeta<" in a_wiki and 'id="browse-create"' not in a_wiki


def test_the_picker_drops_a_recent_wiki_that_no_longer_exists(client, settings, tmp_path):
    import shutil
    folder = tmp_path / "gone-wiki"
    folder.mkdir()
    client.post("/open", data={"path": str(folder), "create": "true"})
    assert 'href="/w/gone-wiki/"' in client.get("/").text
    shutil.rmtree(folder)
    assert 'href="/w/gone-wiki/"' not in client.get("/").text
    assert str(folder) not in settings.recent_file.read_text()


def test_a_wiki_that_stopped_existing_is_refused_not_recreated(client, tmp_path):
    import shutil
    folder = tmp_path / "short-lived"
    folder.mkdir()
    client.post("/open", data={"path": str(folder), "create": "true"})
    assert client.get("/w/short-lived/").status_code == 303           # opened once
    shutil.rmtree(folder / "wiki")
    shutil.rmtree(folder / ".llmwiki")
    response = client.get("/w/short-lived/pages/overview")
    assert response.status_code == 404 and 'href="/"' in response.text
    assert not (folder / "wiki").exists() and not (folder / ".llmwiki").exists()


def test_remove_from_the_list_is_offered_only_for_recent_wikis(client, settings, wiki, tmp_path):
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    client.post("/open", data={"path": str(folder), "create": "true"})
    html = client.get("/").text
    elsewhere = html[html.index('href="/w/elsewhere/"'):].split("</tr>")[0]
    home_row = html[html.index(f'href="/w/{WIKI_ID}/"'):].split("</tr>")[0]
    assert "Quitar de la lista" in elsewhere and "Quitar de la lista" not in home_row
    response = client.post("/recent/remove", data={"path": str(folder)})
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert 'href="/w/elsewhere/"' not in client.get("/").text and (folder / "wiki").is_dir()


def test_open_ignores_a_path_the_check_refuses(client, settings, tmp_path):
    for path in ["some/folder", str(tmp_path / "missing")]:
        response = client.post("/open", data={"path": path})
        assert response.status_code == 303 and response.headers["location"] == "/"
    assert not settings.recent_file.exists() or "missing" not in settings.recent_file.read_text()
    assert "some/folder" not in (settings.recent_file.read_text() if settings.recent_file.exists() else "")


def test_open_accepts_a_pasted_path_with_quotes(client, settings, tmp_path):
    other = tmp_path / "quoted"
    other.mkdir()
    client.post("/open", data={"path": f'"{other}"', "create": "true"})
    assert str(other) in settings.recent_file.read_text()


# ── Page view ────────────────────────────────────────────────────────────────

def test_page_view_rewrites_links_to_application_urls(client):
    response = client.get(f"/w/{WIKI_ID}/pages/concepts/cinderella")
    assert response.status_code == 200
    assert f'<a href="/w/{WIKI_ID}/pages/concepts/prince">the prince</a>' in response.text
    assert f'<a href="/w/{WIKI_ID}/pages/summaries/tale">the tale</a>' in response.text
    assert "title: Cinderella" not in response.text   # front matter is not shown


def test_page_view_lists_the_index_and_marks_the_current_page(client):
    html = client.get(f"/w/{WIKI_ID}/pages/concepts/prince").text
    assert 'class="current" aria-current="page"' in html
    assert "Conceptos" in html and "Resúmenes" in html


def test_each_index_group_shows_its_count_in_the_heading(client):
    html = client.get(f"/w/{WIKI_ID}/pages/concepts/prince").text
    assert "<h4>Conceptos · 2</h4>" in html and "<h4>Resúmenes · 1</h4>" in html


def test_the_page_toolbar_shows_the_path_in_monospace_before_the_actions(client):
    html = client.get(f"/w/{WIKI_ID}/pages/concepts/prince").text
    path = html.index('<span class="page-path mono"')
    assert "wiki/concepts/prince.md</span>" in html[path:path + 160]
    assert path < html.index('class="actions"', path) < html.index("Editar", path)


def test_a_source_document_shows_its_path_in_the_toolbar(client, wiki):
    (wiki.sources_dir / "notas.md").write_text("# Notas\n\nTexto.\n", encoding="utf-8")
    html = client.get(f"/w/{WIKI_ID}/view/notas.md").text
    assert "sources/notas.md</span>" in html


def test_the_reading_header_shows_the_chat_model(client):
    html = client.get(f"/w/{WIKI_ID}/pages/overview").text
    assert '<span class="bar-model"' in html and ">model</span>" in html
    assert 'class="bar-model"' not in client.get(f"/w/{WIKI_ID}/ingest").text


def test_the_mode_is_a_select_with_the_three_modes(client):
    html = client.get(f"/w/{WIKI_ID}/pages/overview").text
    assert 'type="radio"' not in html
    select = html[html.index('<select class="field mode-select"'):]
    select = select[:select.index("</select>")]
    assert 'name="mode"' in select
    assert re.findall(r'<option value="([\w-]+)"[^>]*>([^<]+)</option>', select) == [
        ("pre-retrieval", "Recuperación previa"), ("strict", "Estricto"), ("streaming", "Sin verificación")]


def test_a_missing_page_says_it_does_not_exist(client):
    response = client.get(f"/w/{WIKI_ID}/pages/concepts/no-existe")
    assert response.status_code == 404
    assert "La página no existe." in response.text


def test_a_page_id_outside_the_wiki_is_a_404(client):
    assert client.get(f"/w/{WIKI_ID}/pages/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_fixed_pages_have_no_delete_button(client):
    assert "Borrar la página" not in client.get(f"/w/{WIKI_ID}/pages/overview").text
    assert "Borrar la página" in client.get(f"/w/{WIKI_ID}/pages/concepts/prince").text


def test_a_source_document_name_in_a_page_links_to_the_document(client, wiki):
    (wiki.sources_dir / "Tale.pdf").write_bytes(b"%PDF-1.4")
    (wiki.wiki_dir / "overview.md").write_text("# Overview\n\nSee Tale.pdf.\n", encoding="utf-8")
    html = client.get(f"/w/{WIKI_ID}/pages/overview").text
    assert f'href="/w/{WIKI_ID}/view/Tale.pdf"' in html and 'target="_blank"' in html


# ── Editor ───────────────────────────────────────────────────────────────────

def test_editor_shows_the_body_without_the_front_matter(client):
    response = client.get(f"/w/{WIKI_ID}/edit/concepts/prince")
    assert response.status_code == 200
    assert "glass slipper" in response.text
    assert "type: concept" not in response.text
    assert "El bloque de front-matter no se edita: se conserva tal como está." in response.text


def test_the_editor_shows_the_markdown_and_its_preview_side_by_side(client):
    html = client.get(f"/w/{WIKI_ID}/edit/concepts/cinderella").text
    assert html.index('aria-label="Texto en Markdown"') < html.index('aria-label="Vista previa"')
    # The preview starts rendered by the server, with the page's links rewritten.
    preview = html[html.index('id="preview"'):]
    assert f'<a href="/w/{WIKI_ID}/pages/concepts/prince">the prince</a>' in preview[:preview.index("</article>")]


def test_the_textarea_asks_the_server_for_the_preview_500_ms_after_typing(client):
    html = client.get(f"/w/{WIKI_ID}/edit/concepts/cinderella").text
    textarea = html[html.index("<textarea"):html.index("</textarea>")]
    assert f'hx-post="/w/{WIKI_ID}/preview/concepts/cinderella"' in textarea
    assert 'hx-trigger="input changed delay:500ms"' in textarea and 'hx-target="#preview"' in textarea


def test_the_preview_renders_the_markdown_like_the_page_and_saves_nothing(client, wiki):
    before = read_page(wiki, "concepts/cinderella")
    response = client.post(f"/w/{WIKI_ID}/preview/concepts/cinderella",
                           data={"body": "## Hola\n\nVer [el príncipe](prince.md) y <script>alert(1)</script>."})
    assert response.status_code == 200
    assert "<h2>Hola</h2>" in response.text
    assert f'<a href="/w/{WIKI_ID}/pages/concepts/prince">el príncipe</a>' in response.text
    assert "<script" not in response.text
    assert read_page(wiki, "concepts/cinderella") == before


def test_edit_saves_keeps_the_front_matter_and_commits(client, wiki):
    page = "concepts/prince"
    version = page_version(wiki, page)
    before = read_page(wiki, page).split("---")[1]
    response = client.post(f"/w/{WIKI_ID}/edit/{page}", data={"body": "A new body.", "version": str(version)})
    assert response.status_code == 303
    assert response.headers["location"] == f"/w/{WIKI_ID}/pages/{page}"
    text = read_page(wiki, page)
    assert text.startswith("---\n") and text.split("---")[1] == before
    assert text.rstrip().endswith("A new body.")
    assert "edit: concepts/prince" in git_log(wiki)
    assert "A new body." in client.get(f"/w/{WIKI_ID}/pages/{page}").text


def test_a_stale_version_is_refused_and_the_message_survives_the_redirect(client, wiki):
    page = "concepts/prince"
    version = page_version(wiki, page)
    client.post(f"/w/{WIKI_ID}/edit/{page}", data={"body": "First save.", "version": str(version)})
    response = client.post(f"/w/{WIKI_ID}/edit/{page}", data={"body": "Second save.", "version": str(version)})
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"/w/{WIKI_ID}/edit/{page}?error=")
    assert "Second save." not in read_page(wiki, page)
    shown = client.get(location)
    assert f"{page} changed since it was opened (version {version + 1})" in shown.text


def test_an_error_text_with_url_characters_is_encoded_in_the_redirect(client, wiki, monkeypatch):
    """Defect A.3.2: `html.escape` does not encode a URL; `quote` does."""
    from services.wiki import PageConflict
    import web.routes.pages as pages_routes

    def refuse(*args, **kwargs):
        raise PageConflict("changed & <b>x</b> #1 100% ?a=b")

    monkeypatch.setattr(pages_routes, "save_page", refuse)
    response = client.post(f"/w/{WIKI_ID}/edit/concepts/prince", data={"body": "x", "version": "1"})
    location = response.headers["location"]
    assert " " not in location and "<" not in location and "#" not in location
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
    assert query["error"] == ["changed & <b>x</b> #1 100% ?a=b"]
    # And the editor shows the text unchanged, escaped by the template.
    shown = client.get(location).text
    assert "changed &amp; &lt;b&gt;x&lt;/b&gt; #1 100% ?a=b" in shown


def test_editing_a_missing_page_is_a_404(client):
    assert client.get(f"/w/{WIKI_ID}/edit/concepts/missing").status_code == 404
    assert client.post(f"/w/{WIKI_ID}/edit/concepts/missing", data={"body": "x"}).status_code == 404


# ── Delete a page ────────────────────────────────────────────────────────────

def test_delete_removes_the_page_and_commits(client, wiki):
    response = client.post(f"/w/{WIKI_ID}/delete/concepts/prince", data={"title": "Prince"})
    assert response.status_code == 303
    assert response.headers["location"] == f"/w/{WIKI_ID}/pages/overview?deleted=Prince"
    assert "concepts/prince" not in list_pages(wiki)
    assert "delete: concepts/prince" in git_log(wiki)
    assert "Se borró la página «Prince»." in client.get(response.headers["location"]).text


def test_delete_strips_the_links_other_pages_hold_to_the_page(client, wiki):
    client.post(f"/w/{WIKI_ID}/delete/concepts/prince", data={"title": "Prince"})
    assert "prince.md" not in read_page(wiki, "concepts/cinderella")


def test_the_three_fixed_pages_are_refused(client, wiki):
    for page in ("index", "overview", "log"):
        response = client.post(f"/w/{WIKI_ID}/delete/{page}", data={"title": page})
        assert response.status_code == 400
        assert "es fija" in response.json()["detail"]
        assert page in list_pages(wiki)


def test_deleting_a_missing_page_is_a_404(client):
    assert client.post(f"/w/{WIKI_ID}/delete/concepts/missing").status_code == 404


# ── Source documents ─────────────────────────────────────────────────────────

def test_a_pdf_source_is_served_inline(client, wiki):
    (wiki.sources_dir / "Tale one.pdf").write_bytes(b"%PDF-1.4 body")
    response = client.get(f"/w/{WIKI_ID}/view/Tale%20one.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["cache-control"] == "no-cache"
    assert response.content == b"%PDF-1.4 body"


def test_a_docx_source_is_served_as_the_pdf_ingestion_cached(client, wiki):
    (wiki.sources_dir / "Tale.docx").write_bytes(b"PK")
    with get_connection(wiki.db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, user_id, filename, relative_path, path, source_kind, file_type, status) "
            "VALUES ('doc-1', 'local', 'Tale.docx', 'sources/Tale.docx', 'sources/', 'source', 'docx', 'ready')")
        conn.commit()
    cached = wiki.path / ".llmwiki" / "cache" / "local" / "doc-1" / "converted.pdf"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"%PDF-1.4 converted")
    response = client.get(f"/w/{WIKI_ID}/view/Tale.docx")
    assert response.status_code == 200 and response.content == b"%PDF-1.4 converted"
    assert response.headers["content-type"] == "application/pdf"


@pytest.mark.parametrize("name", ["Tale.odt", "Tale.rtf", "Tale.doc"])
def test_every_office_source_is_served_as_the_pdf_ingestion_cached(client, wiki, name):
    (wiki.sources_dir / name).write_bytes(b"x")
    with get_connection(wiki.db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, user_id, filename, relative_path, path, source_kind, file_type, status) "
            "VALUES ('doc-1', 'local', ?, ?, 'sources/', 'source', 'odt', 'ready')", (name, f"sources/{name}"))
        conn.commit()
    cached = wiki.path / ".llmwiki" / "cache" / "local" / "doc-1" / "converted.pdf"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"%PDF-1.4 converted")
    response = client.get(f"/w/{WIKI_ID}/view/{name}")
    assert response.status_code == 200 and response.content == b"%PDF-1.4 converted"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")


def test_a_txt_source_is_served_inline_as_utf8_text(client, wiki):
    (wiki.sources_dir / "notas.txt").write_text("Canción <b>no html</b>", encoding="utf-8")
    response = client.get(f"/w/{WIKI_ID}/view/notas.txt")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert "content-disposition" not in response.headers
    assert response.headers["cache-control"] == "no-cache"
    assert response.content == "Canción <b>no html</b>".encode("utf-8")


def test_a_cp1252_txt_source_is_served_converted_to_utf8(client, wiki):
    (wiki.sources_dir / "viejo.txt").write_bytes("Año “nuevo”".encode("cp1252"))
    response = client.get(f"/w/{WIKI_ID}/view/viejo.txt")
    assert response.content.decode("utf-8") == "Año “nuevo”"


def test_a_md_source_is_rendered_inside_the_layout_and_is_read_only(client, wiki):
    (wiki.sources_dir / "notas.md").write_text(
        "---\ntitle: Interno\n---\n# Notas\n\n- **uno**\n- [enlace](https://example.com)\n", encoding="utf-8")
    response = client.get(f"/w/{WIKI_ID}/view/notas.md")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "<h1>Notas</h1>" in html and "<strong>uno</strong>" in html
    assert 'href="https://example.com"' in html
    assert "title: Interno" not in html
    assert 'class="reader"' in html and "Documento fuente" in html          # the application layout
    assert "/edit/" not in html and "/delete/" not in html and "Editar" not in html
    assert 'name="open_page" value=""' in html                             # the chat gets no page as context


def test_a_md_source_with_the_name_of_a_page_is_the_source(client, wiki):
    (wiki.sources_dir / "tale.md").write_text("# El archivo fuente\n", encoding="utf-8")
    assert "El archivo fuente" in client.get(f"/w/{WIKI_ID}/view/tale.md").text


def test_a_page_listing_a_md_source_links_it_to_the_view_not_to_a_page(client, wiki):
    (wiki.sources_dir / "notas.md").write_text("# Notas\n", encoding="utf-8")
    save_page(wiki, "concepts/cinderella", "Texto.\n\n## Fuentes\n\n- notas.md\n", None)
    html = client.get(f"/w/{WIKI_ID}/pages/concepts/cinderella").text
    assert f'href="/w/{WIKI_ID}/view/notas.md"' in html
    assert f'href="/w/{WIKI_ID}/pages/notas' not in html


def test_view_refuses_a_name_outside_sources(client, wiki):
    (wiki.sources_dir / "Tale.pdf").write_bytes(b"%PDF-1.4")
    for name in ("overview.md", "..%2Fwiki%2Foverview.md", "..%2F.llmwiki%2Findex.db",
                 "%2Fetc%2Fpasswd", "missing.pdf", ".hidden"):
        assert client.get(f"/w/{WIKI_ID}/view/{name}").status_code == 404, name


# ── Static files ─────────────────────────────────────────────────────────────

def test_static_files_are_revalidated_so_a_changed_stylesheet_is_seen(client):
    response = client.get("/static/css/reader.css")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-cache"
    again = client.get("/static/css/reader.css", headers={"If-None-Match": response.headers["etag"]})
    assert again.status_code == 304
