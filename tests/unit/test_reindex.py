"""Tests for domain/tools/reindex.py: the index rebuilt from disk, with no model.

The original wiki is built with the real ingestion pipeline, a scripted model
client and a fake extractor (no Java, no LibreOffice, no network). The rebuild
runs with the same fake extractor in place of `domain.ingestion.extractor.extract`.
What the roadmap lists as not recoverable (`version`, `document_number`, the
dates) is not asserted.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from domain.ingestion import extractor, pipeline
from domain.tools.db import get_connection
from domain.tools.reindex import NothingExtracted, reindex_from_disk
from domain.tools.search import search_chunks
from tests.helpers.workspace import WorkspaceFixture

TALES = ("Tale One.pdf", "Tale Two.pdf")


def fake_extract(file_path: Path, cache_dir: Path):
    """Two pages per source, fixed by the file name."""
    stem = file_path.stem
    return [(1, f"# {stem}\n\nCinderella lost a glass slipper at the royal ball. {stem} page one."),
            (2, f"The prince searched the kingdom for the owner of the slipper. {stem} page two.")], "fake-parser"


class ScriptedClient:
    """Answers the ingestion prompts in order: extraction, then one page per concept."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, messages, **kwargs):
        content = self.responses.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _extraction(summary: str, *concepts: str) -> str:
    return json.dumps({"document_summary": summary, "concepts": [
        {"name": name, "category": "entity", "insight": f"About {name}"} for name in concepts]})


def _concept(name: str, source: str, links: str = "") -> str:
    return (f"# {name}\n\n{name} appears in the tale of the glass slipper. {links}\n\n"
            f"## Sources\n- [^1]: {source}\n")


@pytest.fixture
def original(tmp_workspace: WorkspaceFixture, monkeypatch) -> WorkspaceFixture:
    """A wiki built by ingestion: two sources, three concepts, two summaries.

    Each concept page links only to pages created before it, so the references the
    pipeline recorded while it ran are the references the files hold now.
    """
    monkeypatch.setattr(pipeline, "extract", fake_extract)
    monkeypatch.setattr(pipeline, "check_java", lambda: "/usr/bin/java")
    ws = tmp_workspace
    for name in TALES:
        shutil.copy(Path(__file__), ws.workspace / "sources" / name)   # any bytes: the extractor is fake
    client = ScriptedClient([
        _extraction("Tale one tells how a slipper was lost.", "Glass Slipper", "Fairy Godmother"),
        _concept("Glass Slipper", "Tale One.pdf"),
        _concept("Fairy Godmother", "Tale One.pdf", "See [Glass Slipper](glass-slipper.md)."),
        _extraction("Tale two tells how the prince searched.", "Royal Ball"),
        _concept("Royal Ball", "Tale Two.pdf", "See [Glass Slipper](glass-slipper.md)."),
    ])
    for name in TALES:
        result = pipeline.ingest_file(ws.workspace / "sources" / name, ws.db_path, ws.workspace, client, "fake",
                                      _batch_mode=True)
        assert result.status == "ingested", result.message
    return ws


@pytest.fixture
def fake_extractor(monkeypatch):
    monkeypatch.setattr(extractor, "extract", fake_extract)


