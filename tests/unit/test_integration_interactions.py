"""Interactions between merged branches that no single branch tested.

Each test names the branches it joins. No model, no network, no Java, no LibreOffice:
the model is scripted, the text formats are read directly, and the PDF path is not used.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from domain.ingestion import extractor, pipeline
from domain.rollback import snapshots
from domain.rollback.revert import revert_wiki
from domain.tools.db import get_connection
from domain.tools.git_ops import auto_commit, head_sha, init_wiki_repo
from domain.tools.search import search_chunks
from tests.helpers.rollback import ScriptedClient, concept, extraction, git, wiki_files


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv("WIKI_AUTOCOMMIT", raising=False)
    monkeypatch.delenv("WIKI_SNAPSHOT_KEEP", raising=False)


def ingest_text(ws, name: str, body: str, client: ScriptedClient) -> str:
    """Ingest a `.md` or `.txt` source with the real text extractor; return the commit sha."""
    path = ws.workspace / "sources" / name
    path.write_text(body, encoding="utf-8")
    result = pipeline.ingest_file(path, ws.db_path, ws.workspace, client, "fake", _batch_mode=True)
    assert result.status == "ingested", result.message
    init_wiki_repo(ws.workspace)
    auto_commit(ws.workspace, f"ingest: {name}")
    return head_sha(ws.workspace)


@pytest.fixture
def text_history(tmp_workspace):
    """Commit A ingests `notes.md`; commit B ingests `memo.txt`."""
    ws = tmp_workspace
    a = ingest_text(ws, "notes.md", "# Notes\n\n" + "Cinderella lost a glass slipper at the royal ball. " * 12 + "\n",
                    ScriptedClient([extraction("Notes about a slipper.", "Glass Slipper"),
                                    concept("Glass Slipper", "notes.md")]))
    b = ingest_text(ws, "memo.txt", ("The prince searched the kingdom with a velvet cushion. " * 12) + "\n",
                    ScriptedClient([extraction("A memo about the prince.", "Royal Search"),
                                    concept("Royal Search", "memo.txt")]))
    return ws, {"A": a, "B": b}


def source_paths(ws) -> set[str]:
    with get_connection(ws.db_path) as conn:
        return {r["filename"] for r in conn.execute("SELECT filename FROM documents WHERE source_kind = 'source'")}


# ── 1. Rollback and reindex (gracious-bohr + reindex-from-disk + text-formats) ─

def test_a_revert_without_a_snapshot_reindexes_a_wiki_with_md_and_txt_sources(text_history, monkeypatch):
    ws, shas = text_history
    assert source_paths(ws) == {"notes.md", "memo.txt"}
    snapshots._snapshot_file(ws.workspace, shas["A"]).unlink()
    # Text sources need neither Java nor LibreOffice, so the route must not ask for them.
    monkeypatch.setattr(extractor, "check_java", lambda: None)
    monkeypatch.setattr(extractor, "check_libreoffice", lambda: None)

    result = revert_wiki(ws.workspace, shas["A"])

    assert result.db_via == "reindex" and result.reindex is not None
    assert "concepts/royal-search.md" not in wiki_files(ws)          # page of B, gone
    assert "concepts/glass-slipper.md" in wiki_files(ws)
    assert git(ws.workspace, "diff", shas["A"], "HEAD", "--", "wiki") == ""
    # sources/ is never touched, so the rebuilt index knows both files, as the disk does.
    assert source_paths(ws) == {"notes.md", "memo.txt"}
    assert (ws.workspace / "sources" / "memo.txt").is_file()
    assert [h["filename"] for h in search_chunks(ws.db_path, "velvet cushion", limit=5)] == ["memo.txt"]
    assert [h["filename"] for h in search_chunks(ws.db_path, "glass slipper", limit=5)]


def test_the_revert_route_of_a_text_wiki_is_a_reindex_that_needs_no_java(text_history, monkeypatch):
    from domain.rollback.revert import index_route

    ws, shas = text_history
    snapshots._snapshot_file(ws.workspace, shas["A"]).unlink()
    monkeypatch.setattr(extractor, "check_java", lambda: None)
    via, reason = index_route(ws.workspace, shas["A"])
    assert via == "reindex" and shas["A"][:7] in reason


# ── 4. Relations graph and the new formats (relations-graph + text-formats) ──

def test_a_md_and_a_txt_source_are_source_nodes_of_the_graph(text_history):
    from services.wiki import open_wiki, graph

    ws, _ = text_history
    data = graph(open_wiki(ws.workspace))
    sources = {n["id"]: n for n in data["nodes"] if n["kind"] == "source"}
    assert set(sources) == {"source:notes.md", "source:memo.txt"}
    assert sources["source:notes.md"]["title"] == "notes.md"
    cites = {(e["source"], e["target"]) for e in data["edges"] if e["type"] == "cites"}
    assert any(target == "source:notes.md" for _, target in cites)
    assert any(target == "source:memo.txt" for _, target in cites)


# ── 2. Rollback and the save review (gracious-bohr + save-review) ────────────

def test_a_page_saved_through_the_review_is_one_commit_with_a_snapshot(text_history):
    from services import chat as chat_service
    from services.wiki import commit_id, history, open_wiki

    ws, shas = text_history
    wiki = open_wiki(ws.workspace)
    before = len(history(wiki))

    message = chat_service.save_reviewed_page(wiki, "Slipper story", "# Slipper story\n\nWritten in the dialog.\n")

    assert message
    commits = history(wiki)
    assert len(commits) == before + 1                                   # one commit, not two
    saved = commits[0]
    assert saved.message == "chat: Slipper story" and saved.has_snapshot
    assert set(saved.pages) == {"concepts/slipper-story", "index"}   # the page and the index entry
    assert git(ws.workspace, "rev-parse", "HEAD") == commit_id(wiki, saved.sha)
    assert snapshots._snapshot_file(ws.workspace, saved.sha).is_file()


def test_reverting_to_the_point_before_a_reviewed_save_removes_the_page(text_history):
    from services import chat as chat_service
    from services.wiki import open_wiki

    ws, shas = text_history
    wiki = open_wiki(ws.workspace)
    chat_service.save_reviewed_page(wiki, "Slipper story", "# Slipper story\n\nWritten in the dialog.\n")
    assert "concepts/slipper-story.md" in wiki_files(ws)

    result = revert_wiki(ws.workspace, shas["B"])

    assert result.db_via == "snapshot"
    assert "concepts/slipper-story.md" not in wiki_files(ws)
    assert "concepts/royal-search.md" in wiki_files(ws)
    assert git(ws.workspace, "diff", shas["B"], "HEAD", "--", "wiki") == ""
    with get_connection(ws.db_path) as conn:
        paths = {r["relative_path"] for r in conn.execute("SELECT relative_path FROM documents")}
    assert "wiki/concepts/slipper-story.md" not in paths                  # the index forgot it too
    assert [h["filename"] for h in search_chunks(ws.db_path, "written in the dialog", limit=5)] == []


# ── 5. Regeneration language and the review (known-defects + save-review) ────

def test_a_regenerated_summary_of_a_spanish_wiki_is_spanish_and_a_save_still_makes_one_commit(tmp_workspace):
    from domain.i18n import get_locale
    from services import chat as chat_service
    from services.wiki import open_wiki, history
    from tests.helpers.fake_llm import FakeLLMClient

    ws = tmp_workspace
    first = {"document_summary": "Un cuento.", "concepts": [{"name": "Zapatilla", "category": "entity",
                                                               "insight": "Un zapato"}]}
    path = ws.workspace / "sources" / "cuento.md"
    path.write_text("# Cuento\n\nCenicienta perdió una zapatilla de cristal.\n", encoding="utf-8")
    result = pipeline.ingest_file(
        path, ws.db_path, ws.workspace,
        ScriptedClient([json.dumps(first), concept("Zapatilla", "cuento.md")]), "fake", language="es",
        _batch_mode=True)
    assert result.status == "ingested", result.message
    init_wiki_repo(ws.workspace)
    auto_commit(ws.workspace, "ingest: cuento.md")

    second = {"document_summary": "Un cuento clásico.", "concepts": first["concepts"]}
    client = FakeLLMClient(response_content=json.dumps(second))
    assert [r.status for r in pipeline.regenerate_wiki_pages(
        ws.workspace, ws.db_path, client, "fake", language="es")] == ["ingested"]

    loc = get_locale("es")
    summary = (ws.workspace / "wiki" / "summaries" / "cuento.md").read_text(encoding="utf-8")
    assert loc.content_directive in client.calls[0]["messages"][0]["content"]
    assert f"## {loc.h_summary}" in summary and "## Summary" not in summary
    assert "Un cuento clásico." in summary

    # A page saved from the chat afterwards is one commit that says what it holds.
    wiki = open_wiki(ws.workspace)
    auto_commit(ws.workspace, "regenerate summaries: 1")
    before = len(history(wiki))
    chat_service.save_reviewed_page(wiki, "Nota", "# Nota\n\nTexto revisado.\n")
    assert len(history(wiki)) == before + 1
    assert subprocess.run(["git", "status", "--porcelain", "--", "wiki"], cwd=ws.workspace, capture_output=True,
                          text=True).stdout.strip() == ""
    assert Path(ws.workspace / "wiki" / "concepts" / "nota.md").is_file()
