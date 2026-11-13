"""The OCR question of quickstart.py, with simulated input. Nothing is installed or run."""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ("http://localhost:11434/v1", "ollama", "llama3.2")


@pytest.fixture
def quickstart(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("quickstart_ocr_under_test", REPO_ROOT / "quickstart.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    return module


def answers(monkeypatch, *replies):
    """Simulated keyboard: each input() call returns the next reply."""
    queue = list(replies)
    monkeypatch.setattr("builtins.input", lambda prompt="": queue.pop(0))
    return queue


def test_the_default_answer_is_no_ocr(quickstart, monkeypatch):
    answers(monkeypatch, "")
    assert quickstart.choose_ocr(None, PROVIDER) == {"OCR_ENGINE": "none"}


def test_choice_2_is_the_local_engine_and_checks_the_programs(quickstart, monkeypatch, capsys):
    answers(monkeypatch, "2")
    monkeypatch.setattr(quickstart.shutil, "which", lambda name: None)
    assert quickstart.choose_ocr(None, PROVIDER) == {"OCR_ENGINE": "tesseract"}
    out = capsys.readouterr().out
    assert "ocrmypdf, tesseract" in out and "tesseract-ocr-spa" in out


def test_the_local_engine_with_the_programs_installed_prints_no_install_command(quickstart, monkeypatch, capsys):
    monkeypatch.setattr(quickstart.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert quickstart.choose_ocr("tesseract", PROVIDER) == {"OCR_ENGINE": "tesseract"}
    assert "apt-get" not in capsys.readouterr().out


@pytest.mark.parametrize("platform, name, expected", [
    ("darwin", "posix", "brew install ocrmypdf tesseract tesseract-lang"),
    ("linux", "posix", "sudo apt-get install ocrmypdf tesseract-ocr tesseract-ocr-spa"),
    ("win32", "nt", "installation.html#windows"),
])
def test_the_install_command_follows_the_operating_system(quickstart, monkeypatch, platform, name, expected):
    monkeypatch.setattr(quickstart.sys, "platform", platform)
    monkeypatch.setattr(quickstart.os, "name", name)
    assert expected in quickstart.ocr_install_command()


def test_the_installer_never_runs_a_command(quickstart, monkeypatch):
    monkeypatch.setattr(quickstart.shutil, "which", lambda name: None)
    monkeypatch.setattr(quickstart.subprocess, "run", lambda *a, **k: pytest.fail("ran a command"))
    quickstart.check_ocr_programs()


def test_choice_3_states_the_cost_asks_for_the_model_and_reuses_the_llm_settings(quickstart, monkeypatch, capsys):
    answers(monkeypatch, "3", "gpt-4o-mini", "y")
    assert quickstart.choose_ocr(None, PROVIDER) == {"OCR_ENGINE": "vision", "OCR_MODEL": "gpt-4o-mini"}
    assert "one model call" in capsys.readouterr().out


def test_choice_3_can_set_its_own_endpoint(quickstart, monkeypatch):
    answers(monkeypatch, "3", "vision-1", "n", "http://ocr/v1", "ocr-key")
    values = quickstart.choose_ocr(None, PROVIDER)
    assert values == {"OCR_ENGINE": "vision", "OCR_MODEL": "vision-1",
                      "OCR_BASE_URL": "http://ocr/v1", "OCR_API_KEY": "ocr-key"}


def test_a_model_is_required_for_the_vision_engine(quickstart, monkeypatch):
    answers(monkeypatch, "", "vision-1", "y")
    assert quickstart.choose_ocr("vision", PROVIDER)["OCR_MODEL"] == "vision-1"


def test_the_ocr_values_are_added_to_an_existing_env_without_touching_the_rest(quickstart, tmp_path):
    (tmp_path / ".env").write_text("WIKI_PATH=/w\nLLM_MODEL=m\nOCR_ENGINE=none\n", encoding="utf-8")
    quickstart.write_ocr_env({"OCR_ENGINE": "vision", "OCR_MODEL": "v"})
    lines = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["WIKI_PATH=/w", "LLM_MODEL=m"]
    assert lines.count("OCR_ENGINE=vision") == 1 and "OCR_ENGINE=none" not in lines and "OCR_MODEL=v" in lines


def test_running_the_writer_twice_does_not_duplicate_lines(quickstart, tmp_path):
    for _ in range(2):
        quickstart.write_ocr_env({"OCR_ENGINE": "tesseract"})
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert text.count("OCR_ENGINE=tesseract") == 1 and text.count("# OCR") == 1


def test_the_flag_skips_the_prompt(quickstart, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("asked"))
    assert quickstart.choose_ocr("none", PROVIDER) == {"OCR_ENGINE": "none"}
    assert quickstart.parse_args(["--ocr", "vision"]).ocr == "vision"
