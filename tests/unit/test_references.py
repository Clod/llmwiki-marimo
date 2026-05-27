"""Tests for domain/tools/references.py — step 1.4."""

import uuid

from domain.tools.db import get_connection
from domain.tools.references import (
    find_orphan_pages,
    find_uncited_sources,
    get_backlinks,
    get_forward_refs,
    update_references,
)
from domain.tools.wiki_fs import create_page
from tests.helpers.workspace import WorkspaceFixture

_WIKI_CONTENT = (
    "# Federal Reserve\n\n"
    "The Federal Reserve is the central banking system of the United States, "
    "responsible for monetary policy and financial stability.\n\n"
)


def _insert_source(db_path: str, filename: str, title: str) -> str:
    """Insert a minimal source document. Returns its ID."""
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
                "file_type, status, content, document_number) "
                "VALUES (?,?,?,?,'sources/',?,'source','pdf','ready','',?)",
                (doc_id, user_id, filename, title, f"sources/{filename}", doc_number),
            )
    return doc_id


# ── update_references + citation parsing ─────────────────────────────────────

def test_update_references_creates_cites_edge(tmp_workspace: WorkspaceFixture) -> None:
    source_id = _insert_source(tmp_workspace.db_path, "fed-paper.pdf", "Fed Paper")
    content = _WIKI_CONTENT + "[^1]: fed-paper.pdf\n"
    result = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "federal-reserve", "Federal Reserve", content, [],
    )
    update_references(tmp_workspace.db_path, result["id"], content, "/wiki/summaries/")

    with get_connection(tmp_workspace.db_path) as conn:
        edge = conn.execute(
            "SELECT reference_type FROM document_references "
            "WHERE source_document_id=? AND target_document_id=?",
            (result["id"], source_id),
        ).fetchone()
    assert edge is not None
    assert edge["reference_type"] == "cites"


def test_update_references_creates_cites_edge_from_plain_bullet(
    tmp_workspace: WorkspaceFixture,
) -> None:
    """Regression (H1): concept pages list sources as plain '- file.pdf' bullets,
    not '[^1]: file.pdf' footnotes. The parser must build cites edges from these."""
    source_id = _insert_source(tmp_workspace.db_path, "Cenicienta.pdf", "Cenicienta")
    content = (
        "# Cinderella\n\n"
        "## Definition\nA folk tale about a mistreated young woman.\n\n"
        "## Sources\n- Cenicienta.pdf\n"
    )
    result = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "cinderella", "Cinderella", content, [],
    )
    update_references(tmp_workspace.db_path, result["id"], content, "/wiki/concepts/")

    with get_connection(tmp_workspace.db_path) as conn:
        edge = conn.execute(
            "SELECT reference_type FROM document_references "
            "WHERE source_document_id=? AND target_document_id=?",
            (result["id"], source_id),
        ).fetchone()
    assert edge is not None
    assert edge["reference_type"] == "cites"


def test_update_references_parses_plain_bullet_with_page(
    tmp_workspace: WorkspaceFixture,
) -> None:
    """A plain Sources bullet may carry a page suffix: '- file.pdf, p.3'."""
    source_id = _insert_source(tmp_workspace.db_path, "Cenicienta.pdf", "Cenicienta")
    content = "# Cinderella\n\n## Sources\n- Cenicienta.pdf, p.3\n"
    page = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "cinderella", "Cinderella", content, [],
    )
    update_references(tmp_workspace.db_path, page["id"], content, "/wiki/concepts/")

    with get_connection(tmp_workspace.db_path) as conn:
        edge = conn.execute(
            "SELECT reference_type, page FROM document_references "
            "WHERE source_document_id=? AND target_document_id=?",
            (page["id"], source_id),
        ).fetchone()
    assert edge is not None
    assert edge["reference_type"] == "cites"
    assert edge["page"] == 3


def test_update_references_ignores_bullets_outside_sources(
    tmp_workspace: WorkspaceFixture,
) -> None:
    """Plain bullets only count as citations under the '## Sources' heading."""
    source_id = _insert_source(tmp_workspace.db_path, "Cenicienta.pdf", "Cenicienta")
    content = (
        "# Cinderella\n\n"
        "## Key Characteristics\n- Cenicienta.pdf\n\n"  # bullet NOT under Sources
        "## Sources\n- other-file.pdf\n"
    )
    page = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "cinderella", "Cinderella", content, [],
    )
    update_references(tmp_workspace.db_path, page["id"], content, "/wiki/concepts/")

    with get_connection(tmp_workspace.db_path) as conn:
        edge = conn.execute(
            "SELECT 1 FROM document_references "
            "WHERE source_document_id=? AND target_document_id=?",
            (page["id"], source_id),
        ).fetchone()
    assert edge is None  # Cenicienta.pdf appeared only outside Sources → no cites edge


