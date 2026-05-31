"""Tests for domain/tools/git_ops — step 2.7."""

import subprocess
from pathlib import Path

import pytest

from domain.tools.git_ops import auto_commit, autocommit_enabled, init_wiki_repo


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_init_wiki_repo_creates_git_dir(tmp_path: Path) -> None:
    init_wiki_repo(tmp_path)
    assert (tmp_path / ".git").is_dir()


def test_init_wiki_repo_creates_gitignore(tmp_path: Path) -> None:
    init_wiki_repo(tmp_path)
    gitignore = tmp_path / ".gitignore"
    assert gitignore.exists()
    assert ".llmwiki/" in gitignore.read_text()


def test_init_wiki_repo_idempotent(tmp_path: Path) -> None:
    init_wiki_repo(tmp_path)
    init_wiki_repo(tmp_path)  # must not raise
    assert (tmp_path / ".git").is_dir()


def test_auto_commit_creates_commit(tmp_path: Path) -> None:
    init_wiki_repo(tmp_path)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "page.md").write_text("# Page\n")
    auto_commit(tmp_path, "test: add page")
    log = _git(["log", "--oneline"], tmp_path)
    assert "test: add page" in log


def test_auto_commit_silent_on_nothing_to_commit(tmp_path: Path) -> None:
    init_wiki_repo(tmp_path)
    (tmp_path / "wiki").mkdir()
    auto_commit(tmp_path, "first commit")
    auto_commit(tmp_path, "second commit — nothing new")  # must not raise


# ── WIKI_AUTOCOMMIT opt-out ───────────────────────────────────────────────────


def test_autocommit_enabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("WIKI_AUTOCOMMIT", raising=False)
    assert autocommit_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE", " Off "])
def test_autocommit_disabled_by_falsy_env(monkeypatch, value: str) -> None:
    monkeypatch.setenv("WIKI_AUTOCOMMIT", value)
    assert autocommit_enabled() is False


def test_disabled_init_skips_git(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WIKI_AUTOCOMMIT", "0")
    init_wiki_repo(tmp_path)
    assert not (tmp_path / ".git").exists()
    assert not (tmp_path / ".gitignore").exists()


def test_disabled_auto_commit_is_noop(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WIKI_AUTOCOMMIT", "0")
    # Manually set up a repo with a seed commit (bypassing the gated init).
    for args in (
        ["init"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git"] + args, cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "seed.txt").write_text("x")
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-m", "seed"], tmp_path)
    # A wiki change + disabled auto_commit must NOT create a new commit.
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "page.md").write_text("# Page\n")
    auto_commit(tmp_path, "should not be committed")  # must not raise
    log = _git(["log", "--oneline"], tmp_path)
    assert "should not be committed" not in log
    assert "seed" in log
