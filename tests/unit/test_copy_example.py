"""`services.wiki.copy_example`: the copy of an example wiki that the picker opens."""

from __future__ import annotations

from pathlib import Path

from services.wiki import copy_example, open_wiki


def _example(root: Path) -> Path:
    wiki = open_wiki(root / "examples" / "tales")
    (wiki.wiki_dir / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    (wiki.path / ".git").mkdir(exist_ok=True)
    (wiki.path / ".git" / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
    return wiki.path


def test_the_copy_holds_the_wiki_and_the_index_but_not_git(tmp_path):
    example = _example(tmp_path)
    dest = copy_example(example, tmp_path / "home")
    assert dest == tmp_path / "home" / "tales"
    assert (dest / "wiki" / "index.md").is_file() and (dest / ".llmwiki" / "index.db").is_file()
    assert not (dest / ".git").exists()


def test_a_second_copy_reuses_the_first_and_keeps_its_changes(tmp_path):
    example = _example(tmp_path)
    first = copy_example(example, tmp_path / "home")
    (first / "wiki" / "index.md").write_text("# Edited\n", encoding="utf-8")
    assert copy_example(example, tmp_path / "home") == first
    assert (first / "wiki" / "index.md").read_text(encoding="utf-8") == "# Edited\n"


def test_the_example_is_not_written(tmp_path):
    example = _example(tmp_path)
    before = sorted((p.relative_to(example), p.stat().st_mtime_ns) for p in example.rglob("*") if p.is_file())
    dest = copy_example(example, tmp_path / "home")
    (dest / "wiki" / "index.md").write_text("# Edited\n", encoding="utf-8")
    after = sorted((p.relative_to(example), p.stat().st_mtime_ns) for p in example.rglob("*") if p.is_file())
    assert before == after
