"""Tests for domain/rollback/snapshots.py and its wiring into auto_commit."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from domain.rollback import snapshots
from domain.tools.db import get_connection, open_db
from domain.tools.git_ops import auto_commit, head_sha, init_wiki_repo
from tests.helpers.rollback import build_history, db_view

SHA = "a" * 40


def sha(n: int) -> str:
    return f"{n:040x}"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv("WIKI_AUTOCOMMIT", raising=False)
    monkeypatch.delenv("WIKI_SNAPSHOT_KEEP", raising=False)


# ── capture / restore ────────────────────────────────────────────────────────

def test_capture_writes_a_snapshot_that_restore_puts_back(tmp_workspace):
    ws = tmp_workspace
    snap = snapshots.capture(ws.workspace, SHA)
    assert snap and snap.db_path == ws.workspace / ".llmwiki" / "snapshots" / f"{SHA}.db" and snap.size_bytes > 0
    with get_connection(ws.db_path) as conn:
        conn.execute("UPDATE workspace SET name = 'changed'")
        conn.commit()
    assert snapshots.restore(ws.workspace, SHA) is True
    with get_connection(ws.db_path) as conn:
        assert conn.execute("SELECT name FROM workspace").fetchone()[0] == "test-workspace"


def test_capture_sees_frames_that_no_checkpoint_has_written(tmp_workspace):
    """The database is in WAL mode; a file copy of index.db would miss the committed rows."""
    ws = tmp_workspace
    conn = open_db(ws.db_path)
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("UPDATE workspace SET name = 'in-the-wal'")
    conn.commit()
    wal = Path(ws.db_path + "-wal")
    assert wal.exists() and wal.stat().st_size > 0
    try:
        raw = sqlite3.connect(f"file:{ws.db_path}?immutable=1", uri=True)   # the main file alone
        assert raw.execute("SELECT name FROM workspace").fetchone()[0] == "test-workspace"
        raw.close()
        snap = snapshots.capture(ws.workspace, SHA)
    finally:
        conn.close()
    copy = sqlite3.connect(snap.db_path)
    assert copy.execute("SELECT name FROM workspace").fetchone()[0] == "in-the-wal"
    copy.close()
    assert not Path(f"{snap.db_path}-wal").exists()   # a single self-contained file


def test_restore_drops_the_stale_wal_of_the_old_database(tmp_workspace):
    ws = tmp_workspace
    snapshots.capture(ws.workspace, SHA)
    conn = open_db(ws.db_path)
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("UPDATE workspace SET name = 'later'")
    conn.commit()
    conn.close()
    Path(ws.db_path + "-wal").write_bytes(b"\0" * 64)   # leftovers of the old index
    assert snapshots.restore(ws.workspace, SHA)
    assert not Path(ws.db_path + "-wal").exists()
    with get_connection(ws.db_path) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("SELECT name FROM workspace").fetchone()[0] == "test-workspace"


def test_restore_returns_false_for_a_missing_or_damaged_snapshot_and_leaves_the_index(tmp_workspace):
    ws = tmp_workspace
    before = Path(ws.db_path).read_bytes()
    assert snapshots.restore(ws.workspace, SHA) is False
    snapshots.capture(ws.workspace, SHA)
    (ws.workspace / ".llmwiki" / "snapshots" / f"{SHA}.db").write_bytes(b"not a database" * 50)
    assert snapshots.restore(ws.workspace, SHA) is False
    assert Path(ws.db_path).read_bytes() == before


def test_capture_returns_none_without_an_index(tmp_path):
    assert snapshots.capture(tmp_path, SHA) is None


def test_a_sha_must_be_a_full_sha(tmp_workspace):
    with pytest.raises(ValueError):
        snapshots.capture(tmp_workspace.workspace, "../../etc/passwd")


# ── retention ────────────────────────────────────────────────────────────────

def test_prune_keeps_the_newest(tmp_workspace):
    ws = tmp_workspace
    for n in range(7):
        snapshots.capture(ws.workspace, sha(n))
    assert [s.commit_sha for s in snapshots.list_snapshots(ws.workspace)] == [sha(n) for n in range(6, -1, -1)]
    assert snapshots.prune(ws.workspace, keep=5) == 2
    kept = [s.commit_sha for s in snapshots.list_snapshots(ws.workspace)]
    assert kept == [sha(n) for n in range(6, 1, -1)]
    assert not (ws.workspace / ".llmwiki" / "snapshots" / f"{sha(0)}.db").exists()
    assert snapshots.prune(ws.workspace, keep=5) == 0


def test_list_survives_a_lost_manifest(tmp_workspace):
    ws = tmp_workspace
    snapshots.capture(ws.workspace, sha(1))
    snapshots.capture(ws.workspace, sha(2))
    (ws.workspace / ".llmwiki" / "snapshots" / "manifest.json").write_text("{broken")
    assert [s.commit_sha for s in snapshots.list_snapshots(ws.workspace)] == [sha(2), sha(1)]


@pytest.mark.parametrize("value, expected", [("", 5), ("3", 3), ("0", 0), ("-2", 5), ("many", 5)])
def test_keep_comes_from_the_environment(monkeypatch, value, expected):
    monkeypatch.setenv("WIKI_SNAPSHOT_KEEP", value)
    assert snapshots.snapshot_keep() == expected
    assert snapshots.snapshots_enabled() is (expected > 0)


def test_keep_zero_disables_capture(tmp_workspace, monkeypatch):
    monkeypatch.setenv("WIKI_SNAPSHOT_KEEP", "0")
    assert snapshots.capture(tmp_workspace.workspace, SHA) is None
    assert snapshots.list_snapshots(tmp_workspace.workspace) == []


# ── auto_commit wiring ───────────────────────────────────────────────────────

def test_a_commit_captures_a_snapshot_paired_with_it_and_old_ones_are_pruned(tmp_workspace, monkeypatch):
    ws = tmp_workspace
    monkeypatch.setenv("WIKI_SNAPSHOT_KEEP", "2")
    init_wiki_repo(ws.workspace)
    shas = []
    for n in range(3):
        (ws.workspace / "wiki" / f"p{n}.md").write_text(f"# P{n}\n")
        auto_commit(ws.workspace, f"add p{n}")
        shas.append(head_sha(ws.workspace))
    assert [s.commit_sha for s in snapshots.list_snapshots(ws.workspace)] == [shas[2], shas[1]]


def test_a_commit_that_changes_nothing_captures_nothing(tmp_workspace):
    ws = tmp_workspace
    init_wiki_repo(ws.workspace)
    auto_commit(ws.workspace, "first")
    before = snapshots.list_snapshots(ws.workspace)
    auto_commit(ws.workspace, "nothing")
    assert snapshots.list_snapshots(ws.workspace) == before


def test_no_snapshot_when_autocommit_is_off(tmp_workspace, monkeypatch):
    monkeypatch.setenv("WIKI_AUTOCOMMIT", "0")
    auto_commit(tmp_workspace.workspace, "x")
    assert snapshots.list_snapshots(tmp_workspace.workspace) == []
    assert not (tmp_workspace.workspace / ".git").exists()


def test_no_snapshot_when_keep_is_zero_but_the_commit_happens(tmp_workspace, monkeypatch):
    ws = tmp_workspace
    monkeypatch.setenv("WIKI_SNAPSHOT_KEEP", "0")
    init_wiki_repo(ws.workspace)
    (ws.workspace / "wiki" / "p.md").write_text("# P\n")
    auto_commit(ws.workspace, "add p")
    assert head_sha(ws.workspace) and snapshots.list_snapshots(ws.workspace) == []


def test_a_failing_snapshot_never_fails_the_commit(tmp_workspace, monkeypatch, caplog):
    ws = tmp_workspace
    init_wiki_repo(ws.workspace)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(snapshots, "capture", boom)
    (ws.workspace / "wiki" / "p.md").write_text("# P\n")
    with caplog.at_level("WARNING"):
        auto_commit(ws.workspace, "add p")      # must not raise
    assert head_sha(ws.workspace)
    assert "snapshot failed" in caplog.text.lower()


def test_each_commit_of_a_real_history_has_the_index_of_its_moment(tmp_workspace, monkeypatch):
    ws = tmp_workspace
    shas = build_history(ws, monkeypatch)
    paths = {c: {p for kind, p, _ in db_view(snapshots._snapshot_file(ws.workspace, shas[c]))["documents"]
                 if kind == "wiki"} for c in "ABC"}
    assert "wiki/concepts/royal-ball.md" not in paths["A"] and "wiki/concepts/royal-ball.md" in paths["B"]
    assert "wiki/concepts/fairy-godmother.md" in paths["B"] and "wiki/concepts/fairy-godmother.md" not in paths["C"]
