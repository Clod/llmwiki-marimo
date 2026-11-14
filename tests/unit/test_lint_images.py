"""The missing-image and orphan-image checks and their repairs. No model.

Most tests replace the extraction of images with a fake; the last one runs the
real opendataloader-pdf on a copy of `examples/fairy-tales` and skips without Java.
"""

import shutil
import uuid
from pathlib import Path

import pytest

from domain.ingestion import extractor
from domain.ingestion.extractor import check_java
from domain.ingestion.images import IMAGE_LINK_RE, image_link, images_dir
from domain.lint.image_checks import missing_image_check, orphan_image_check
from domain.lint.report import LintReport
from domain.lint.runner import lint_wiki
from domain.repair.actions import repair_missing_image, repair_orphan_image
from domain.repair.runner import repair_wiki
from domain.tools.db import get_connection
from domain.tools.wiki_fs import create_page
from tests.helpers.workspace import WorkspaceFixture

REPO = Path(__file__).resolve().parents[2]


def add_source(ws: WorkspaceFixture, filename: str, pages: list[str], *, file: bool = True) -> str:
    doc_id = str(uuid.uuid4())
    with get_connection(ws.db_path) as conn:
        user_id = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()["user_id"]
        with conn:
            conn.execute(
                "INSERT INTO documents (id, user_id, filename, title, path, relative_path, "
                "source_kind, file_type, status, content, page_count) "
                "VALUES (?,?,?,?,'sources/',?,'source','pdf','ready',?,?)",
                (doc_id, user_id, filename, filename, f"sources/{filename}",
                 "\n\n---\n\n".join(pages), len(pages)))
            for n, text in enumerate(pages, 1):
                conn.execute("INSERT INTO document_pages (id, document_id, page, content) VALUES (?,?,?,?)",
                             (str(uuid.uuid4()), doc_id, n, text))
            conn.execute(
                "INSERT INTO document_chunks (id, document_id, chunk_index, content, page, start_char, token_count) "
                "VALUES (?,?,0,?,1,0,5)", (str(uuid.uuid4()), doc_id, pages[0]))
    if file:
        (ws.workspace / "sources" / filename).write_bytes(b"%PDF-1.4")
    return doc_id


def keep(ws: WorkspaceFixture, doc_id: str, *names: str) -> None:
    folder = images_dir(ws.workspace, doc_id)
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"png")


def page_texts(ws: WorkspaceFixture, doc_id: str) -> list[str]:
    with get_connection(ws.db_path) as conn:
        return [r["content"] for r in conn.execute(
            "SELECT content FROM document_pages WHERE document_id = ? ORDER BY page", (doc_id,))]


@pytest.fixture
def fake_extraction(monkeypatch):
    """Extracting images again keeps `imageFile1.png` and `imageFile2.png`."""
    calls = []

    def fake(file_path, cache_dir):
        calls.append(file_path.name)
        folder = cache_dir / "images"
        folder.mkdir(parents=True, exist_ok=True)
        for name in ("imageFile1.png", "imageFile2.png"):
            (folder / name).write_bytes(b"new")

    monkeypatch.setattr(extractor, "extract_images", fake)
    return calls


# ── Missing image ────────────────────────────────────────────────────────────

def test_a_link_to_a_kept_file_is_not_reported(tmp_workspace):
    doc = add_source(tmp_workspace, "a.pdf", ["placeholder"])
    with get_connection(tmp_workspace.db_path) as conn:
        with conn:
            conn.execute("UPDATE document_pages SET content = ?", (image_link(doc, "imageFile1.png", 1),))
    keep(tmp_workspace, doc, "imageFile1.png")
    assert missing_image_check(tmp_workspace.db_path, tmp_workspace.workspace) == []


def test_a_link_to_a_missing_file_in_a_source_is_reported(tmp_workspace):
    doc = "no-such-folder"
    add_source(tmp_workspace, "a.pdf", [image_link(doc, "imageFile1.png", 1), "plain"])
    issues = missing_image_check(tmp_workspace.db_path, tmp_workspace.workspace)
    assert [(i.check, i.page) for i in issues] == [("missing_image", "sources/a.pdf")]
    assert "1 image link" in issues[0].description


