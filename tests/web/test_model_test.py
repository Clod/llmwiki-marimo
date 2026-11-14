"""Configuración, block Models: the test button, the help button and the save button of
each model. `services.model_test.run` is replaced: no test calls a model."""

from __future__ import annotations

import dataclasses
import re
from types import SimpleNamespace

import pytest

from domain.modeltest import Check, Report
from services import model_test
from tests.web.conftest import WIKI_ID
from tests.web.test_ingest import lines_of

URL = f"/w/{WIKI_ID}/settings"
ENV = ("OTHER=1\nLLM_BASE_URL=http://saved.invalid/v1\nLLM_API_KEY=saved-key\nLLM_MODEL=saved-model\n"
       "WIKI_LLM_API_KEY=saved-ingest-key\n")
FORM = {"llm_base_url": "https://read.invalid/v1", "llm_api_key": "", "llm_model": "read-model",
        "wiki_llm_base_url": "", "wiki_llm_api_key": "", "wiki_llm_model": "",
        "vision_base_url": "", "vision_api_key": "", "vision_model": "", "ocr_engine": "none"}
PASSED = (Check("answers", True, "the path ends in Q21"), Check("declines", True, "the path ends in R5"),
          Check("uses_tools", True, "the path ends in T5"))
FAILED = (Check("answers", True, "the path ends in Q21"),
          Check("declines", False, "the path ends in Q21, not in R5"),
          Check("uses_tools", True, "the path ends in T5"))
NOT_RUN = (Check("answers", False, "Connection error.", ran=False),
           Check("declines", False, "Connection error.", ran=False),
           Check("uses_tools", False, "Connection error.", ran=False))


def run_url(group: str) -> str:
    return f"/w/{WIKI_ID}/settings/models/{group}/test"


def save_url(group: str) -> str:
    return f"/w/{WIKI_ID}/settings/models/{group}"


@pytest.fixture
def env_file(app, tmp_path):
    """The application reads and writes the models of a `.env` of the test."""
    path = tmp_path / ".env"
    path.write_text(ENV, encoding="utf-8")
    state = app.state.web
    state.settings = dataclasses.replace(state.settings, env_file=path)
    return path


@pytest.fixture
def runs(monkeypatch):
    """The calls to `model_test.run`. Each call sends its checks to `progress` and returns
    a report with the checks of `runs.checks` (all passed unless the test changes them)."""
    runs = SimpleNamespace(calls=[], checks=PASSED)

    def run(kind, base_url, api_key, model, language="en", progress=None):
        runs.calls.append({"kind": kind, "base_url": base_url, "api_key": api_key, "model": model,
                           "language": language})
        for check in runs.checks:
            progress(check)
        return Report(kind, model, runs.checks, 12.5)

    monkeypatch.setattr(model_test, "run", run)
    return runs


def run_test(client, group: str, form: dict) -> list[str]:
    return lines_of(client, client.post(run_url(group), data=form))


# ── The screen ───────────────────────────────────────────────────────────────

def test_each_group_has_its_test_help_and_save_buttons_and_the_save_all_button_is_gone(client, env_file):
    html = client.get(URL).text
    for group in ("llm", "wiki_llm", "vision"):
        assert f'hx-post="{run_url(group)}"' in html and f'hx-post="{save_url(group)}"' in html
        assert f'data-help="help-{group}"' in html and f'id="test-{group}"' in html
        assert f'<dialog class="help" id="help-{group}"' in html
    assert html.count(">Probar</button>") == 3 and html.count('aria-label="Qué comprueba la prueba"') == 3
    assert "Guardar los modelos" not in html and 'action="/w/tales/settings/models"' not in html
    assert "Prueba del modelo de lectura" in html and "Prueba del modelo de ingesta" in html
    assert "Prueba del modelo de visión" in html and "La prueba no comprueba Tesseract" in html


def test_the_ocr_engine_is_inside_the_vision_group(client, env_file):
    html = client.get(URL).text
    assert html.index('name="vision_model"') < html.index('name="ocr_engine"') < html.index(f'hx-post="{run_url("vision")}"')


def test_the_help_is_in_the_language_of_the_interface(en_client, env_file):
    html = en_client.get(URL).text
    assert "Test of the reading model" in html and "Passing is necessary, not sufficient" in html
    assert ">Test</button>" in html and "Prueba del modelo" not in html


