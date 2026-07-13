"""Tests for thin_page_check — Piece 7: source chunks left uncovered by the wiki.

The ruler is orphan chunks, NOT page size (a good summary is deliberately short).
A source page-chunk is an "orphan" when almost none of its content words appear in
any wiki page citing that source; many orphans → the wiki under-covers the source.
Reuses the same lexical `overlap.coverage` that verifies Tier-2 answers. No LLM.
"""

import uuid

from domain.lint.checks import thin_page_check
from domain.tools.db import get_connection
from domain.tools.wiki_fs import create_page
from tests.helpers.workspace import WorkspaceFixture

# Four chunks with distinctive, non-overlapping vocabulary so coverage is clear-cut.
_CHUNK_PF = "plazo fijo tradicional interes nominal deposito bancario acreditacion"
_CHUNK_CAUCION = "caucion bursatil garantia colateral mercado operacion tomadora"
_CHUNK_ON = "obligaciones negociables corporativas emisor cupon renta amortizacion"
_CHUNK_FCI = "fondos money market liquidez rescate cuotaparte administradora"


def _insert_source_with_chunks(db_path: str, filename: str, chunks: list[str]) -> str:
    doc_id = str(uuid.uuid4())
    with get_connection(db_path) as conn:
        user_id = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()["user_id"]
        doc_number = conn.execute(
            "SELECT COALESCE(MAX(document_number), 0) + 1 FROM documents"
        ).fetchone()[0]
        with conn:
            conn.execute(
                "INSERT INTO documents "
                "(id, user_id, filename, title, path, relative_path, source_kind, "
                "file_type, status, content, page_count, document_number) "
                "VALUES (?,?,?,?,'sources/',?,'source','pdf','ready','',?,?)",
                (doc_id, user_id, filename, filename, f"sources/{filename}",
                 len(chunks), doc_number),
            )
            for i, chunk in enumerate(chunks, start=1):
                conn.execute(
                    "INSERT INTO document_pages (id, document_id, page, content) "
                    "VALUES (?,?,?,?)",
                    (str(uuid.uuid4()), doc_id, i, chunk),
                )
    return doc_id


def _cite(db_path: str, wiki_id: str, source_id: str) -> None:
    with get_connection(db_path) as conn:
        with conn:
            conn.execute(
                "INSERT INTO document_references "
                "(source_document_id, target_document_id, reference_type) "
                "VALUES (?,?,'cites')",
                (wiki_id, source_id),
            )


def _summary(db_path, workspace, slug, content, source_id):
    page = create_page(
        db_path, workspace, "/wiki/summaries/", slug, slug.title(), content, [],
        overwrite=True, source_document_id=source_id,
    )
    _cite(db_path, page["id"], source_id)
    return page


def _concept(db_path, workspace, slug, content):
    return create_page(
        db_path, workspace, "/wiki/concepts/", slug, slug.title(), content, [],
        overwrite=True,
    )


def _link(db_path, from_id, to_id):
    """A links_to edge — a summary page pointing at a concept it spawned."""
    with get_connection(db_path) as conn:
        with conn:
            conn.execute(
                "INSERT INTO document_references "
                "(source_document_id, target_document_id, reference_type) "
                "VALUES (?,?,'links_to')",
                (from_id, to_id),
            )


def test_thin_page_flags_when_most_chunks_orphaned(tmp_workspace: WorkspaceFixture) -> None:
    src = _insert_source_with_chunks(
        tmp_workspace.db_path, "instrumentos.pdf",
        [_CHUNK_PF, _CHUNK_CAUCION, _CHUNK_ON, _CHUNK_FCI],
    )
    # Summary covers ONLY the plazo-fijo chunk → 3 of 4 chunks are orphaned.
    _summary(tmp_workspace.db_path, tmp_workspace.workspace, "instrumentos",
             f"# Instrumentos\n\n{_CHUNK_PF}\n", src)

    issues = thin_page_check(tmp_workspace.db_path)
    thin = [i for i in issues if i.check == "thin_page"]
    assert len(thin) == 1
    assert thin[0].severity == "warning"
    assert "instrumentos" in thin[0].page


