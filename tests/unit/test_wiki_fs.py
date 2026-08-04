"""Tests for domain/tools/wiki_fs.py — create_page (1.1), read_page + append_to_page (1.2)."""

import pytest

from domain.tools.db import get_connection
from domain.tools.wiki_fs import create_page, delete_page, read_page
from tests.helpers.workspace import WorkspaceFixture

_LONG = "word " * 50  # padding to exceed MIN_CHUNK_TOKENS

_CONTENT = (
    "# My Page\n\n"
    "This page covers important financial concepts related to investment strategies "
    "in emerging markets, including equities, bonds, and alternative assets. "
    "It provides a structured overview of key themes and entities.\n"
)


def test_create_page_writes_file(tmp_workspace: WorkspaceFixture) -> None:
    """create_page now prepends a code-rendered frontmatter block (type/title/tags/
    sources) — see domain/datasets/frontmatter.py:render_frontmatter — so the body
    is checked via split_frontmatter rather than a byte-exact file match."""
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", _CONTENT, [],
    )
    expected = tmp_workspace.workspace / "wiki" / "summaries" / "my-page.md"
    assert expected.exists()
    text = expected.read_text()
    assert text.startswith("---\n")
    # split_frontmatter's own lines-based reconstruction drops a final trailing
    # newline, so verify against the raw bytes on disk with endswith rather
    # than round-tripping the body back through split_frontmatter.
    assert text.endswith(_CONTENT)


def test_create_page_db_row(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", _CONTENT, ["finance"],
    )
    with get_connection(tmp_workspace.db_path) as conn:
        row = conn.execute(
            "SELECT source_kind, status, path, relative_path, title, file_type "
            "FROM documents WHERE relative_path = ?",
            ("wiki/summaries/my-page.md",),
        ).fetchone()
    assert row is not None
    assert row["source_kind"] == "wiki"
    assert row["status"] == "ready"
    assert row["path"] == "/wiki/summaries/"
    assert row["relative_path"] == "wiki/summaries/my-page.md"
    assert row["title"] == "My Page"
    assert row["file_type"] == "md"


def test_create_page_creates_fts5_chunks(tmp_workspace: WorkspaceFixture) -> None:
    result = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", _CONTENT, [],
    )
    with get_connection(tmp_workspace.db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE document_id = ?",
            (result["id"],),
        ).fetchone()[0]
    assert count > 0


def test_create_page_returns_id_and_path(tmp_workspace: WorkspaceFixture) -> None:
    result = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", _CONTENT, [],
    )
    assert "id" in result
    assert result["path"] == "wiki/summaries/my-page.md"


def test_create_page_duplicate_raises(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", _CONTENT, [],
    )
    with pytest.raises(FileExistsError):
        create_page(
            tmp_workspace.db_path, tmp_workspace.workspace,
            "/wiki/summaries/", "my-page", "My Page", _CONTENT, [],
        )


def test_create_page_overwrite_updates_content(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", _CONTENT, [],
    )
    new_content = "# My Page\n\nUpdated content.\n"
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", new_content, [],
        overwrite=True,
    )
    file_path = tmp_workspace.workspace / "wiki" / "summaries" / "my-page.md"
    assert file_path.read_text().endswith(new_content)


def test_create_page_overwrite_increments_version(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", _CONTENT, [],
    )
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", "# Updated\n", [],
        overwrite=True,
    )
    with get_connection(tmp_workspace.db_path) as conn:
        row = conn.execute(
            "SELECT version FROM documents WHERE relative_path = ?",
            ("wiki/summaries/my-page.md",),
        ).fetchone()
    assert row["version"] == 1


def test_create_page_overwrite_replaces_chunks(tmp_workspace: WorkspaceFixture) -> None:
    _LONG = (
        "# Updated Page\n\n"
        "Entirely different content about monetary policy, central banks, "
        "interest rates, and inflation targeting frameworks used globally.\n"
    )
    result = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", _CONTENT, [],
    )
    with get_connection(tmp_workspace.db_path) as conn:
        old_ids = {
            r[0] for r in conn.execute(
                "SELECT id FROM document_chunks WHERE document_id = ?",
                (result["id"],),
            ).fetchall()
        }
    assert old_ids  # baseline: chunks exist

    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", _LONG, [],
        overwrite=True,
    )
    with get_connection(tmp_workspace.db_path) as conn:
        new_ids = {
            r[0] for r in conn.execute(
                "SELECT id FROM document_chunks WHERE document_id = ?",
                (result["id"],),
            ).fetchall()
        }
    # Old chunks deleted, new ones inserted — no overlap in IDs
    assert new_ids
    assert old_ids.isdisjoint(new_ids)


