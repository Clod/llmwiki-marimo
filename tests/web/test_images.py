"""The route that serves the images kept from a PDF source, and their rewriting."""

from __future__ import annotations

import uuid

import pytest

from domain.ingestion.images import images_dir
from domain.tools.db import get_connection
from services.wiki import kept_image
from tests.web.conftest import WIKI_ID
from web.render import render_markdown

PNG = b"\x89PNG\r\n\x1a\nfake"


def add_source(wiki, filename: str, files: dict[str, bytes]) -> str:
    """A source row in the index with kept images; return its document id."""
    doc_id = str(uuid.uuid4())
    with get_connection(wiki.db_path) as conn:
        user_id = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()["user_id"]
        with conn:
            conn.execute(
                "INSERT INTO documents (id, user_id, filename, title, path, relative_path, "
                "source_kind, file_type, status) VALUES (?,?,?,?,'sources/',?,'source','pdf','ready')",
                (doc_id, user_id, filename, filename, f"sources/{filename}"))
    folder = images_dir(wiki.path, doc_id)
    folder.mkdir(parents=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)
    return doc_id


def test_a_kept_image_is_served(client, wiki):
    doc = add_source(wiki, "a.pdf", {"imageFile1.png": PNG})
    response = client.get(f"/w/{WIKI_ID}/images/{doc}/imageFile1.png")
    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"


def test_a_file_of_another_document_is_a_404(client, wiki):
    one = add_source(wiki, "a.pdf", {"imageFile1.png": PNG})
    two = add_source(wiki, "b.pdf", {"other.png": PNG})
    assert client.get(f"/w/{WIKI_ID}/images/{one}/other.png").status_code == 404
    assert client.get(f"/w/{WIKI_ID}/images/{two}/other.png").status_code == 200


@pytest.mark.parametrize("file", ["..", "%2e%2e", "..%2fconverted.pdf", "%2e%2e%2f%2e%2e%2findex.db",
                                  "sub%2fimageFile1.png", "..%5cimageFile1.png"])
def test_a_file_name_that_leaves_the_folder_is_a_404(client, wiki, file):
    doc = add_source(wiki, "a.pdf", {"imageFile1.png": PNG})
    (images_dir(wiki.path, doc).parent / "converted.pdf").write_bytes(b"%PDF")
    assert client.get(f"/w/{WIKI_ID}/images/{doc}/{file}").status_code == 404


def test_an_unknown_document_id_is_a_404(client, wiki):
    add_source(wiki, "a.pdf", {"imageFile1.png": PNG})
    assert client.get(f"/w/{WIKI_ID}/images/no-such-id/imageFile1.png").status_code == 404
    # A folder on disk is not enough: the id must be a document of the index.
    folder = images_dir(wiki.path, "orphan-folder")
    folder.mkdir(parents=True)
    (folder / "x.png").write_bytes(PNG)
    assert client.get(f"/w/{WIKI_ID}/images/orphan-folder/x.png").status_code == 404


def test_a_wiki_page_is_not_a_source_document(client, wiki):
    with get_connection(wiki.db_path) as conn:
        page_id = conn.execute("SELECT id FROM documents WHERE source_kind = 'wiki' LIMIT 1").fetchone()["id"]
    folder = images_dir(wiki.path, page_id)
    folder.mkdir(parents=True)
    (folder / "x.png").write_bytes(PNG)
    with pytest.raises(FileNotFoundError):
        kept_image(wiki, page_id, "x.png")


def test_a_page_with_a_stored_image_link_shows_it_through_the_route(client, wiki):
    doc = add_source(wiki, "a.pdf", {"imageFile1.png": PNG})
    from services.wiki import save_page
    link = f"![Imagen, página 2](.llmwiki/cache/local/{doc}/images/imageFile1.png)"
    save_page(wiki, "concepts/prince", f"A prince.\n\n{link}", None)
    html = client.get(f"/w/{WIKI_ID}/pages/concepts/prince").text
    assert f'<img alt="Imagen, página 2" loading="lazy" src="/w/{WIKI_ID}/images/{doc}/imageFile1.png">' in html


# ── render.py alone ──────────────────────────────────────────────────────────

PAGES = {"concepts/prince"}


def test_render_rewrites_a_stored_image_link_in_a_page_and_in_an_answer():
    text = "Look.\n\n![Imagen, página 3](.llmwiki/cache/local/abc-1/images/imageFile4.png)"
    expected = '<img alt="Imagen, página 3" loading="lazy" src="/w/tales/images/abc-1/imageFile4.png">'
    assert expected in render_markdown("tales", text, "concepts/prince", PAGES)
    assert expected in render_markdown("tales", text, None, PAGES)


def test_render_leaves_a_file_name_outside_the_stored_form():
    text = "![Imagen, página 1](.llmwiki/cache/local/abc/images/image%20(1).png)"
    # The stored form has plain file names; a space or a parenthesis is left as it is.
    html = render_markdown("tales", text, None, PAGES)
    assert "/w/tales/images/" not in html


def test_render_leaves_a_page_without_images_unchanged():
    text = "A prince and [a tale](../summaries/tale.md).\n\n![logo](https://example.org/a.png)"
    html = render_markdown("tales", text, "concepts/prince", PAGES | {"summaries/tale"})
    assert "/images/" not in html
    assert 'src="https://example.org/a.png"' in html
    assert '<a href="/w/tales/pages/summaries/tale">a tale</a>' in html


def test_render_rewrites_the_thumbnail_link_of_a_figures_section():
    from domain.ingestion.wiki_generator import figures_section

    text = figures_section("abc-1", [{"page": 2, "file": "imageFile2.png", "caption": "A dwarf."}], "es")
    html = render_markdown("tales", text, "summaries/tale", {"summaries/tale"})
    url = "/w/tales/images/abc-1/imageFile2.png"
    assert f'<a href="{url}"><img alt="Imagen, página 2" loading="lazy" src="{url}"></a>' in html
    assert ".llmwiki" not in html