def snapshot(db_path: str) -> dict:
    """The content of an index, keyed by `relative_path`; ids and counters left out."""
    with get_connection(db_path) as conn:
        path_of = {r["id"]: r["relative_path"] for r in conn.execute("SELECT id, relative_path FROM documents")}
        documents = {}
        for r in conn.execute(
                "SELECT relative_path, filename, title, path, source_kind, file_type, file_size, status, page_count, "
                "content, tags, parser, content_hash, mtime_ns, source_document_id FROM documents"):
            row = dict(r)
            row["source_document_id"] = path_of.get(row["source_document_id"])
            documents[row.pop("relative_path")] = row
        pages = {(path_of[r["document_id"]], r["page"]): r["content"]
                 for r in conn.execute("SELECT document_id, page, content FROM document_pages")}
        chunks = sorted(
            (path_of[r["document_id"]], r["chunk_index"], r["content"], r["content_stemmed"], r["page"],
             r["start_char"], r["token_count"], r["header_breadcrumb"])
            for r in conn.execute("SELECT * FROM document_chunks"))
        references = sorted(
            (path_of[r["source_document_id"]], path_of[r["target_document_id"]], r["reference_type"], r["page"])
            for r in conn.execute("SELECT * FROM document_references"))
        fts_rows = conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'slipper'").fetchone()[0]
    return {"documents": documents, "pages": pages, "chunks": chunks, "references": references, "fts_rows": fts_rows}


def tree_hashes(*roots: Path) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in roots for p in sorted(root.rglob("*")) if p.is_file()}


# ── The rebuilt index equals the original ───────────────────────────────────

def test_the_rebuilt_index_holds_the_same_rows_pages_chunks_and_references(original, fake_extractor):
    before = snapshot(original.db_path)
    assert before["references"], "the fixture must have a reference graph to compare"
    reindex_from_disk(original.workspace, original.db_path)
    after = snapshot(original.db_path)
    assert after["documents"].keys() == before["documents"].keys()
    assert after["documents"] == before["documents"]
    assert after["pages"] == before["pages"]
    assert after["chunks"] == before["chunks"]
    assert after["references"] == before["references"]
    assert after["fts_rows"] == before["fts_rows"] > 0


def test_the_rebuilt_index_answers_searches_as_the_original_did(original, fake_extractor):
    def hits():
        return sorted((h["filename"], h["content"]) for h in search_chunks(original.db_path, "glass slipper", limit=50))

    before = hits()
    assert before
    reindex_from_disk(original.workspace, original.db_path)
    assert hits() == before


def test_the_three_fixed_pages_have_no_row(original, fake_extractor):
    reindex_from_disk(original.workspace, original.db_path)
    paths = set(snapshot(original.db_path)["documents"])
    assert not paths & {"wiki/index.md", "wiki/overview.md", "wiki/log.md"}


def test_a_summary_page_gets_its_source_document_id_back_by_slug(original, fake_extractor):
    with get_connection(original.db_path) as conn:
        conn.execute("UPDATE documents SET source_document_id = NULL WHERE source_kind='wiki'")
        conn.commit()
    report = reindex_from_disk(original.workspace, original.db_path)
    documents = snapshot(original.db_path)["documents"]
    assert documents["wiki/summaries/tale-one.md"]["source_document_id"] == "sources/Tale One.pdf"
    assert documents["wiki/summaries/tale-two.md"]["source_document_id"] == "sources/Tale Two.pdf"
    assert documents["wiki/concepts/glass-slipper.md"]["source_document_id"] is None
    assert report.summaries_linked == 2


def test_the_report_counts_what_was_written(original, fake_extractor):
    lines: list[str] = []
    report = reindex_from_disk(original.workspace, original.db_path, progress=lines.append)
    assert (report.sources_indexed, report.sources_failed, report.wiki_pages_indexed) == (2, (), 5)
    assert report.references == len(snapshot(original.db_path)["references"])
    assert any("Tale One.pdf" in line for line in lines) and any("glass-slipper" in line for line in lines)


# ── What the rebuild does not touch, and what it keeps ──────────────────────

def test_no_markdown_file_and_no_source_file_is_modified(original, fake_extractor):
    ws = original.workspace
    before = tree_hashes(ws / "wiki", ws / "sources")
    reindex_from_disk(ws, original.db_path)
    assert tree_hashes(ws / "wiki", ws / "sources") == before


def test_the_old_index_stays_as_a_backup(original, fake_extractor):
    before = snapshot(original.db_path)
    reindex_from_disk(original.workspace, original.db_path)
    assert snapshot(original.db_path + ".bak") == before
    assert not Path(original.db_path + ".reindex").exists()