def test_create_page_normalizes_dir_path(tmp_workspace: WorkspaceFixture) -> None:
    # Callers may omit leading/trailing slashes — should still work
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "wiki/concepts", "bitcoin", "Bitcoin", _CONTENT, [],
    )
    expected = tmp_workspace.workspace / "wiki" / "concepts" / "bitcoin.md"
    assert expected.exists()


# ── Step 1.2: read_page + append_to_page ─────────────────────────────────────

from domain.tools.wiki_fs import append_to_page  # noqa: E402

_APPEND = (
    "## New Section\n\n"
    "Additional analysis covering macroeconomic indicators and their relationship "
    "to asset class performance across different market cycles.\n"
)


def test_read_page_returns_content(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", _CONTENT, [],
    )
    result = read_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/summaries/", "my-page")
    assert result.endswith(_CONTENT)


def test_read_page_nonexistent_returns_none(tmp_workspace: WorkspaceFixture) -> None:
    result = read_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/summaries/", "ghost")
    assert result is None


def test_read_page_normalizes_dir_path(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "wiki/summaries", "my-page", "My Page", _CONTENT, [],
    )
    result = read_page(tmp_workspace.db_path, tmp_workspace.workspace, "wiki/summaries", "my-page")
    assert result.endswith(_CONTENT)


def test_append_to_page_updates_file(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", _CONTENT, [],
    )
    append_to_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/summaries/", "my-page", _APPEND)
    file_path = tmp_workspace.workspace / "wiki" / "summaries" / "my-page.md"
    on_disk = file_path.read_text()
    assert _CONTENT.rstrip("\n") in on_disk
    assert _APPEND in on_disk


def test_append_to_page_read_shows_combined(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", _CONTENT, [],
    )
    append_to_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/summaries/", "my-page", _APPEND)
    combined = read_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/summaries/", "my-page")
    assert _CONTENT.rstrip("\n") in combined
    assert _APPEND in combined


def test_append_to_page_increments_version(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", _CONTENT, [],
    )
    append_to_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/summaries/", "my-page", _APPEND)
    with get_connection(tmp_workspace.db_path) as conn:
        row = conn.execute(
            "SELECT version FROM documents WHERE relative_path=?",
            ("wiki/summaries/my-page.md",),
        ).fetchone()
    assert row["version"] == 1


def test_append_to_page_updates_chunks(tmp_workspace: WorkspaceFixture) -> None:
    result = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", _CONTENT, [],
    )
    with get_connection(tmp_workspace.db_path) as conn:
        old_ids = {
            r[0] for r in conn.execute(
                "SELECT id FROM document_chunks WHERE document_id=?", (result["id"],)
            ).fetchall()
        }
    append_to_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/summaries/", "my-page", _APPEND)
    with get_connection(tmp_workspace.db_path) as conn:
        new_ids = {
            r[0] for r in conn.execute(
                "SELECT id FROM document_chunks WHERE document_id=?", (result["id"],)
            ).fetchall()
        }
    assert new_ids
    assert old_ids.isdisjoint(new_ids)


def test_append_to_page_nonexistent_returns_false(tmp_workspace: WorkspaceFixture) -> None:
    result = append_to_page(
        tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/summaries/", "ghost", _APPEND
    )
    assert result is False


# ── delete_page: dead link cleanup ───────────────────────────────────────────

def test_delete_page_strips_dead_links_from_referencing_page(tmp_workspace: WorkspaceFixture) -> None:
    result_a = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "target", "Target Page",
        "# Target Page\n\nThis is the target.\n", [],
    )
    link_content = (
        "# Linking Page\n\n"
        "See [Target Page](wiki/summaries/target.md) for details.\n"
    )
    result_b = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "linker", "Linking Page",
        link_content, [],
    )
    with get_connection(tmp_workspace.db_path) as conn:
        with conn:
            conn.execute(
                "INSERT INTO document_references "
                "(source_document_id, target_document_id, reference_type) "
                "VALUES (?, ?, 'links_to')",
                (result_b["id"], result_a["id"]),
            )

    delete_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/summaries/", "target")

    # Link must be gone from disk
    updated_disk = read_page(
        tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/summaries/", "linker"
    )
    assert "wiki/summaries/target.md" not in updated_disk
    # Label text must be preserved
    assert "Target Page" in updated_disk

    # DB content must also reflect the change
    with get_connection(tmp_workspace.db_path) as conn:
        row = conn.execute(
            "SELECT content FROM documents WHERE id=?", (result_b["id"],)
        ).fetchone()
    assert "wiki/summaries/target.md" not in row["content"]
    assert "Target Page" in row["content"]


