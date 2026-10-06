"""Tests for the command quickstart.py prints and runs at the end of the install.

The installer launches the web interface (`web.app:app`, served by uvicorn), the
interface of the project. No LLM.
"""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_quickstart():
    spec = importlib.util.spec_from_file_location(
        "quickstart_under_test", REPO_ROOT / "quickstart.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launch_command_names_the_web_application():
    quickstart = _load_quickstart()
    command = quickstart.launch_command(REPO_ROOT / ".venv", 2720)
    assert command[1:4] == ["-m", "uvicorn", "web.app:app"]
    assert not any("marimo" in part for part in command[1:])


def test_launch_command_runs_the_python_of_the_venv():
    quickstart = _load_quickstart()
    command = quickstart.launch_command(REPO_ROOT / ".venv", 2720)
    assert command[0] == str(quickstart.venv_python(REPO_ROOT / ".venv"))


def test_launch_command_carries_the_port():
    quickstart = _load_quickstart()
    command = quickstart.launch_command(REPO_ROOT / ".venv", 2999)
    assert command[command.index("--port") + 1] == "2999"


def test_the_browser_opens_on_the_root_of_the_server(monkeypatch):
    """The root of the application is the wiki picker."""
    quickstart = _load_quickstart()
    opened = []
    monkeypatch.setattr(quickstart.webbrowser, "open", opened.append)
    quickstart.open_browser_soon("http://localhost:2720", delay=0)
    import time
    time.sleep(0.3)
    assert opened == ["http://localhost:2720"]


def test_the_requirements_file_installs_the_web_group():
    """quickstart installs requirements.txt; the web interface needs these."""
    pinned = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for name in ("fastapi==", "uvicorn==", "jinja2==", "sse-starlette==", "nh3=="):
        assert name in pinned


def test_java_runtime_check_warns_instead_of_dying(monkeypatch, capsys):
    """A missing Java runtime must not block an install: the demos are pre-ingested."""
    quickstart = _load_quickstart()
    monkeypatch.setattr(quickstart.shutil, "which", lambda name: None)
    monkeypatch.delenv("JAVA_HOME", raising=False)

    quickstart.check_java_runtime()

    printed = capsys.readouterr().out
    assert "No Java runtime found" in printed
    assert "temurin" in printed


def test_java_runtime_check_is_silent_when_java_is_installed(monkeypatch, capsys):
    quickstart = _load_quickstart()
    monkeypatch.setattr(quickstart.shutil, "which", lambda name: "/usr/bin/java")

    quickstart.check_java_runtime()

    assert capsys.readouterr().out == ""


def test_venv_support_check_names_the_debian_package(monkeypatch, capsys):
    """Debian/Ubuntu ship python3 without ensurepip, so `python3 -m venv` fails
    after the demo and provider prompts. The installer must say which package
    fixes it before it gets there."""
    import pytest
    quickstart = _load_quickstart()
    monkeypatch.setattr(quickstart.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(SystemExit):
        quickstart.check_venv_support()

    printed = capsys.readouterr().out
    assert "python3-venv" in printed


def test_venv_support_check_is_silent_when_ensurepip_exists(capsys):
    quickstart = _load_quickstart()
    quickstart.check_venv_support()
    assert capsys.readouterr().out == ""


def _terminal(monkeypatch, quickstart, *, tty=True, nt=False, **env):
    monkeypatch.setattr(quickstart.sys.stdout, "isatty", lambda: tty, raising=False)
    monkeypatch.setattr(quickstart.os, "name", "nt" if nt else "posix")
    for name in ("NO_COLOR", "TERM", "WT_SESSION", "ANSICON", "ConEmuANSI"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def test_output_has_no_ansi_sequences_when_stdout_is_not_a_terminal(monkeypatch, capsys):
    quickstart = _load_quickstart()
    _terminal(monkeypatch, quickstart, tty=False)
    quickstart.step(1, 7, "Checking Python")
    assert "\033" not in capsys.readouterr().out
    quickstart.say(quickstart.style("32", "done"))
    assert capsys.readouterr().out == "done\n"


def test_output_has_no_ansi_sequences_in_a_classic_windows_console(monkeypatch):
    quickstart = _load_quickstart()
    _terminal(monkeypatch, quickstart, nt=True)
    assert quickstart.style("1", "x") == "x"


def test_color_is_kept_on_a_terminal_that_renders_it(monkeypatch):
    quickstart = _load_quickstart()
    _terminal(monkeypatch, quickstart, TERM="xterm-256color")
    assert quickstart.style("1", "x") == "\033[1mx\033[0m"
    _terminal(monkeypatch, quickstart, nt=True, WT_SESSION="abc")
    assert quickstart.style("1", "x") == "\033[1mx\033[0m"


def test_no_color_and_a_dumb_terminal_turn_color_off(monkeypatch):
    quickstart = _load_quickstart()
    _terminal(monkeypatch, quickstart, NO_COLOR="1")
    assert quickstart.style("1", "x") == "x"
    _terminal(monkeypatch, quickstart, TERM="dumb")
    assert quickstart.style("1", "x") == "x"
