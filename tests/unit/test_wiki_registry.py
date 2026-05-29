"""Tests for domain/wiki_registry.py — discovery, recent list, path hygiene."""

from pathlib import Path

import pytest

from domain.wiki_registry import (
    clean_path_input,
    discover_wikis,
    is_wiki_dir,
    load_recent,
    merge_options,
    push_recent,
    resolve_wiki_home,
    save_recent,
    short_label,
)


def _make_wiki(root: Path, name: str, marker: str = "wiki") -> Path:
    d = root / name
    (d / marker).mkdir(parents=True)
    return d


# ── is_wiki_dir ──────────────────────────────────────────────────────────────


def test_is_wiki_dir_true_for_wiki_subdir(tmp_path: Path) -> None:
    d = _make_wiki(tmp_path, "w", "wiki")
    assert is_wiki_dir(d)


def test_is_wiki_dir_true_for_llmwiki_subdir(tmp_path: Path) -> None:
    d = _make_wiki(tmp_path, "w", ".llmwiki")
    assert is_wiki_dir(d)


def test_is_wiki_dir_false_for_plain_dir(tmp_path: Path) -> None:
    assert not is_wiki_dir(tmp_path)


# ── clean_path_input ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  /a/b  ", "/a/b"),
        ("'/a/b c'", "/a/b c"),
        ('"/a/b"', "/a/b"),
        ("/a/b", "/a/b"),
        ("'/a/b\"", "'/a/b\""),  # mismatched quotes left untouched
        ("", ""),
    ],
)
def test_clean_path_input(raw: str, expected: str) -> None:
    assert clean_path_input(raw) == expected


# ── recent list round-trip ───────────────────────────────────────────────────


def test_load_recent_missing_returns_empty(tmp_path: Path) -> None:
    assert load_recent(tmp_path / "nope.json") == []


def test_load_recent_malformed_returns_empty(tmp_path: Path) -> None:
    f = tmp_path / "recent.json"
    f.write_text("{not json")
    assert load_recent(f) == []


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    f = tmp_path / "sub" / "recent.json"
    save_recent(["/a", "/b"], f)
    assert load_recent(f) == ["/a", "/b"]


def test_push_recent_dedups_and_fronts(tmp_path: Path) -> None:
    f = tmp_path / "recent.json"
    out = push_recent("/b", ["/a", "/b", "/c"], recent_file=f)
    assert out == ["/b", "/a", "/c"]
    assert load_recent(f) == ["/b", "/a", "/c"]


def test_push_recent_caps(tmp_path: Path) -> None:
    f = tmp_path / "recent.json"
    out = push_recent("/new", [f"/p{i}" for i in range(12)], cap=3, recent_file=f)
    assert out == ["/new", "/p0", "/p1"]


# ── discovery + merge ────────────────────────────────────────────────────────


def test_discover_wikis_finds_children(tmp_path: Path) -> None:
    _make_wiki(tmp_path, "alpha")
    _make_wiki(tmp_path, "beta", ".llmwiki")
    (tmp_path / "not_a_wiki").mkdir()
    found = discover_wikis(tmp_path)
    assert str(tmp_path / "alpha") in found
    assert str(tmp_path / "beta") in found
    assert str(tmp_path / "not_a_wiki") not in found


def test_discover_wikis_includes_home_itself(tmp_path: Path) -> None:
    (tmp_path / "wiki").mkdir()
    assert str(tmp_path) in discover_wikis(tmp_path)


def test_discover_wikis_missing_home_is_safe(tmp_path: Path) -> None:
    assert discover_wikis(tmp_path / "ghost") == []


def test_merge_options_orders_active_first_and_dedups(tmp_path: Path) -> None:
    a = _make_wiki(tmp_path, "alpha")
    merged = merge_options(tmp_path, recent=[str(a), "/recent-only"], active=str(a))
    assert merged[0] == str(a)
    assert merged.count(str(a)) == 1
    assert "/recent-only" in merged


# ── labels + home resolution ─────────────────────────────────────────────────


def test_short_label_truncates() -> None:
    assert short_label("/Users/x/Documents/finanzas/my-wiki") == "…/finanzas/my-wiki"


def test_short_label_keeps_short_paths() -> None:
    assert short_label("/a") == "/a"


def test_resolve_wiki_home_prefers_env_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WIKI_HOME", str(tmp_path))
    assert resolve_wiki_home() == tmp_path.resolve()


def test_resolve_wiki_home_falls_back_to_parent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("WIKI_HOME", raising=False)
    child = tmp_path / "my-wiki"
    child.mkdir()
    assert resolve_wiki_home(str(child)) == tmp_path.resolve()