def test_the_workspace_row_is_kept(original, fake_extractor):
    with get_connection(original.db_path) as conn:
        old = tuple(conn.execute("SELECT id, name, user_id FROM workspace").fetchone())
    reindex_from_disk(original.workspace, original.db_path)
    with get_connection(original.db_path) as conn:
        assert tuple(conn.execute("SELECT id, name, user_id FROM workspace").fetchone()) == old
        assert {r["user_id"] for r in conn.execute("SELECT user_id FROM documents")} == {old[2]}


def test_a_damaged_index_is_rebuilt(original, fake_extractor):
    before = snapshot(original.db_path)
    Path(original.db_path).write_bytes(b"this is not a database" * 100)
    Path(original.db_path + "-wal").unlink(missing_ok=True)
    Path(original.db_path + "-shm").unlink(missing_ok=True)
    reindex_from_disk(original.workspace, original.db_path)
    after = snapshot(original.db_path)
    assert after["documents"] == before["documents"] and after["references"] == before["references"]
    with get_connection(original.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM workspace").fetchone()[0] == 1


def test_a_missing_index_is_built(original, fake_extractor):
    before = snapshot(original.db_path)
    for suffix in ("", "-wal", "-shm"):
        Path(original.db_path + suffix).unlink(missing_ok=True)
    reindex_from_disk(original.workspace, original.db_path)
    assert snapshot(original.db_path)["documents"] == before["documents"]


def test_a_manual_edit_to_a_page_is_indexed_not_overwritten(original, fake_extractor):
    page = original.workspace / "wiki" / "concepts" / "glass-slipper.md"
    edited = page.read_text(encoding="utf-8") + "\nA manual line about the ball.\n"
    page.write_text(edited, encoding="utf-8")
    reindex_from_disk(original.workspace, original.db_path)
    assert page.read_text(encoding="utf-8") == edited
    assert "A manual line about the ball." in snapshot(original.db_path)["documents"]["wiki/concepts/glass-slipper.md"]["content"]


def test_title_and_tags_come_from_the_front_matter_else_the_heading_else_the_file_name(original, fake_extractor):
    concepts = original.workspace / "wiki" / "concepts"
    (concepts / "with-heading.md").write_text("# The Heading\n\nBody about slippers.\n", encoding="utf-8")
    (concepts / "no-heading_here.md").write_text("Body only.\n", encoding="utf-8")
    (concepts / "broken-fm.md").write_text("---\ntitle: [unclosed\n---\n\n# Fallback Title\n", encoding="utf-8")
    reindex_from_disk(original.workspace, original.db_path)
    documents = snapshot(original.db_path)["documents"]
    assert documents["wiki/concepts/glass-slipper.md"]["title"] == "Glass Slipper"
    assert json.loads(documents["wiki/concepts/glass-slipper.md"]["tags"]) == ["entity"]
    assert documents["wiki/concepts/with-heading.md"]["title"] == "The Heading"
    assert documents["wiki/concepts/no-heading_here.md"]["title"] == "No Heading Here"
    assert documents["wiki/concepts/broken-fm.md"]["title"] == "Fallback Title"


def test_a_link_to_a_page_created_later_is_found(original, fake_extractor):
    """Ingestion records a link only when the target already exists; the rebuild sees every page."""
    page = original.workspace / "wiki" / "concepts" / "glass-slipper.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nSee [Royal Ball](royal-ball.md).\n", encoding="utf-8")
    reindex_from_disk(original.workspace, original.db_path)
    edge = ("wiki/concepts/glass-slipper.md", "wiki/concepts/royal-ball.md", "links_to", None)
    assert edge in snapshot(original.db_path)["references"]


# ── Sources that are gone or cannot be read ─────────────────────────────────