# ── The test ─────────────────────────────────────────────────────────────────

def test_the_test_uses_the_form_and_does_not_write_env(client, env_file, runs):
    run_test(client, "llm", {**FORM, "llm_api_key": "typed-key"})
    assert runs.calls == [{"kind": "reading", "base_url": "https://read.invalid/v1", "api_key": "typed-key",
                           "model": "read-model", "language": "en"}]
    assert env_file.read_text(encoding="utf-8") == ENV


def test_an_empty_key_uses_the_saved_key(client, env_file, runs):
    run_test(client, "llm", FORM)
    assert runs.calls[-1]["api_key"] == "saved-key"


def test_an_empty_ingestion_group_uses_the_reading_values_of_the_form(client, env_file, runs):
    run_test(client, "wiki_llm", FORM)
    call = runs.calls[-1]
    assert call["kind"] == "ingestion"
    # The ingestion key saved in `.env` comes before the reading key.
    assert (call["base_url"], call["api_key"], call["model"]) == ("https://read.invalid/v1", "saved-ingest-key", "read-model")
    run_test(client, "vision", {**FORM, "wiki_llm_model": "big-model"})
    call = runs.calls[-1]
    assert call["kind"] == "vision"
    assert (call["base_url"], call["api_key"], call["model"]) == ("https://read.invalid/v1", "saved-ingest-key", "big-model")


def test_the_test_uses_the_language_of_the_wiki(client, env_file, runs, home):
    # Written before the application opens the wiki, which reads its language once.
    (home / WIKI_ID / "wiki_config.toml").write_text('[wiki]\nlanguage = "es"\n', encoding="utf-8")
    run_test(client, "llm", FORM)
    assert runs.calls[-1]["language"] == "es"


def test_without_url_key_or_model_the_test_does_not_run(client, env_file, runs):
    for field in ("llm_base_url", "llm_model"):
        html = client.post(run_url("llm"), data={**FORM, field: ""}).text
        assert "Escribí la URL, la clave y el modelo antes de probar." in html
    env_file.write_text("OTHER=1\n", encoding="utf-8")
    html = client.post(run_url("llm"), data=FORM).text
    assert "Escribí la URL, la clave y el modelo antes de probar." in html
    assert runs.calls == []


def test_a_second_test_waits_for_the_first(client, app, env_file, runs):
    lock = app.state.web.model_test_lock
    lock.acquire()
    try:
        html = client.post(run_url("llm"), data=FORM).text
    finally:
        lock.release()
    assert "Ya hay una prueba en curso." in html and runs.calls == []


def test_an_unknown_group_is_not_found(client, env_file, runs):
    assert client.post(run_url("other"), data=FORM).status_code == 404
    assert client.post(save_url("other"), data=FORM).status_code == 404


# ── The console ──────────────────────────────────────────────────────────────

def test_the_console_of_a_passed_test(client, env_file, runs):
    lines = run_test(client, "llm", FORM)
    assert lines == ["✓ Responde con la página y la cita", "✓ Se niega cuando la página no tiene la respuesta",
                     "✓ Busca en la wiki con las herramientas",
                     "✓ El modelo pasó las 3 comprobaciones (12,5 s). Pasar es necesario, no suficiente."]


def test_the_console_of_a_failed_test(client, env_file, runs):
    runs.checks = FAILED
    lines = run_test(client, "llm", FORM)
    assert lines[1] == "✗ Se niega cuando la página no tiene la respuesta: the path ends in Q21, not in R5"
    assert lines[-1] == "✗ El modelo falló 1 de 3 comprobaciones (12,5 s)."


def test_the_console_when_the_service_gives_no_answer(client, env_file, runs):
    runs.checks = NOT_RUN
    lines = run_test(client, "llm", FORM)
    assert lines[0] == "? Responde con la página y la cita: el servicio no respondió (Connection error.)"
    assert not any(line.startswith("✗") for line in lines)
    assert lines[-1] == "? Las pruebas no pudieron correr: el servicio no respondió."


