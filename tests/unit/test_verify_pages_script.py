"""scripts/verify_pages.py, run with the fake verifier over a copy of the example wiki."""

import importlib.util
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("verify_pages_under_test", REPO_ROOT / "scripts" / "verify_pages.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_verifies_every_page_with_sources_and_prints_the_table(script, tmp_path, capsys):
    wiki = tmp_path / "finanzas"
    shutil.copytree(REPO_ROOT / "examples" / "finanzas-argentinas", wiki)
    assert script.main(["--wiki", str(wiki), "--verifier", "fake"]) == 0
    out = capsys.readouterr().out
    assert "| concepts/plazo-fijo-uva | concept |" in out and "| summaries/10-plazos-fijos | summary |" in out
    assert "Pages in the wiki: 35; verified: 35" in out
    assert "saved by the pre-selection" in out


def test_without_the_local_model_the_script_says_how_to_install_it(script, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(script.nli, "is_available", lambda: False)
    assert script.main(["--wiki", str(tmp_path / "w")]) == 2
    assert "uv sync --group verify" in capsys.readouterr().err
