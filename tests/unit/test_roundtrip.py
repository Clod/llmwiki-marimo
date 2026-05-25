"""Phase 1.6 — Full round-trip integration test for all native tools.

Exercises create_page, read_page, append_to_page, search_chunks,
update_references, and get_backlinks in a realistic sequence.
Serves as the regression baseline before Phase 2 changes the pipeline.
"""

import uuid

from domain.tools.db import get_connection
from domain.tools.references import get_backlinks, update_references
from domain.tools.search import search_chunks
from domain.tools.wiki_fs import append_to_page, create_page, read_page
from tests.helpers.workspace import WorkspaceFixture

_SUMMARY_CONTENT = (
    "# Quantitative Easing\n\n"
    "Quantitative easing (QE) is a monetary policy tool used by central banks "
    "to stimulate the economy by purchasing government securities and other "
    "financial assets, thereby increasing the money supply.\n\n"
    "The Federal Reserve deployed QE extensively after the 2008 financial crisis "
    "and again during the COVID-19 pandemic to support economic recovery.\n\n"
    "[^1]: qe-study.pdf\n"
    "See also [Federal Reserve](/wiki/concepts/federal-reserve.md).\n"
)

_CONCEPT_CONTENT = (
    "# Federal Reserve\n\n"
    "The Federal Reserve System is the central banking system of the United States. "
    "It was created in 1913 and is responsible for conducting monetary policy, "
    "supervising banks, and maintaining financial stability.\n"
)

_APPEND_CONTENT = (
    "## QE Programs\n\n"
    "The Federal Reserve conducted four major rounds of quantitative easing between "
    "2008 and 2014, purchasing trillions of dollars in mortgage-backed securities "
    "and Treasury bonds to lower long-term interest rates.\n"
)


def _insert_source(db_path: str, filename: str) -> str:
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
                (doc_id, user_id, filename, filename, f"sources/{filename}", doc_number),
            )
    return doc_id


def test_phase1_full_roundtrip(tmp_workspace: WorkspaceFixture) -> None:
    """
    Full round-trip:
      1. create_page — summary page + concept page
      2. search_chunks — finds content from both
      3. append_to_page — concept page updated
      4. read_page — returns combined content
      5. update_references — creates citation + link edges
      6. get_backlinks — concept page sees the summary as a backlink
    """
    source_id = _insert_source(tmp_workspace.db_path, "qe-study.pdf")

    # ── 1. Create pages ───────────────────────────────────────────────────────
    summary = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "quantitative-easing",
        "Quantitative Easing", _SUMMARY_CONTENT, [],
    )
    concept = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "federal-reserve",
        "Federal Reserve", _CONCEPT_CONTENT, [],
    )

    assert (tmp_workspace.workspace / "wiki" / "summaries" / "quantitative-easing.md").exists()
    assert (tmp_workspace.workspace / "wiki" / "concepts" / "federal-reserve.md").exists()

    # ── 2. search_chunks finds content from both pages ────────────────────────
    results_all = search_chunks(tmp_workspace.db_path, "monetary policy")
    assert len(results_all) >= 2, "Expected hits in both pages"

    results_wiki = search_chunks(tmp_workspace.db_path, "central bank", scope="wiki")
    assert results_wiki
    assert all(r["path"].startswith("/wiki/") for r in results_wiki)

    # ── 3. append_to_page — concept page extended ─────────────────────────────
    ok = append_to_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "federal-reserve", _APPEND_CONTENT,
    )
    assert ok is True

    # ── 4. read_page — combined content visible ───────────────────────────────
    combined = read_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "federal-reserve",
    )
    assert combined is not None
    assert "Federal Reserve System" in combined       # original
    assert "QE Programs" in combined                  # appended section

    # append updated FTS5 — search should now find the appended text
    # Note: FTS5 porter tokenizer splits on hyphens, so use plain terms
    results_qe = search_chunks(tmp_workspace.db_path, "Treasury bonds")
    assert results_qe, "Appended content should be searchable via FTS5"

    # ── 5. update_references — summary cites source + links to concept ────────
    update_references(
        tmp_workspace.db_path, summary["id"],
        _SUMMARY_CONTENT, "/wiki/summaries/",
    )

    with get_connection(tmp_workspace.db_path) as conn:
        edges = conn.execute(
            "SELECT target_document_id, reference_type "
            "FROM document_references WHERE source_document_id=?",
            (summary["id"],),
        ).fetchall()

    edge_map = {e["target_document_id"]: e["reference_type"] for e in edges}
    assert source_id in edge_map, "Summary must cite the source PDF"
    assert edge_map[source_id] == "cites"
    assert concept["id"] in edge_map, "Summary must link to the concept page"
    assert edge_map[concept["id"]] == "links_to"

    # ── 6. get_backlinks — concept page sees the summary ─────────────────────
    backlinks = get_backlinks(tmp_workspace.db_path, concept["id"])
    assert len(backlinks) == 1
    assert backlinks[0]["id"] == summary["id"]
    assert backlinks[0]["reference_type"] == "links_to"

    # source PDF sees the summary as a backlink too
    source_backlinks = get_backlinks(tmp_workspace.db_path, source_id)
    assert any(b["id"] == summary["id"] for b in source_backlinks)
