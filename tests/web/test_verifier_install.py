"""Mantener: the line «Verificador NLI» and the installation of the verifier from the screen."""

from __future__ import annotations

import pytest

from domain.verify import install
from tests.web.conftest import WIKI_ID
from tests.web.test_ingest import lines_of

URL = f"/w/{WIKI_ID}/maintain"
INSTALL = f"/w/{WIKI_ID}/verifier/install"
OLD_HELP = "quickstart.py --with-verifier"


@pytest.fixture
def status(monkeypatch):
    """The status of the verifier the screens see, settable by the test."""
    current = {"value": "missing"}
    monkeypatch.setattr(install, "status", lambda: current["value"])
    return current


@pytest.fixture
def commands(app, status):
    """The commands the installation runs: recorded, and successful unless the test says."""
    ran = []

    def run(command, progress):
        ran.append(command)
        progress(f"output of {command[0]}")
        if command[1] == "-c":  # the download of the model is the last step
            status["value"] = "installed"
        return 0

    app.state.web.run_command = run
    return ran


def test_a_missing_verifier_shows_its_status_and_the_button(client, status):
    html = client.get(URL).text
    assert "Verificador NLI" in html and "No está instalado" in html
    assert 'id="install-verifier"' in html and f'hx-post="{INSTALL}"' in html
    assert "550 MB" in html


def test_a_verifier_without_its_model_says_so(client, status):
    status["value"] = "packages"
    html = client.get(URL).text
    assert "Paquetes instalados, falta el modelo" in html and 'id="install-verifier"' in html


def test_an_installed_verifier_has_no_button(client, status):
    status["value"] = "installed"
    html = client.get(URL).text
    assert "Instalado" in html and 'id="install-verifier"' not in html


def test_the_installation_runs_every_step_and_the_verifier_is_found_without_a_restart(client, app, commands, status):
    state = app.state.web
    state._local_verifier_installed = False  # the probe made when the server started
    lines = lines_of(client, client.post(INSTALL))
    assert len(commands) >= 2 and commands[-1][1] == "-c"
    assert all("sync" not in command for command in commands)
    assert "→ Instalando los paquetes del verificador" in lines and "→ Bajando el modelo NLI" in lines
    assert lines[-1].startswith("✓ El verificador está instalado")
    assert state._local_verifier_installed is None  # the next `verifier_available` probes again


def test_a_failed_step_is_named_and_the_rest_does_not_run(client, app, status):
    ran = []
    app.state.web.run_command = lambda command, progress: ran.append(command) or 1
    lines = lines_of(client, client.post(INSTALL))
    assert len(ran) == 1
    assert any("terminó con el código 1" in line and "no quedó instalado" in line for line in lines)
    assert status["value"] == "missing"


def test_a_second_installation_waits_for_the_first(client, app, status):
    lock = app.state.web.install_lock
    lock.acquire()
    try:
        html = client.post(INSTALL).text
    finally:
        lock.release()
    assert "Ya hay una instalación del verificador en curso." in html


def test_no_screen_gives_the_old_install_command(client, wiki, status, monkeypatch):
    from domain.verify import nli
    monkeypatch.setattr(nli, "is_available", lambda: False)
    # The page screen holds the chat and the page's own verification button.
    for path in ("ingest", "maintain", "pages/concepts/cinderella"):
        response = client.get(f"/w/{WIKI_ID}/{path}")
        assert response.status_code == 200, path
        assert OLD_HELP not in response.text and "group verify" not in response.text, path
        assert "Se instala desde Mantener" in response.text, path