def test_thin_page_no_flag_when_pages_cover_source(tmp_workspace: WorkspaceFixture) -> None:
    src = _insert_source_with_chunks(
        tmp_workspace.db_path, "instrumentos.pdf",
        [_CHUNK_PF, _CHUNK_CAUCION, _CHUNK_ON, _CHUNK_FCI],
    )
    # A rich summary that reflects every chunk → no orphans.
    rich = "\n".join(["# Instrumentos", _CHUNK_PF, _CHUNK_CAUCION, _CHUNK_ON, _CHUNK_FCI])
    _summary(tmp_workspace.db_path, tmp_workspace.workspace, "instrumentos", rich, src)

    issues = thin_page_check(tmp_workspace.db_path)
    assert not any(i.check == "thin_page" for i in issues)


def test_thin_page_no_flag_below_min_chunks(tmp_workspace: WorkspaceFixture) -> None:
    # Only 2 chunks (< the minimum) → a ratio is meaningless; never flag.
    src = _insert_source_with_chunks(
        tmp_workspace.db_path, "corto.pdf", [_CHUNK_PF, _CHUNK_CAUCION],
    )
    _summary(tmp_workspace.db_path, tmp_workspace.workspace, "corto",
             "# Corto\n\nnada que ver\n", src)

    issues = thin_page_check(tmp_workspace.db_path)
    assert not any(i.check == "thin_page" for i in issues)


def test_thin_page_no_flag_without_citing_page(tmp_workspace: WorkspaceFixture) -> None:
    # Source with orphan-prone chunks but NO wiki page citing it → nothing to judge.
    _insert_source_with_chunks(
        tmp_workspace.db_path, "huerfano.pdf",
        [_CHUNK_PF, _CHUNK_CAUCION, _CHUNK_ON, _CHUNK_FCI],
    )
    issues = thin_page_check(tmp_workspace.db_path)
    assert not any(i.check == "thin_page" for i in issues)


def test_thin_page_attaches_issue_to_summary_page(tmp_workspace: WorkspaceFixture) -> None:
    src = _insert_source_with_chunks(
        tmp_workspace.db_path, "instrumentos.pdf",
        [_CHUNK_PF, _CHUNK_CAUCION, _CHUNK_ON, _CHUNK_FCI],
    )
    _summary(tmp_workspace.db_path, tmp_workspace.workspace, "instrumentos",
             f"# Instrumentos\n\n{_CHUNK_PF}\n", src)

    issues = thin_page_check(tmp_workspace.db_path)
    thin = [i for i in issues if i.check == "thin_page"]
    assert thin and thin[0].page == "/wiki/summaries/instrumentos.md"


def test_thin_page_no_flag_when_linked_concepts_cover_source(tmp_workspace: WorkspaceFixture) -> None:
    # The summary is a short overview that reflects NONE of the chunks, but the
    # concept pages it links to DO cover them. Coverage must count those concepts,
    # or the detector false-alarms on every doc whose detail lives in concepts.
    db, ws = tmp_workspace.db_path, tmp_workspace.workspace
    src = _insert_source_with_chunks(
        db, "instrumentos.pdf", [_CHUNK_PF, _CHUNK_CAUCION, _CHUNK_ON, _CHUNK_FCI],
    )
    summ = _summary(db, ws, "instrumentos", "# Instrumentos\n\nUn panorama breve del documento.\n", src)
    for slug, chunk in [("plazo-fijo", _CHUNK_PF), ("caucion-b", _CHUNK_CAUCION),
                        ("on-corp", _CHUNK_ON), ("fci-rf", _CHUNK_FCI)]:
        concept = _concept(db, ws, slug, chunk)
        _link(db, summ["id"], concept["id"])

    issues = thin_page_check(db)
    assert not any(i.check == "thin_page" for i in issues)


def test_thin_page_flags_when_neither_summary_nor_linked_concepts_cover(tmp_workspace: WorkspaceFixture) -> None:
    # Guard: linked concepts that are about unrelated things must NOT rescue a
    # genuinely thin page — the chunks are still orphaned everywhere.
    db, ws = tmp_workspace.db_path, tmp_workspace.workspace
    src = _insert_source_with_chunks(
        db, "instrumentos.pdf", [_CHUNK_PF, _CHUNK_CAUCION, _CHUNK_ON, _CHUNK_FCI],
    )
    summ = _summary(db, ws, "instrumentos", "# Instrumentos\n\nUn panorama breve del documento.\n", src)
    for slug, txt in [("otro-1", "asunto ajeno alfa omega sigma"),
                      ("otro-2", "cuestion distinta lambda kappa theta")]:
        concept = _concept(db, ws, slug, txt)
        _link(db, summ["id"], concept["id"])

    issues = thin_page_check(db)
    assert any(i.check == "thin_page" for i in issues)