def test_a_page_whose_source_file_is_gone_is_registered_with_a_dangling_citation(original, fake_extractor):
    (original.workspace / "sources" / "Tale One.pdf").unlink()
    report = reindex_from_disk(original.workspace, original.db_path)
    after = snapshot(original.db_path)
    assert "sources/Tale One.pdf" not in after["documents"]
    assert "wiki/concepts/glass-slipper.md" in after["documents"] and "wiki/summaries/tale-one.md" in after["documents"]
    assert after["documents"]["wiki/summaries/tale-one.md"]["source_document_id"] is None
    assert not [r for r in after["references"] if r[1] == "sources/Tale One.pdf"]
    assert report.sources_indexed == 1


def test_a_source_the_extractor_cannot_read_is_registered_as_failed(original, monkeypatch):
    def extract(path, cache_dir):
        if path.name == "Tale Two.pdf":
            raise RuntimeError("corrupt PDF")
        return fake_extract(path, cache_dir)

    monkeypatch.setattr(extractor, "extract", extract)
    report = reindex_from_disk(original.workspace, original.db_path)
    assert report.sources_indexed == 1 and report.sources_failed == (("Tale Two.pdf", "corrupt PDF"),)
    row = snapshot(original.db_path)["documents"]["sources/Tale Two.pdf"]
    assert row["status"] == "failed"
    with get_connection(original.db_path) as conn:
        assert conn.execute("SELECT error_message FROM documents WHERE filename='Tale Two.pdf'").fetchone()[0] == "corrupt PDF"


@pytest.mark.parametrize("error", [extractor.JavaNotInstalledError("Tale One.pdf"),
                                   extractor.LibreOfficeNotInstalledError("Tale One.pdf")])
def test_an_extractor_that_cannot_run_stops_the_rebuild_and_keeps_the_old_index(original, monkeypatch, error):
    def extract(path, cache_dir):
        raise error

    monkeypatch.setattr(extractor, "extract", extract)
    before = snapshot(original.db_path)
    with pytest.raises(type(error)):
        reindex_from_disk(original.workspace, original.db_path)
    assert snapshot(original.db_path) == before
    assert not Path(original.db_path + ".reindex").exists() and not Path(original.db_path + ".bak").exists()


def test_an_extractor_that_fails_on_every_source_keeps_the_old_index(original, monkeypatch):
    """No source extracted means the machine is at fault (no Writer filter in LibreOffice, say):
    replacing the index would drop every page of text."""
    def extract(path, cache_dir):
        raise RuntimeError("LibreOffice produced no PDF output")

    monkeypatch.setattr(extractor, "extract", extract)
    before = snapshot(original.db_path)
    with pytest.raises(NothingExtracted, match="LibreOffice produced no PDF output"):
        reindex_from_disk(original.workspace, original.db_path)
    assert snapshot(original.db_path) == before
    assert not Path(original.db_path + ".reindex").exists() and not Path(original.db_path + ".bak").exists()


def test_a_source_with_an_unsupported_suffix_or_a_hidden_name_is_not_indexed(original, fake_extractor):
    (original.workspace / "sources" / "notes.csv").write_text("not a source", encoding="utf-8")
    (original.workspace / "sources" / ".hidden.pdf").write_bytes(b"x")
    reindex_from_disk(original.workspace, original.db_path)
    sources = {k for k, v in snapshot(original.db_path)["documents"].items() if v["source_kind"] == "source"}
    assert sources == {"sources/Tale One.pdf", "sources/Tale Two.pdf"}


def test_an_empty_workspace_gives_an_empty_index(tmp_workspace, fake_extractor):
    report = reindex_from_disk(tmp_workspace.workspace, tmp_workspace.db_path)
    assert (report.sources_indexed, report.wiki_pages_indexed, report.references) == (0, 0, 0)
    with get_connection(tmp_workspace.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_the_rebuilt_database_is_a_valid_sqlite_file(original, fake_extractor):
    reindex_from_disk(original.workspace, original.db_path)
    conn = sqlite3.connect(original.db_path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()
