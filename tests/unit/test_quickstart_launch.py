"""Tests for the command quickstart.py prints and runs at the end of the install.

The installer used to launch `marimo/read_app.py`, the three-column app, while
the README screenshots and the rest of the documentation show the tabbed app.
A new user therefore met a different interface from the one advertised. No LLM.
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


def test_launch_command_names_the_tabbed_app():
    quickstart = _load_quickstart()
    command = quickstart.launch_command(REPO_ROOT / ".venv", 2720)
    assert "marimo/read_app_tabs.py" in command
    assert "marimo/read_app.py" not in command


def test_launch_command_carries_the_port_and_disables_the_sandbox():
    quickstart = _load_quickstart()
    command = quickstart.launch_command(REPO_ROOT / ".venv", 2999)
    assert "--no-sandbox" in command
    assert command[command.index("--port") + 1] == "2999"


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
