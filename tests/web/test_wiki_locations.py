"""`WIKI_PATH` and `WIKI_HOME` come from the environment or, failing that, from `.env`
(`web.settings.wiki_locations`). Before, a value written only in `.env` had no effect."""

from __future__ import annotations

from domain.wiki_registry import resolve_wiki_home
from web.settings import read_env, wiki_locations


def test_wiki_home_from_env_file_when_the_environment_has_none(tmp_path):
    assert wiki_locations({}, {"WIKI_HOME": str(tmp_path)}) == (None, str(tmp_path))


def test_the_environment_wins_over_the_env_file(tmp_path):
    env = {"WIKI_HOME": "/from/environment", "WIKI_PATH": "/env/wiki"}
    values = {"WIKI_HOME": str(tmp_path), "WIKI_PATH": str(tmp_path)}
    assert wiki_locations(env, values) == ("/env/wiki", "/from/environment")


def test_wiki_path_from_env_file_only_when_its_folder_exists(tmp_path):
    assert wiki_locations({}, {"WIKI_PATH": str(tmp_path)}) == (str(tmp_path), "")
    assert wiki_locations({}, {"WIKI_PATH": "/path/to/your/wiki"}) == (None, "")


def test_read_env_reads_a_quoted_windows_path(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("WIKI_HOME='C:/Users/ana/Documents/LLM Wiki'\n# comment\nLLM_MODEL=\n", encoding="utf-8")
    assert read_env(env_file) == {"WIKI_HOME": "C:/Users/ana/Documents/LLM Wiki", "LLM_MODEL": ""}
    assert read_env(tmp_path / "missing") == {}


def test_resolve_wiki_home_takes_the_given_home_over_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_HOME", "/somewhere/else")
    assert resolve_wiki_home(None, str(tmp_path)) == tmp_path.resolve()
    assert resolve_wiki_home(None, "") != tmp_path.resolve()
