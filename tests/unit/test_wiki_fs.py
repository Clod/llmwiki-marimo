"""Tests for domain/tools/wiki_fs.py — create_page (1.1), read_page + append_to_page (1.2)."""

import pytest

from domain.tools.db import get_connection
from domain.tools.wiki_fs import create_page, delete_page, read_page
from tests.helpers.workspace import WorkspaceFixture

_CONTENT = (
    "# My Page\n\n"
    "This page covers important financial concepts related to investment strategies "
    "in emerging markets, including equities, bonds, and alternative assets. "
    "It provides a structured overview of key themes and entities.\n"
)


def test_create_page_writes_file(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "my-page", "My Page", _CONTENT, [],
    )
    expected = tmp_workspace.workspace / "wiki" / "summaries" / "my-page.md"
    assert expected.exists()
    assert expected.read_text() == _CONTENT


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
    assert file_path.read_text() == new_content


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
    assert result == _CONTENT


def test_read_page_nonexistent_returns_none(tmp_workspace: WorkspaceFixture) -> None:
    result = read_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/summaries/", "ghost")
    assert result is None


def test_read_page_normalizes_dir_path(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "wiki/summaries", "my-page", "My Page", _CONTENT, [],
    )
    result = read_page(tmp_workspace.db_path, tmp_workspace.workspace, "wiki/summaries", "my-page")
    assert result == _CONTENT


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