def test_the_old_link_form_counts_as_missing(tmp_workspace):
    add_source(tmp_workspace, "a.pdf", ["![image](a_images/imageFile1.png)"])
    assert len(missing_image_check(tmp_workspace.db_path, tmp_workspace.workspace)) == 1


def test_a_link_in_a_wiki_page_is_reported(tmp_workspace):
    ws = tmp_workspace
    link = image_link("gone", "imageFile3.png", 4)
    create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "x", "X", f"Body.\n\n{link}\n", [])
    issues = missing_image_check(ws.db_path, ws.workspace)
    assert [(i.check, i.page) for i in issues] == [("missing_image", "/wiki/concepts/x.md")]


def test_repair_extracts_again_and_rewrites_the_old_links(tmp_workspace, fake_extraction):
    ws = tmp_workspace
    doc = add_source(ws, "a.pdf", ["One ![image](a_images/imageFile1.png)",
                                   "Two ![image](a_images/imageFile2.png) end"])
    issue = missing_image_check(ws.db_path, ws.workspace)[0]
    result = repair_missing_image(issue, ws.db_path, ws.workspace)

    assert result.success and result.action == "images_restored", result.message
    assert fake_extraction == ["a.pdf"]
    assert page_texts(ws, doc) == [
        f"One {image_link(doc, 'imageFile1.png', 1)}",
        f"Two {image_link(doc, 'imageFile2.png', 2)} end",
    ]
    with get_connection(ws.db_path) as conn:
        content = conn.execute("SELECT content FROM documents WHERE id = ?", (doc,)).fetchone()["content"]
        chunk = conn.execute("SELECT content FROM document_chunks WHERE document_id = ?", (doc,)).fetchone()
    assert image_link(doc, "imageFile2.png", 2) in content
    assert chunk["content"] == f"One {image_link(doc, 'imageFile1.png', 1)}"
    assert missing_image_check(ws.db_path, ws.workspace) == []


def test_repair_replaces_the_links_of_a_source_that_is_gone(tmp_workspace, fake_extraction):
    ws = tmp_workspace
    doc = add_source(ws, "a.pdf", ["x", f"Look {image_link('old-id', 'imageFile1.png', 2)} and "
                                       "![image](a_images/imageFile2.png)"], file=False)
    issue = missing_image_check(ws.db_path, ws.workspace)[0]
    result = repair_missing_image(issue, ws.db_path, ws.workspace)

    assert result.success and result.action == "links_replaced"
    assert fake_extraction == []
    assert page_texts(ws, doc)[1] == "Look [Imagen no disponible, página 2] and [Imagen no disponible, página 2]"
    assert missing_image_check(ws.db_path, ws.workspace) == []


def test_repair_fails_and_changes_nothing_when_the_extraction_fails(tmp_workspace, monkeypatch):
    ws = tmp_workspace
    doc = add_source(ws, "a.pdf", ["![image](a_images/imageFile1.png)"])

    def boom(file_path, cache_dir):
        raise RuntimeError("Java missing")

    monkeypatch.setattr(extractor, "extract_images", boom)
    issue = missing_image_check(ws.db_path, ws.workspace)[0]
    result = repair_missing_image(issue, ws.db_path, ws.workspace)
    assert not result.success and "Java missing" in result.message
    assert page_texts(ws, doc) == ["![image](a_images/imageFile1.png)"]


def test_repair_of_a_wiki_page_restores_or_replaces_by_source(tmp_workspace, fake_extraction):
    ws = tmp_workspace
    doc = add_source(ws, "a.pdf", ["plain"])
    gone = add_source(ws, "b.pdf", ["plain"], file=False)
    create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "x", "X",
                f"Body.\n\n{image_link(doc, 'imageFile2.png', 3)}\n\n{image_link(gone, 'imageFile1.png', 5)}\n", [])
    issue = missing_image_check(ws.db_path, ws.workspace)[0]
    result = repair_missing_image(issue, ws.db_path, ws.workspace)
    text = (ws.workspace / "wiki" / "concepts" / "x.md").read_text(encoding="utf-8")
    assert result.success
    assert image_link(doc, "imageFile2.png", 3) in text
    assert "[Imagen no disponible, página 5]" in text


# ── Orphan image ─────────────────────────────────────────────────────────────