def test_delete_page_strips_links_written_the_way_pages_actually_write_them(
    tmp_workspace: WorkspaceFixture,
) -> None:
    """Generated pages link by *relative* href, not by full path.

    Two pages in the same folder link to each other as `[Title](other.md)`, and
    across folders as `[Title](../summaries/other.md)` — that is what
    `inject_see_also` and `repair_missing_xref` emit, and what all 26 links in the
    fairy-tale demo look like. The full `wiki/...` form the other tests use is
    never produced by the pipeline.

    Matching only the full form meant deleting a page left a broken link in every
    page that pointed at it.
    """
    target = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "target", "Target Page",
        f"# Target Page\n\n{_LONG}\n", [],
    )
    # same folder → bare basename
    sibling = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "sibling", "Sibling",
        f"# Sibling\n\n{_LONG}\n\n## See also\n\n- [Target Page](target.md)\n", [],
    )
    # other folder → ../concepts/…
    cousin = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "cousin", "Cousin",
        f"# Cousin\n\n{_LONG}\n\n## See also\n\n- [Target Page](../concepts/target.md)\n", [],
    )
    with get_connection(tmp_workspace.db_path) as conn:
        with conn:
            for src in (sibling["id"], cousin["id"]):
                conn.execute(
                    "INSERT INTO document_references "
                    "(source_document_id, target_document_id, reference_type) "
                    "VALUES (?, ?, 'links_to')",
                    (src, target["id"]),
                )

    delete_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/concepts/", "target")

    same_dir = read_page(
        tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/concepts/", "sibling")
    cross_dir = read_page(
        tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/summaries/", "cousin")

    assert "(target.md)" not in same_dir, f"same-folder link survived:\n{same_dir}"
    assert "(../concepts/target.md)" not in cross_dir, f"cross-folder link survived:\n{cross_dir}"
    # the label text stays, so the sentence still reads
    assert "Target Page" in same_dir and "Target Page" in cross_dir


def test_delete_page_removes_the_entry_from_index_md(
    tmp_workspace: WorkspaceFixture,
) -> None:
    """index.md is the catalogue a reader browses; a deleted page must leave it.

    Nothing else prunes it — index.md has no row in `documents`, so no cascade
    reaches it — and an entry pointing at a file that is gone is a broken link in
    the one page whose whole job is to list what exists.
    """
    from domain.ingestion.index_manager import update_index

    page = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "doomed", "Doomed Page",
        f"# Doomed Page\n\n{_LONG}\n", [],
    )
    update_index(tmp_workspace.workspace, page["path"], "A page about to go", "concepts")
    index_path = tmp_workspace.workspace / "wiki" / "index.md"
    assert "doomed.md" in index_path.read_text(encoding="utf-8")

    delete_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/concepts/", "doomed")

    assert "doomed.md" not in index_path.read_text(encoding="utf-8"), (
        "the deleted page is still listed in index.md:\n"
        + index_path.read_text(encoding="utf-8")
    )


def test_delete_page_strips_absolute_wiki_links(tmp_workspace: WorkspaceFixture) -> None:
    result_a = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "target", "Target Page",
        "# Target Page\n\nContent.\n", [],
    )
    link_content = (
        "# Linker\n\n"
        "See [Target Page](/wiki/summaries/target.md) here.\n"
    )
    result_b = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "linker", "Linker",
        link_content, [],
    )
    with get_connection(tmp_workspace.db_path) as conn:
        with conn:
            conn.execute(
                "INSERT INTO document_references "
                "(source_document_id, target_document_id, reference_type) "
                "VALUES (?, ?, 'links_to')",
                (result_b["id"], result_a["id"]),
            )

    delete_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/summaries/", "target")

    updated_disk = read_page(
        tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/summaries/", "linker"
    )
    assert "/wiki/summaries/target.md" not in updated_disk
    assert "Target Page" in updated_disk
