"""Tests for the history and rollback services in services/wiki.py, and scripts/wiki_revert.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from domain.rollback import snapshots
from domain.rollback.revert import DirtyWiki
from services import wiki as wiki_service
from tests.helpers.rollback import build_history, git

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "wiki_revert.py"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv("WIKI_AUTOCOMMIT", raising=False)
    monkeypatch.delenv("WIKI_SNAPSHOT_KEEP", raising=False)


@pytest.fixture
def history(tmp_workspace, monkeypatch):
    shas = build_history(tmp_workspace, monkeypatch)
    return wiki_service.open_wiki(tmp_workspace.workspace), shas


def test_history_lists_the_commits_that_touched_wiki_newest_first(history):
    wiki, shas = history
    commits = wiki_service.history(wiki)
    assert [c.sha for c in commits] == [shas["C"], shas["B"], shas["A"]]
    assert [c.message for c in commits] == ["delete: concepts/fairy-godmother", "ingest: Tale Two.pdf",
                                            "ingest: Tale One.pdf"]
    assert all(c.has_snapshot and len(c.short) == 7 and c.date for c in commits)
    deleted = commits[0]
    assert "concepts/fairy-godmother" in deleted.pages and "index" in deleted.pages
    assert all(f.startswith("wiki/") for f in deleted.files)


def test_history_marks_the_commits_whose_snapshot_is_gone(history):
    wiki, shas = history
    snapshots._snapshot_file(wiki.path, shas["A"]).unlink()
    assert {c.sha: c.has_snapshot for c in wiki_service.history(wiki)} == {
        shas["C"]: True, shas["B"]: True, shas["A"]: False}


def test_history_limit(history):
    assert len(wiki_service.history(history[0], limit=2)) == 2


def test_history_is_empty_without_a_repository_or_with_autocommit_off(tmp_workspace):
    assert wiki_service.history(wiki_service.open_wiki(tmp_workspace.workspace)) == []


def test_page_history_lists_only_the_commits_that_touched_the_page(history):
    wiki, shas = history
    assert [c.sha for c in wiki_service.page_history(wiki, "concepts/glass-slipper")] == [shas["A"]]
    commits = wiki_service.page_history(wiki, "concepts/fairy-godmother")
    assert [c.sha for c in commits] == [shas["C"], shas["A"]]
    assert commits[0].pages == ("concepts/fairy-godmother",)       # files limited to the page


def test_page_at_returns_the_text_the_commit_had(history):
    wiki, shas = history
    assert "Fairy Godmother" in wiki_service.page_at(wiki, "concepts/fairy-godmother", shas["B"])
    assert wiki_service.page_at(wiki, "concepts/fairy-godmother", shas["C"]) == ""
    assert wiki_service.page_at(wiki, "concepts/royal-ball", shas["A"]) == ""
    assert wiki_service.page_at(wiki, "concepts/royal-ball", shas["B"][:8]).startswith("---")


@pytest.mark.parametrize("sha", ["--all", "HEAD", "", "zzzz", "0" * 40])
def test_page_at_refuses_anything_but_a_commit_id(history, sha):
    with pytest.raises(ValueError):
        wiki_service.page_at(history[0], "index", sha)


def test_page_at_cannot_leave_wiki(history):
    wiki, shas = history
    with pytest.raises(ValueError):
        wiki_service.page_at(wiki, "../.gitignore", shas["A"])


def test_changes_since_names_the_pages_added_changed_and_removed(history):
    wiki, shas = history
    since_a = wiki_service.changes_since(wiki, shas["A"])
    assert "concepts/royal-ball" in since_a.added and "summaries/tale-two" in since_a.added
    assert "concepts/fairy-godmother" in since_a.removed
    assert "index" in since_a.changed
    since_b = wiki_service.changes_since(wiki, shas["B"])
    assert since_b.added == () and since_b.removed == ("concepts/fairy-godmother",)


def test_revert_plan_says_how_the_index_comes_back_and_what_stops_it(history, monkeypatch):
    wiki, shas = history
    plan = wiki_service.revert_plan(wiki, shas["A"])
    assert (plan.db_via, plan.problem) == ("snapshot", "") and plan.changes.removed

    (wiki.wiki_dir / "concepts" / "glass-slipper.md").write_text("# by hand\n")
    dirty = wiki_service.revert_plan(wiki, shas["A"])
    assert dirty.problem_code == "dirty" and dirty.files == ("wiki/concepts/glass-slipper.md",)
    git(wiki.path, "checkout", "--", "wiki")

    snapshots._snapshot_file(wiki.path, shas["A"]).unlink()
    from domain.ingestion import extractor
    monkeypatch.setattr(extractor, "check_java", lambda: None)
    blocked = wiki_service.revert_plan(wiki, shas["A"])
    assert blocked.problem_code == "index" and blocked.db_via is None and "Java" in blocked.problem


def test_service_revert_runs_the_revert_and_reports_progress(history):
    wiki, shas = history
    lines: list[str] = []
    result = wiki_service.revert(wiki, shas["A"], progress=lines.append)
    assert result.db_via == "snapshot" and lines
    assert wiki_service.history(wiki)[0].message == f"revert: restore wiki to {shas['A'][:7]}"
    assert wiki_service.read_page(wiki, "concepts/royal-ball") == ""


def test_service_revert_refuses_uncommitted_changes(history):
    wiki, shas = history
    (wiki.wiki_dir / "concepts" / "glass-slipper.md").write_text("# by hand\n")
    with pytest.raises(DirtyWiki):
        wiki_service.revert(wiki, shas["A"])
    assert wiki_service.uncommitted(wiki) == ["wiki/concepts/glass-slipper.md"]


# ── scripts/wiki_revert.py ───────────────────────────────────────────────────

def load_script():
    spec = importlib.util.spec_from_file_location("wiki_revert", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_lists_and_reverts(history, capsys):
    wiki, shas = history
    script = load_script()
    assert script.main([str(wiki.path), "--list"]) == 0
    assert shas["B"][:7] in capsys.readouterr().out
    assert script.main([str(wiki.path), "--to", "HEAD~1"]) == 0
    out = capsys.readouterr().out
    assert f"restored to {shas['B'][:7]}" in out and "Index: snapshot" in out
    assert wiki_service.read_page(wiki, "concepts/fairy-godmother")


def test_script_refuses_a_dirty_wiki_and_an_unknown_revision(history, capsys):
    wiki, shas = history
    script = load_script()
    (wiki.wiki_dir / "concepts" / "glass-slipper.md").write_text("# by hand\n")
    assert script.main([str(wiki.path), "--to", shas["A"]]) == 2
    assert "uncommitted" in capsys.readouterr().err
    assert script.main([str(wiki.path), "--to", shas["A"], "--force"]) == 0
    assert script.main([str(wiki.path), "--to", "nonsense"]) == 1