def test_update_references_creates_links_to_edge(tmp_workspace: WorkspaceFixture) -> None:
    page_b = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "inflation", "Inflation",
        "# Inflation\n\nInflation is the rate at which prices rise over time.\n", [],
    )
    content = _WIKI_CONTENT + "\nSee also [Inflation](/wiki/summaries/inflation.md).\n"
    page_a = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "federal-reserve", "Federal Reserve", content, [],
    )
    update_references(tmp_workspace.db_path, page_a["id"], content, "/wiki/summaries/")

    with get_connection(tmp_workspace.db_path) as conn:
        edge = conn.execute(
            "SELECT reference_type FROM document_references "
            "WHERE source_document_id=? AND target_document_id=?",
            (page_a["id"], page_b["id"]),
        ).fetchone()
    assert edge is not None
    assert edge["reference_type"] == "links_to"


def test_update_references_rebuilds_on_second_call(tmp_workspace: WorkspaceFixture) -> None:
    _insert_source(tmp_workspace.db_path, "old-paper.pdf", "Old Paper")
    content_v1 = _WIKI_CONTENT + "[^1]: old-paper.pdf\n"
    result = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "federal-reserve", "Federal Reserve", content_v1, [],
    )
    update_references(tmp_workspace.db_path, result["id"], content_v1, "/wiki/summaries/")

    # Remove citation — second call should delete the old edge
    content_v2 = _WIKI_CONTENT
    update_references(tmp_workspace.db_path, result["id"], content_v2, "/wiki/summaries/")

    with get_connection(tmp_workspace.db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM document_references WHERE source_document_id=?",
            (result["id"],),
        ).fetchone()[0]
    assert count == 0


# ── get_backlinks / get_forward_refs ─────────────────────────────────────────

def test_get_backlinks_returns_citing_page(tmp_workspace: WorkspaceFixture) -> None:
    source_id = _insert_source(tmp_workspace.db_path, "paper.pdf", "Paper")
    content = _WIKI_CONTENT + "[^1]: paper.pdf\n"
    page = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "federal-reserve", "Federal Reserve", content, [],
    )
    update_references(tmp_workspace.db_path, page["id"], content, "/wiki/summaries/")

    backlinks = get_backlinks(tmp_workspace.db_path, source_id)
    assert len(backlinks) == 1
    assert backlinks[0]["id"] == page["id"]
    assert backlinks[0]["reference_type"] == "cites"


def test_get_forward_refs_returns_cited_source(tmp_workspace: WorkspaceFixture) -> None:
    source_id = _insert_source(tmp_workspace.db_path, "paper.pdf", "Paper")
    content = _WIKI_CONTENT + "[^1]: paper.pdf\n"
    page = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "federal-reserve", "Federal Reserve", content, [],
    )
    update_references(tmp_workspace.db_path, page["id"], content, "/wiki/summaries/")

    forward = get_forward_refs(tmp_workspace.db_path, page["id"])
    assert len(forward) == 1
    assert forward[0]["id"] == source_id
    assert forward[0]["reference_type"] == "cites"


# ── find_orphan_pages ─────────────────────────────────────────────────────────

def test_find_orphan_pages_returns_uncited_wiki(tmp_workspace: WorkspaceFixture) -> None:
    # Three wiki pages — only one gets cited by another
    page_a = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "page-a", "Page A", _WIKI_CONTENT, [],
    )
    page_b_content = _WIKI_CONTENT + "\nSee [Page A](/wiki/summaries/page-a.md).\n"
    page_b = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "page-b", "Page B", page_b_content, [],
    )
    page_c = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "page-c", "Page C", _WIKI_CONTENT, [],
    )
    update_references(tmp_workspace.db_path, page_b["id"], page_b_content, "/wiki/summaries/")

    orphans = find_orphan_pages(tmp_workspace.db_path)
    orphan_ids = {o["id"] for o in orphans}
    assert page_a["id"] not in orphan_ids   # page-a is linked by page-b
    assert page_b["id"] in orphan_ids       # page-b has no inbound links
    assert page_c["id"] in orphan_ids       # page-c has no inbound links


# ── find_uncited_sources ──────────────────────────────────────────────────────

def test_find_uncited_sources_returns_uncited(tmp_workspace: WorkspaceFixture) -> None:
    cited_id = _insert_source(tmp_workspace.db_path, "cited.pdf", "Cited Paper")
    uncited_id = _insert_source(tmp_workspace.db_path, "uncited.pdf", "Uncited Paper")

    content = _WIKI_CONTENT + "[^1]: cited.pdf\n"
    page = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "federal-reserve", "Federal Reserve", content, [],
    )
    update_references(tmp_workspace.db_path, page["id"], content, "/wiki/summaries/")

    uncited = find_uncited_sources(tmp_workspace.db_path)
    uncited_ids = {u["id"] for u in uncited}
    assert cited_id not in uncited_ids
    assert uncited_id in uncited_ids
