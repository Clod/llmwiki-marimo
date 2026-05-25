"""Tests for domain/tools/git_ops — step 2.7."""

import subprocess
from pathlib import Path

from domain.tools.git_ops import auto_commit, init_wiki_repo


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
