"""A wiki with a history, for the rollback tests: two ingestions and a deletion,
each committed by `auto_commit` (which also captures the snapshot).

The ingestions run the real pipeline with a scripted model client and a fake
extractor, so nothing needs Java, LibreOffice or a network."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from domain.ingestion import extractor, pipeline
from domain.tools.db import get_connection
from domain.tools.git_ops import auto_commit, head_sha, init_wiki_repo
from domain.tools.search import search_chunks
from domain.tools.wiki_fs import delete_page
from tests.helpers.workspace import WorkspaceFixture

TALES = ("Tale One.pdf", "Tale Two.pdf")


def fake_extract(file_path: Path, cache_dir: Path):
    stem = file_path.stem
    return [(1, f"# {stem}\n\nCinderella lost a glass slipper at the royal ball. {stem} page one."),
            (2, f"The prince searched the kingdom for the owner of the slipper. {stem} page two.")], "fake-parser"


class ScriptedClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, messages, **kwargs):
        content = self.responses.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def extraction(summary: str, *concepts: str) -> str:
    return json.dumps({"document_summary": summary, "concepts": [
        {"name": name, "category": "entity", "insight": f"About {name}"} for name in concepts]})


def concept(name: str, source: str, links: str = "") -> str:
    return (f"# {name}\n\n{name} appears in the tale of the glass slipper. {links}\n\n"
            f"## Sources\n- [^1]: {source}\n")


def git(workspace: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=workspace, capture_output=True, text=True, check=True).stdout.strip()


def ingest(ws: WorkspaceFixture, name: str, client: ScriptedClient) -> str:
    """Ingest `name` as the batch does (pipeline, then one commit); return the commit sha."""
    shutil.copy(Path(__file__), ws.workspace / "sources" / name)   # any bytes: the extractor is fake
    result = pipeline.ingest_file(ws.workspace / "sources" / name, ws.db_path, ws.workspace, client, "fake",
                                  _batch_mode=True)
    assert result.status == "ingested", result.message
    init_wiki_repo(ws.workspace)
    auto_commit(ws.workspace, f"ingest: {name}")
    return head_sha(ws.workspace)


def use_fake_extractor(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "extract", fake_extract)
    monkeypatch.setattr(pipeline, "check_java", lambda: "/usr/bin/java")
    monkeypatch.setattr(extractor, "extract", fake_extract)
    monkeypatch.setattr(extractor, "check_java", lambda: "/usr/bin/java")
    monkeypatch.setattr(extractor, "check_libreoffice", lambda: "/usr/bin/soffice")


def build_history(ws: WorkspaceFixture, monkeypatch) -> dict[str, str]:
    """A: ingest Tale One. B: ingest Tale Two. C: delete a page of Tale One.
    Returns the sha of each commit."""
    use_fake_extractor(monkeypatch)
    shas = {}
    shas["A"] = ingest(ws, "Tale One.pdf", ScriptedClient([
        extraction("Tale one tells how a slipper was lost.", "Glass Slipper", "Fairy Godmother"),
        concept("Glass Slipper", "Tale One.pdf"),
        concept("Fairy Godmother", "Tale One.pdf", "See [Glass Slipper](glass-slipper.md)."),
    ]))
    shas["B"] = ingest(ws, "Tale Two.pdf", ScriptedClient([
        extraction("Tale two tells how the prince searched.", "Royal Ball"),
        concept("Royal Ball", "Tale Two.pdf", "See [Glass Slipper](glass-slipper.md)."),
    ]))
    assert delete_page(ws.db_path, ws.workspace, "/wiki/concepts/", "fairy-godmother")
    auto_commit(ws.workspace, "delete: concepts/fairy-godmother")
    shas["C"] = head_sha(ws.workspace)
    return shas


def wiki_files(ws: WorkspaceFixture) -> dict[str, str]:
    wiki = ws.workspace / "wiki"
    return {p.relative_to(wiki).as_posix(): p.read_text(encoding="utf-8") for p in sorted(wiki.rglob("*")) if p.is_file()}


def db_view(db_path: str) -> dict:
    """What a user can observe through the index: wiki pages, the sources, the
    reference graph and the search hits. Ids, dates and counters are left out."""
    with get_connection(db_path) as conn:
        path_of = {r["id"]: r["relative_path"] for r in conn.execute("SELECT id, relative_path FROM documents")}
        documents = sorted((r["source_kind"], r["relative_path"], r["title"])
                           for r in conn.execute("SELECT source_kind, relative_path, title FROM documents"))
        references = sorted((path_of[r["source_document_id"]], path_of[r["target_document_id"]], r["reference_type"])
                            for r in conn.execute("SELECT * FROM document_references"))
    hits = sorted((h["filename"], h["content"]) for h in search_chunks(db_path, "glass slipper", limit=50))
    return {"documents": documents, "references": references, "hits": hits}