def test_the_console_names_each_check_of_ingestion_and_vision(client, env_file, runs):
    runs.checks = (Check("ingests", True, "ok"),)
    assert run_test(client, "wiki_llm", FORM)[0] == "✓ Escribe la página de resumen y las páginas de concepto"
    runs.checks = (Check("reads_page", False, "the reading lacks 1 of the 14 data: ZK-4471"),
                   Check("describes", True, "ok"))
    lines = run_test(client, "vision", FORM)
    assert lines[0] == "✗ Lee la página escaneada: the reading lacks 1 of the 14 data: ZK-4471"
    assert lines[1] == "✓ Describe una imagen con el formato pedido"


# ── Saving one group ─────────────────────────────────────────────────────────

def test_saving_untested_values_asks_for_a_confirmation(client, env_file, runs):
    html = client.post(save_url("llm"), data=FORM).text
    assert 'class="confirm' in html and "Estos valores no se probaron. ¿Guardarlos igual?" in html
    assert f'hx-post="{save_url("llm")}"' in html and '"confirm": "1"' in html and "Guardar igual" in html
    assert env_file.read_text(encoding="utf-8") == ENV
    html = client.post(save_url("llm"), data={**FORM, "confirm": "1"}).text
    assert "Guardado en .env" in html
    assert "LLM_MODEL=read-model\n" in env_file.read_text(encoding="utf-8")


def test_saving_after_a_failed_test_asks_for_a_confirmation(client, env_file, runs):
    runs.checks = FAILED
    run_test(client, "llm", FORM)
    html = client.post(save_url("llm"), data=FORM).text
    assert "La última prueba de estos valores falló. ¿Guardarlos igual?" in html
    assert env_file.read_text(encoding="utf-8") == ENV


def test_saving_after_a_test_the_service_did_not_answer_asks_for_a_confirmation(client, env_file, runs):
    runs.checks = NOT_RUN
    run_test(client, "llm", FORM)
    assert "La última prueba de estos valores falló." in client.post(save_url("llm"), data=FORM).text


def test_saving_after_a_passed_test_of_the_same_values_needs_no_confirmation(client, env_file, runs, app):
    run_test(client, "llm", FORM)
    html = client.post(save_url("llm"), data=FORM).text
    assert "Guardado en .env" in html and 'class="confirm' not in html
    assert app.state.web.settings.llm_model == "read-model"


def test_a_field_changed_after_the_test_asks_for_a_confirmation(client, env_file, runs):
    run_test(client, "llm", FORM)
    html = client.post(save_url("llm"), data={**FORM, "llm_model": "other-model"}).text
    assert "Estos valores no se probaron." in html
    html = client.post(save_url("llm"), data={**FORM, "llm_api_key": "another-key"}).text
    assert "Estos valores no se probaron." in html


def test_saving_one_group_keeps_the_keys_of_the_other_groups(client, env_file, runs):
    client.post(save_url("wiki_llm"), data={**FORM, "wiki_llm_model": "big-model", "confirm": "1"})
    text = env_file.read_text(encoding="utf-8")
    assert "WIKI_LLM_MODEL=big-model\n" in text and "WIKI_LLM_API_KEY=saved-ingest-key\n" in text
    assert "LLM_BASE_URL=http://saved.invalid/v1\n" in text and "LLM_MODEL=saved-model\n" in text
    assert "VISION_" not in text and "OCR_ENGINE" not in text


def test_the_vision_group_saves_the_ocr_engine(client, env_file, runs):
    client.post(save_url("vision"), data={**FORM, "vision_model": "eye-model", "ocr_engine": "vision", "confirm": "1"})
    text = env_file.read_text(encoding="utf-8")
    assert "VISION_MODEL=eye-model\n" in text and "OCR_ENGINE=vision\n" in text
    assert "LLM_MODEL=saved-model\n" in text and not re.search(r"^WIKI_LLM_MODEL=", text, re.M)


def test_saving_applies_every_saved_group(client, env_file, runs, app):
    client.post(save_url("wiki_llm"), data={**FORM, "wiki_llm_model": "big-model", "confirm": "1"})
    s = app.state.web.settings
    # The reading model is the saved one: the form values of the other groups are not saved.
    assert (s.llm_base_url, s.llm_api_key, s.llm_model) == ("http://saved.invalid/v1", "saved-key", "saved-model")
    assert (s.ingest_api_key, s.ingest_model) == ("saved-ingest-key", "big-model")
