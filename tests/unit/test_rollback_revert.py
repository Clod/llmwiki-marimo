"""Tests for domain/rollback/revert.py: both layers return to the same point, or nothing changes."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from domain.ingestion import extractor
from domain.rollback import revert as revert_mod
from domain.rollback import snapshots
from domain.rollback.revert import (
    DirtyWiki,
    IndexUnavailable,
    RevertUnavailable,
    UnknownRevision,
    dirty_files,
    revert_wiki,
)
from tests.helpers.rollback import build_history, db_view, git, wiki_files


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv("WIKI_AUTOCOMMIT", raising=False)
    monkeypatch.delenv("WIKI_SNAPSHOT_KEEP", raising=False)


@pytest.fixture
def history(tmp_workspace, monkeypatch):
    """The workspace with commits A (Tale One), B (Tale Two), C (a page deleted)."""
    return tmp_workspace, build_history(tmp_workspace, monkeypatch)


def sources_hash(ws) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ws.workspace / "sources").iterdir())}


# ── The snapshot route ───────────────────────────────────────────────────────

def test_revert_through_the_snapshot_restores_pages_and_index_exactly(history):
    ws, shas = history
    view_at_b = db_view(str(snapshots._snapshot_file(ws.workspace, shas["B"])))
    sources = sources_hash(ws)

    result = revert_wiki(ws.workspace, shas["B"])

    assert result.db_via == "snapshot" and result.target_sha == shas["B"]
    assert "available" in result.db_reason
    assert db_view(ws.db_path) == view_at_b
    assert "concepts/fairy-godmother.md" in wiki_files(ws)          # deleted in C, back
    in_b = git(ws.workspace, "ls-tree", "-r", "--name-only", shas["B"], "--", "wiki").split()
    assert sorted(wiki_files(ws)) == sorted(f.removeprefix("wiki/") for f in in_b)
    assert git(ws.workspace, "diff", shas["B"], "HEAD", "--", "wiki") == ""
    assert sources_hash(ws) == sources                               # sources/ is never touched


def test_the_revert_is_a_new_commit_and_history_is_not_rewritten(history):
    ws, shas = history
    before = git(ws.workspace, "rev-list", "HEAD").split()
    result = revert_wiki(ws.workspace, shas["A"])
    after = git(ws.workspace, "rev-list", "HEAD").split()
    assert after[1:] == before and after[0] == result.commit_sha
    assert git(ws.workspace, "log", "-1", "--format=%s") == f"revert: restore wiki to {shas['A'][:7]}"
    assert result.snapshot_captured
    assert [s.commit_sha for s in snapshots.list_snapshots(ws.workspace)][0] == result.commit_sha


def test_a_file_added_after_the_target_is_removed(history):
    ws, shas = history
    assert "concepts/royal-ball.md" in wiki_files(ws)
    revert_wiki(ws.workspace, shas["A"])
    assert "concepts/royal-ball.md" not in wiki_files(ws)
    assert not any(p.name == "royal-ball.md" for p in (ws.workspace / "wiki").rglob("*"))


def test_head_tilde_works_as_a_target(history):
    ws, shas = history
    result = revert_wiki(ws.workspace, "HEAD~1")
    assert result.target_sha == shas["B"]


def test_a_revert_can_be_reverted(history):
    ws, shas = history
    first = revert_wiki(ws.workspace, shas["A"])
    again = revert_wiki(ws.workspace, shas["C"])
    assert again.db_via == "snapshot" and again.commit_sha != first.commit_sha
    assert git(ws.workspace, "diff", shas["C"], "HEAD", "--", "wiki") == ""


def test_reverting_to_the_current_state_commits_nothing_but_restores_the_index(history):
    ws, shas = history
    head = git(ws.workspace, "rev-parse", "HEAD")
    result = revert_wiki(ws.workspace, head)
    assert result.commit_sha is None and git(ws.workspace, "rev-parse", "HEAD") == head


# ── The reindex route ────────────────────────────────────────────────────────

def test_without_a_snapshot_the_index_is_rebuilt_from_disk_and_is_equivalent(history):
    ws, shas = history
    view_at_b = db_view(str(snapshots._snapshot_file(ws.workspace, shas["B"])))
    snapshots._snapshot_file(ws.workspace, shas["B"]).unlink()

    result = revert_wiki(ws.workspace, shas["B"])

    assert result.db_via == "reindex" and result.reindex is not None
    assert f"no snapshot of {shas['B'][:7]}" in result.db_reason
    assert db_view(ws.db_path) == view_at_b
    assert git(ws.workspace, "diff", shas["B"], "HEAD", "--", "wiki") == ""


def test_with_snapshots_disabled_the_revert_reindexes(history, monkeypatch):
    ws, shas = history
    monkeypatch.setenv("WIKI_SNAPSHOT_KEEP", "0")
    result = revert_wiki(ws.workspace, shas["A"])
    assert result.db_via == "reindex" and "WIKI_SNAPSHOT_KEEP=0" in result.db_reason
    assert not result.snapshot_captured


def test_a_damaged_snapshot_falls_back_to_the_reindex(history):
    ws, shas = history
    snapshots._snapshot_file(ws.workspace, shas["A"]).write_bytes(b"garbage" * 100)
    result = revert_wiki(ws.workspace, shas["A"])
    assert result.db_via == "reindex" and "damaged" in result.db_reason


# ── The refused fallback: nothing changes ────────────────────────────────────

def dump_hash(db_path: str) -> str:
    """Every row of the index, ids included: the same hash means the same index."""
    conn = sqlite3.connect(db_path)
    try:
        return hashlib.sha256("\n".join(conn.iterdump()).encode()).hexdigest()
    finally:
        conn.close()


def state(ws) -> dict:
    return {"files": wiki_files(ws), "head": git(ws.workspace, "rev-parse", "HEAD"), "db": db_view(ws.db_path),
            "db_dump": dump_hash(ws.db_path),
            "status": git(ws.workspace, "status", "--porcelain", "--", "wiki", ".gitignore")}


@pytest.mark.parametrize("missing, code", [("check_java", "java"), ("check_libreoffice", "libreoffice")])
def test_no_snapshot_and_no_extractor_refuses_before_changing_anything(history, monkeypatch, missing, code):
    ws, shas = history
    snapshots._snapshot_file(ws.workspace, shas["A"]).unlink()
    if code == "libreoffice":
        (ws.workspace / "sources" / "Extra.docx").write_bytes(b"x")
    monkeypatch.setattr(extractor, missing, lambda: None)
    before = state(ws)
    with pytest.raises(IndexUnavailable) as exc:
        revert_wiki(ws.workspace, shas["A"])
    assert exc.value.code == code and f"no snapshot of {shas['A'][:7]}" in exc.value.reason
    assert state(ws) == before


def test_a_snapshot_makes_the_missing_extractor_irrelevant(history, monkeypatch):
    ws, shas = history
    monkeypatch.setattr(extractor, "check_java", lambda: None)
    assert revert_wiki(ws.workspace, shas["A"]).db_via == "snapshot"


def test_a_rebuild_that_fails_after_the_checkout_puts_pages_and_index_back(history, monkeypatch):
    ws, shas = history
    snapshots._snapshot_file(ws.workspace, shas["A"]).unlink()

    def broken(path, cache):
        raise RuntimeError("cannot read this file")

    monkeypatch.setattr(extractor, "extract", broken)       # check_java still says yes
    before = state(ws)
    with pytest.raises(IndexUnavailable) as exc:
        revert_wiki(ws.workspace, shas["A"])
    assert exc.value.code == "nothing_extracted"
    assert state(ws) == before
    assert not list((ws.workspace / ".llmwiki").glob("revert-*"))


def test_a_java_that_vanishes_during_the_rebuild_also_undoes_the_checkout(history, monkeypatch):
    ws, shas = history
    snapshots._snapshot_file(ws.workspace, shas["A"]).unlink()

    def no_java(path, cache):
        raise extractor.JavaNotInstalledError(path.name)

    monkeypatch.setattr(extractor, "extract", no_java)
    before = state(ws)
    with pytest.raises(IndexUnavailable) as exc:
        revert_wiki(ws.workspace, shas["A"])
    assert exc.value.code == "java" and state(ws) == before


def test_a_commit_that_fails_after_the_snapshot_restore_puts_the_old_index_back(history, monkeypatch):
    ws, shas = history
    before = state(ws)

    def fail(*a, **k):
        raise RuntimeError("git commit failed")

    monkeypatch.setattr(revert_mod, "_commit", fail)
    with pytest.raises(IndexUnavailable):
        revert_wiki(ws.workspace, shas["A"])
    assert state(ws) == before


# ── Guards ───────────────────────────────────────────────────────────────────

def test_uncommitted_changes_are_refused_with_the_list_of_files(history):
    ws, shas = history
    (ws.workspace / "wiki" / "concepts" / "glass-slipper.md").write_text("# edited by hand\n")
    (ws.workspace / "wiki" / "concepts" / "new-note.md").write_text("# new\n")
    before = state(ws)
    with pytest.raises(DirtyWiki) as exc:
        revert_wiki(ws.workspace, shas["A"])
    assert exc.value.files == ["wiki/concepts/glass-slipper.md", "wiki/concepts/new-note.md"]
    assert state(ws) == before and dirty_files(ws.workspace) == exc.value.files


def test_force_discards_the_uncommitted_changes(history):
    ws, shas = history
    (ws.workspace / "wiki" / "concepts" / "glass-slipper.md").write_text("# edited by hand\n")
    (ws.workspace / "wiki" / "concepts" / "new-note.md").write_text("# new\n")
    result = revert_wiki(ws.workspace, shas["A"], force=True)
    assert result.db_via == "snapshot"
    assert not (ws.workspace / "wiki" / "concepts" / "new-note.md").exists()
    assert git(ws.workspace, "status", "--porcelain", "--", "wiki", ".gitignore") == ""
    assert git(ws.workspace, "diff", shas["A"], "HEAD", "--", "wiki") == ""


def test_a_clean_wiki_has_no_dirty_files(history):
    assert dirty_files(history[0].workspace) == []


def test_an_unknown_revision_is_refused(history):
    ws, _ = history
    for bad in ("deadbeef", "", "--all", "HEAD~99"):
        with pytest.raises(UnknownRevision):
            revert_wiki(ws.workspace, bad)


def test_revert_is_unavailable_with_autocommit_off(history, monkeypatch):
    ws, shas = history
    monkeypatch.setenv("WIKI_AUTOCOMMIT", "0")
    with pytest.raises(RevertUnavailable):
        revert_wiki(ws.workspace, shas["A"])


def test_revert_is_unavailable_without_a_repository(tmp_workspace):
    with pytest.raises(RevertUnavailable):
        revert_wiki(tmp_workspace.workspace, "HEAD")


# ── wiki_config.toml is part of a point ──────────────────────────────────────

def _config(ws):
    return ws.workspace / "wiki_config.toml"


def test_a_vocabulary_change_is_committed_and_going_back_restores_it(history):
    from domain.chat.config_writer import add_blacklist_term
    from domain.tools.git_ops import auto_commit, head_sha

    ws, shas = history
    add_blacklist_term(ws.workspace, "cripto")
    auto_commit(ws.workspace, 'vocabulary: blacklist "cripto"')
    with_cripto = head_sha(ws.workspace)
    assert "wiki_config.toml" in git(ws.workspace, "show", "--name-only", "--format=", "HEAD").split()
    add_blacklist_term(ws.workspace, "bitcoin")
    auto_commit(ws.workspace, 'vocabulary: blacklist "bitcoin"')

    assert revert_mod.config_changes(ws.workspace, with_cripto)
    revert_wiki(ws.workspace, with_cripto)
    assert "bitcoin" not in _config(ws).read_text() and "cripto" in _config(ws).read_text()
    assert not revert_mod.config_changes(ws.workspace, with_cripto)


def test_a_point_from_before_the_config_was_tracked_keeps_the_current_config(history):
    from domain.chat.config_writer import add_blacklist_term
    from domain.tools.git_ops import auto_commit

    ws, shas = history
    add_blacklist_term(ws.workspace, "cripto")
    auto_commit(ws.workspace, 'vocabulary: blacklist "cripto"')
    assert not revert_mod.config_changes(ws.workspace, shas["A"])   # A holds no wiki_config.toml
    revert_wiki(ws.workspace, shas["A"])
    assert "cripto" in _config(ws).read_text()                       # not deleted


def test_an_uncommitted_config_change_refuses_the_revert(history):
    from domain.chat.config_writer import add_blacklist_term
    from domain.tools.git_ops import auto_commit

    ws, shas = history
    add_blacklist_term(ws.workspace, "cripto")
    auto_commit(ws.workspace, 'vocabulary: blacklist "cripto"')
    _config(ws).write_text(_config(ws).read_text() + "\n# a hand edit\n")
    assert dirty_files(ws.workspace) == ["wiki_config.toml"]
    with pytest.raises(DirtyWiki):
        revert_wiki(ws.workspace, shas["B"])
