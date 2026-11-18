"""The deletion of a source on a copy of `examples/cuentos-de-hadas`.

`La Cenicienta.pdf` is cited by six pages. Three cite only that source: the summary
`la-cenicienta.md` and the concepts `cenicienta.md` and `hada-madrina.md`. Three cite
another source too: `charles-perrault.md`, `moraleja.md` and `reina-malvada.md`.

The deletion removes the three pages that exist only because of the source, keeps
the other three marked stale and without any citation of the source, and leaves no
link to anything deleted in any file of `wiki/` (owner, 2026-11-18). The same holds
when the file is removed from `sources/` by hand: the deletion then runs when the
wiki is opened (`remove_missing_sources`), before a scan, and before a rebuild of
the index. No model is called.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from domain.chat.preretrieval import retrieve_source_chunks
from domain.chat.vocabulary import read_generated_aliases
from domain.tools.db import get_connection
from domain.tools.references import base_name, cited_in_text, header_sources
from domain.tools.deletion import delete_source, remove_missing_sources
from domain.tools.reindex import reindex_from_disk

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "examples" / "cuentos-de-hadas"
SOURCE = "La Cenicienta.pdf"
DELETED = ("wiki/summaries/la-cenicienta.md", "wiki/concepts/cenicienta.md", "wiki/concepts/hada-madrina.md")
KEPT = ("wiki/concepts/charles-perrault.md", "wiki/concepts/moraleja.md", "wiki/concepts/reina-malvada.md")
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_AUTOCOMMIT", "1")
    workspace = tmp_path / "w"
    shutil.copytree(DEMO, workspace, ignore=shutil.ignore_patterns(".git", "traces", "chat_trace.jsonl"))
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                 ["add", "-A"], ["commit", "-qm", "start"]):
        subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)
    return workspace


def _db(workspace: Path) -> str:
    return str(workspace / ".llmwiki" / "index.db")


def _source_id(workspace: Path) -> str:
    with get_connection(_db(workspace)) as conn:
        return conn.execute("SELECT id FROM documents WHERE filename=?", (SOURCE,)).fetchone()["id"]


def _commits(workspace: Path) -> int:
    out = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=workspace, capture_output=True, text=True)
    return int(out.stdout.strip())


def _dead_links(workspace: Path, source_id: str) -> list[tuple[str, str]]:
    """Every link of a `.md` file of `wiki/` that resolves to a deleted target."""
    dead = {*DELETED, f"sources/{SOURCE}"}
    found = []
    for path in sorted((workspace / "wiki").rglob("*.md")):
        rel_dir = path.parent.relative_to(workspace).as_posix()
        for href in LINK.findall(path.read_text(encoding="utf-8")):
            clean = href.split("#", 1)[0].strip()
            target = Path(rel_dir, clean).as_posix()
            resolved = str(Path("/", target).resolve()).lstrip("/")
            if resolved in dead or clean.lstrip("/") in dead or source_id in clean:
                found.append((path.relative_to(workspace).as_posix(), href))
    return found


def _cites_source(path: Path) -> bool:
    """Whether the page cites the deleted source: header `sources`, sources section,
    footnotes. A mention in the prose is the text the source contributed; it stays."""
    text = path.read_text(encoding="utf-8")
    return base_name(SOURCE) in cited_in_text(text) or SOURCE in header_sources(text)


def _check_cascade(workspace: Path, source_id: str) -> None:
    for page in DELETED:
        assert not (workspace / page).exists(), page
    for page in KEPT:
        assert not _cites_source(workspace / page), page
    assert _dead_links(workspace, source_id) == []
    index = (workspace / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "cenicienta.md" not in index and "hada-madrina.md" not in index
    with get_connection(_db(workspace)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE id=? OR relative_path IN (?,?,?)",
                            (source_id, *DELETED)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM document_chunks WHERE document_id=?",
                            (source_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM document_references WHERE target_document_id=?",
                            (source_id,)).fetchone()[0] == 0
        stale = {r[0] for r in conn.execute(
            "SELECT relative_path FROM documents WHERE stale_since IS NOT NULL")}
    assert set(KEPT) <= stale
    assert not [h for h in retrieve_source_chunks(_db(workspace), "lacayos lagartos medianoche", language="es")
                if SOURCE in h]
    aliases = read_generated_aliases(workspace)
    assert "Cenicienta" not in aliases and "Hada madrina" not in aliases
    assert "Charles Perrault" in aliases     # a kept page keeps its aliases


def test_the_screen_deletion_removes_the_pages_of_the_source_only(wiki):
    source_id = _source_id(wiki)
    result = delete_source(_db(wiki), wiki, source_id, also_delete_file=True)
    assert result.success, result.message
    assert sorted(result.deleted_pages) == sorted("/" + p for p in DELETED)
    assert sorted(result.stale_pages) == sorted("/" + p for p in KEPT)
    assert set(result.edited_pages) >= {"/" + p for p in KEPT}
    _check_cascade(wiki, source_id)


def test_a_kept_page_loses_every_citation_of_the_deleted_source(wiki):
    delete_source(_db(wiki), wiki, _source_id(wiki), also_delete_file=True)
    text = (wiki / "wiki" / "concepts" / "moraleja.md").read_text(encoding="utf-8")
    assert "  - resource: sources/Caperucita Roja.pdf\n" in text
    assert "## Fuentes\n- Caperucita Roja.pdf" in text
    assert "La Cenicienta" in text          # the text that came from the source stays
    assert "- Cenicienta\n" not in text     # a «Véase también» item to a deleted page goes whole
    assert "topics: [advertencia moral" in text   # the other fields keep their form


def test_the_deletion_is_one_commit(wiki):
    before = _commits(wiki)
    delete_source(_db(wiki), wiki, _source_id(wiki), also_delete_file=True)
    assert _commits(wiki) == before + 1


def test_a_file_removed_by_hand_is_deleted_when_the_wiki_is_opened(wiki):
    source_id = _source_id(wiki)
    (wiki / "sources" / SOURCE).unlink()
    results = remove_missing_sources(_db(wiki), wiki)
    assert [r.filename for r in results] == [SOURCE] and results[0].success
    _check_cascade(wiki, source_id)


def test_nothing_missing_changes_nothing(wiki):
    before = _commits(wiki)
    stamp = (wiki / ".llmwiki" / "index.db").stat().st_mtime_ns
    assert remove_missing_sources(_db(wiki), wiki) == []
    assert _commits(wiki) == before
    assert (wiki / ".llmwiki" / "index.db").stat().st_mtime_ns == stamp


def test_a_rebuild_after_a_removal_by_hand_applies_the_same_deletion(wiki):
    source_id = _source_id(wiki)
    (wiki / "sources" / SOURCE).unlink()
    reindex_from_disk(wiki, _db(wiki))
    for page in DELETED:
        assert not (wiki / page).exists(), page
    assert _dead_links(wiki, source_id) == []
    for page in KEPT:
        assert not _cites_source(wiki / page), page
