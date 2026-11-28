"""`scripts/install_steps.py` and `scripts/open_web.py`: the Python steps of the installer.
No model, no network; the server and the browser of `open_web` are fakes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from domain.wiki_registry import is_wiki_dir
from services.wiki import open_wiki
from web.settings import read_env

ROOT = Path(__file__).resolve().parents[2]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install = load("install_steps")
open_web = load("open_web")


@pytest.fixture
def repo(tmp_path):
    """A repository folder with two example wikis."""
    root = tmp_path / "repo"
    for name in ("fairy-tales", "cuentos"):
        wiki = open_wiki(root / "examples" / name)
        (wiki.wiki_dir / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    return root


def test_check_passes_on_the_repository():
    lines = install.check()
    assert lines[-1] == "The web interface answers and lists the example wikis."
    assert any("fairy-tales" in line for line in lines)


def test_check_fails_without_examples(tmp_path):
    with pytest.raises(install.InstallError, match="no example wiki"):
        install.check(tmp_path)


def test_resolve_folder(tmp_path):
    assert install.resolve_folder("LLM Wiki", home=tmp_path) == (tmp_path / "LLM Wiki").resolve()
    assert install.resolve_folder(f'"{tmp_path}/w"') == (tmp_path / "w").resolve()
    with pytest.raises(install.InstallError):
        install.resolve_folder("  ")


def test_prepare_wikis_copies_the_examples_and_writes_wiki_home(repo, tmp_path):
    folder = tmp_path / "Documents" / "LLM Wiki"
    env_file = repo / ".env"
    env_file.write_text("LLM_MODEL=llama3.2\n", encoding="utf-8")
    lines = install.prepare_wikis(folder, root=repo, env_file=env_file)
    assert is_wiki_dir(folder / "fairy-tales") and is_wiki_dir(folder / "cuentos")
    assert read_env(env_file) == {"LLM_MODEL": "llama3.2", "WIKI_HOME": str(folder)}
    assert any(line.startswith("Copied the example wiki") for line in lines)


def test_prepare_wikis_twice_keeps_the_wikis_already_there(repo, tmp_path):
    folder = tmp_path / "wikis"
    install.prepare_wikis(folder, root=repo, env_file=repo / ".env")
    (folder / "fairy-tales" / "wiki" / "index.md").write_text("# Edited\n", encoding="utf-8")
    lines = install.prepare_wikis(folder, root=repo, env_file=repo / ".env")
    assert (folder / "fairy-tales" / "wiki" / "index.md").read_text(encoding="utf-8") == "# Edited\n"
    assert sum(line.startswith("Kept the wiki") for line in lines) == 2
    assert read_env(repo / ".env") == {"WIKI_HOME": str(folder)}


def test_prepare_wikis_reports_a_folder_it_cannot_create(repo, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(install.InstallError, match="cannot be created"):
        install.prepare_wikis(blocker / "wikis", root=repo, env_file=repo / ".env")


@pytest.mark.parametrize("system", ["windows", "unix"])
def test_the_final_message_sends_the_user_to_the_settings_tab(system, tmp_path):
    text = "\n".join(install.final_message(system, tmp_path))
    assert "Settings tab" in text and "Test" in text and str(tmp_path) in text
    assert ("open_web.cmd" if system == "windows" else "open_web.sh") in text


def test_the_shortcuts_run_open_web_with_the_web_group(tmp_path):
    windows = install.write_shortcut("windows", r"C:\Users\Ánà\.local\bin\uv.exe", root=tmp_path,
                                     profile=r"C:\Users\Ánà")
    unix = install.write_shortcut("unix", "/home/ana/.local/bin/uv", root=tmp_path)
    assert windows.name == "open_web.cmd" and b"\r\n" in windows.read_bytes()
    text = windows.read_text(encoding="utf-8")
    assert '"%UV%" run --group web python scripts\\open_web.py' in text
    assert r'set "UV=%USERPROFILE%\.local\bin\uv.exe"' in text and text.isascii()
    assert unix.name == "open_web.sh" and unix.stat().st_mode & 0o111
    assert "run --group web python scripts/open_web.py" in unix.read_text(encoding="utf-8")


def test_main_prints_an_error_and_exits_with_1(capsys):
    assert install.main(["wikis", "--folder", "   "]) == 1
    assert capsys.readouterr().out.startswith("ERROR:")


def test_open_web_opens_the_browser_once_the_server_answers():
    states, opened = iter([False, False, True]), []
    assert open_web.open_when_ready("http://localhost:8765/", 8765, open_url=opened.append,
                                    is_up=lambda port: next(states), sleep=lambda s: None)
    assert opened == ["http://localhost:8765/"]


def test_open_web_gives_up_when_the_server_never_answers():
    opened = []
    assert not open_web.open_when_ready("http://x/", 1, timeout=1, open_url=opened.append,
                                        is_up=lambda port: False, sleep=lambda s: None)
    assert opened == []