def test_a_file_that_no_link_uses_is_an_orphan_and_is_deleted(tmp_workspace):
    ws = tmp_workspace
    doc = add_source(ws, "a.pdf", ["x"])
    with get_connection(ws.db_path) as conn:
        with conn:
            conn.execute("UPDATE document_pages SET content = ?", (image_link(doc, "imageFile1.png", 1),))
    keep(ws, doc, "imageFile1.png", "imageFile2.png")

    issues = orphan_image_check(ws.db_path, ws.workspace)
    assert [(i.check, i.page, i.target) for i in issues] == [("orphan_image", "sources/a.pdf", "imageFile2.png")]
    result = repair_orphan_image(issues[0], ws.db_path, ws.workspace)
    assert result.action == "deleted"
    assert sorted(p.name for p in images_dir(ws.workspace, doc).iterdir()) == ["imageFile1.png"]
    assert orphan_image_check(ws.db_path, ws.workspace) == []


def test_a_link_in_a_wiki_page_keeps_the_file(tmp_workspace):
    ws = tmp_workspace
    doc = add_source(ws, "a.pdf", ["x"])
    keep(ws, doc, "imageFile1.png")
    create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "x", "X",
                f"Body.\n\n{image_link(doc, 'imageFile1.png', 1)}\n", [])
    assert orphan_image_check(ws.db_path, ws.workspace) == []


def test_the_orphan_repair_keeps_a_file_that_a_link_uses_now(tmp_workspace):
    """A missing-image repair that ran first may have linked the file."""
    ws = tmp_workspace
    doc = add_source(ws, "a.pdf", ["x"])
    keep(ws, doc, "imageFile1.png")
    issue = orphan_image_check(ws.db_path, ws.workspace)[0]
    with get_connection(ws.db_path) as conn:
        with conn:
            conn.execute("UPDATE document_pages SET content = ?", (image_link(doc, "imageFile1.png", 1),))
    result = repair_orphan_image(issue, ws.db_path, ws.workspace)
    assert result.action == "skipped"
    assert (images_dir(ws.workspace, doc) / "imageFile1.png").exists()


def test_the_runner_runs_both_checks_and_repairs_them(tmp_workspace, fake_extraction):
    ws = tmp_workspace
    doc = add_source(ws, "a.pdf", ["![image](a_images/imageFile1.png)"])
    keep(ws, doc, "stray.png")
    report = lint_wiki(ws.db_path, ws.workspace)
    assert {"missing_image", "orphan_image"} <= {i.check for i in report.issues}
    repaired = repair_wiki(LintReport(issues=[i for i in report.issues if i.check.endswith("_image")]),
                           ws.db_path, ws.workspace)
    assert not repaired.failed
    # The stray file is deleted; the fake extraction wrote the two images.
    assert sorted(p.name for p in images_dir(ws.workspace, doc).iterdir()) == ["imageFile1.png", "imageFile2.png"]


# ── The demo of the repository ───────────────────────────────────────────────

@pytest.mark.skipif(check_java() is None, reason="Java is missing: opendataloader-pdf needs it")
def test_the_repair_resolves_the_ten_links_of_snow_white_in_a_copy_of_the_demo(tmp_path):
    copy = tmp_path / "fairy-tales"
    shutil.copytree(REPO / "examples" / "fairy-tales", copy)
    db_path = str(copy / ".llmwiki" / "index.db")
    name = "Snow White and the Seven Dwarfs.pdf"

    issues = [i for i in lint_wiki(db_path, copy).issues if i.check == "missing_image"]
    mine = [i for i in issues if i.page == f"sources/{name}"]
    assert len(mine) == 1 and "10 image link" in mine[0].description

    result = repair_missing_image(mine[0], db_path, copy)
    assert result.success, result.message

    with get_connection(db_path) as conn:
        doc_id = conn.execute("SELECT id FROM documents WHERE filename = ?", (name,)).fetchone()["id"]
        texts = [r["content"] for r in conn.execute(
            "SELECT content FROM document_pages WHERE document_id = ?", (doc_id,))]
    links = [m for t in texts for m in IMAGE_LINK_RE.finditer(t)]
    assert len(links) == 10
    assert len({m.group("file") for m in links}) == 10
    assert all((images_dir(copy, doc_id) / m.group("file")).is_file() for m in links)
    assert not [i for i in missing_image_check(db_path, copy) if i.page == f"sources/{name}"]
    assert not [i for i in orphan_image_check(db_path, copy) if i.page == f"sources/{name}"]
