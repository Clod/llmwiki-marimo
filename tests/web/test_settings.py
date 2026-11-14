"""The Configuración screen: what it shows, what it saves and what saving changes in the chat."""

from __future__ import annotations

import dataclasses
import re
import tomllib

import pytest

import config
from domain import prompts
from domain.ingestion import ocr
from domain.chat.config import load_config
from tests.web.conftest import WIKI_ID, fake_agents
from web.app import create_app

URL = f"/w/{WIKI_ID}/settings"
CONFIG = ('# Comment that must survive.\n[wiki]\nlanguage = "es"\n\n[assistant]\nsystem_prompt = "Old prompt."\n\n'
          '[pre_retrieval]\nenabled = true\nmin_coverage = 0.3\n\n[fuera_de_alcance]\nterminos = ["cripto"]\n')
FORM = {"system_prompt": "Old prompt.", "suggested_prompts": "Q1\nQ2", "enabled": "on", "min_coverage": "0.3",
        "condense_question": "on", "threshold": "0.5", "candidates": "5",
        "number_tolerance": "0.05", "ignore": "on"}


@pytest.fixture
def config_wiki(wiki):
    (wiki.path / "wiki_config.toml").write_text(CONFIG, encoding="utf-8")
    return wiki


def file_text(wiki) -> str:
    return (wiki.path / "wiki_config.toml").read_text(encoding="utf-8")


def test_the_menu_link_after_history(client, en_client):
    html = client.get(URL).text
    assert f'<a href="{URL}" aria-current="page"' in html and ">Configuración</a>" in html
    assert html.index(f"/w/{WIKI_ID}/history") < html.index(URL)
    assert ">Settings</a>" in en_client.get(URL).text
    assert f'href="{URL}" ' in client.get(f"/w/{WIKI_ID}/history").text


def test_every_key_is_shown_with_its_value_and_the_language_is_read_only(client, config_wiki):
    html = client.get(URL).text
    for name in ("system_prompt", "suggested_prompts", "enabled", "min_coverage", "condense_question", "threshold", "candidates",
                 "number_tolerance"):
        assert f'name="{name}"' in html
    assert "Old prompt." in html and 'value="0.3"' in html
    assert re.search(r'name="enabled"[^>]*checked', html)
    assert 'name="language"' not in html and "<code>es</code>" in html
    assert "reindexar" in html.lower()
    assert f'href="/w/{WIKI_ID}/vocabulary"' in html
    assert "Valor por defecto" in html


def test_a_wiki_without_a_file_shows_the_defaults(client):
    html = client.get(URL).text
    assert prompts.load_prompt("system_prompt", "en").splitlines()[0][:40] in html
    assert 'name="candidates"' in html and 'value="5"' in html


def test_a_spanish_wiki_without_a_prompt_shows_and_restores_the_spanish_default(client, wiki):
    (wiki.path / "wiki_config.toml").write_text('[wiki]\nlanguage = "es"\n', encoding="utf-8")
    html = client.get(URL).text
    default = prompts.load_prompt("system_prompt", "es").strip()
    first = default.splitlines()[0]
    assert first[:40] != prompts.load_prompt("system_prompt", "en").splitlines()[0][:40]
    assert re.search(r'data-default="%s' % re.escape(first[:40]), html)       # the restore button's value
    assert re.search(r'>%s' % re.escape(first[:40]), html)                      # the text shown now


def test_saving_changes_the_file_keeping_comments_and_other_sections(client, config_wiki):
    response = client.post(URL, data={**FORM, "system_prompt": "New prompt.", "min_coverage": "0.4"})
    assert response.status_code == 200
    assert "Guardado. Los cambios se aplican desde la próxima pregunta; las respuestas anteriores no cambian." in response.text
    parsed = tomllib.loads(file_text(config_wiki))
    assert parsed["assistant"]["system_prompt"] == "New prompt." and parsed["pre_retrieval"]["min_coverage"] == 0.4
    assert parsed["assistant"]["suggested_prompts"] == ["Q1", "Q2"]
    assert parsed["wiki"] == {"language": "es"} and parsed["fuera_de_alcance"] == {"terminos": ["cripto"]}
    assert "# Comment that must survive." in file_text(config_wiki)
    assert "verify" not in parsed


def test_an_unticked_checkbox_is_saved_as_false(client, config_wiki):
    form = {k: v for k, v in FORM.items() if k not in ("enabled", "condense_question")}
    client.post(URL, data=form)
    config = load_config(config_wiki.path)
    assert config.pre_retrieval is False and config.condense_question is False


def test_an_invalid_value_is_refused_and_the_file_is_unchanged(client, config_wiki):
    before = file_text(config_wiki)
    html = client.post(URL, data={**FORM, "system_prompt": " ", "threshold": "7", "candidates": "0"}).text
    assert file_text(config_wiki) == before
    assert "Guardado." not in html
    assert html.count('class="field-error"') == 3
    assert 'value="7"' in html and 'value="0"' in html


def test_the_next_chat_turn_uses_the_saved_prompt(settings, home):
    built = []

    def factory(wiki):
        config = load_config(wiki.path)
        built.append(config.system_prompt)
        return fake_agents("Cinderella lost a glass slipper.")

    from fastapi.testclient import TestClient
    from web.i18n import COOKIE
    app = create_app(settings, agents_factory=factory)
    client = TestClient(app, follow_redirects=False, cookies={COOKIE: "es"})
    entry = app.state.web.get_wiki(WIKI_ID)
    (entry.wiki.path / "wiki_config.toml").write_text(CONFIG, encoding="utf-8")
    app.state.web.get_agents(entry)
    assert built == ["Old prompt."] and entry.agents is not None
    assert client.post(URL, data={**FORM, "system_prompt": "Another prompt."}).status_code == 200
    assert entry.agents is None
    app.state.web.get_agents(entry)
    assert built == ["Old prompt.", "Another prompt."]


def test_a_refused_save_keeps_the_agents(client, app, config_wiki):
    entry = app.state.web.get_wiki(WIKI_ID)
    app.state.web.get_agents(entry)
    client.post(URL, data={**FORM, "system_prompt": ""})
    assert entry.agents is not None


def test_a_vocabulary_change_drops_the_agents(client, app, config_wiki):
    entry = app.state.web.get_wiki(WIKI_ID)
    app.state.web.get_agents(entry)
    assert entry.agents is not None
    client.post(f"/w/{WIKI_ID}/vocabulary/blacklist-add", data={"term": "bitcoin"})
    assert entry.agents is None


def test_a_refused_vocabulary_change_keeps_the_agents(client, app, config_wiki):
    entry = app.state.web.get_wiki(WIKI_ID)
    app.state.web.get_agents(entry)
    client.post(f"/w/{WIKI_ID}/vocabulary/blacklist-add", data={"term": "cinderella"})
    assert entry.agents is not None


def test_reset_agents_drops_only_that_wiki(app):
    state = app.state.web
    entry = state.get_wiki(WIKI_ID)
    state.get_agents(entry)
    state.reset_agents(WIKI_ID)
    assert entry.agents is None
    state.reset_agents("unopened")


def test_saving_is_one_commit_of_the_wiki(client, config_wiki):
    import subprocess

    client.post(URL, data={**FORM, "min_coverage": "0.4"})
    subject = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=config_wiki.path, capture_output=True, text=True).stdout
    assert subject.strip() == "settings: assistant.suggested_prompts, pre_retrieval.min_coverage"
    status = subprocess.run(["git", "status", "--porcelain"], cwd=config_wiki.path, capture_output=True, text=True).stdout
    assert "wiki_config.toml" not in status


# ── The models: the reading model and the ingestion model, in `.env` ──────────

ENV = ("# Comment that must survive.\nOTHER=1\nLLM_BASE_URL=http://old.invalid/v1\n"
       "LLM_API_KEY=secret-old\nLLM_MODEL=old-model\n")
MODELS_FORM = {"llm_base_url": "https://new.invalid/v1", "llm_api_key": "", "llm_model": "new-model",
               "wiki_llm_base_url": "", "wiki_llm_api_key": "", "wiki_llm_model": "big-model"}


def save(client, group: str, form: dict):
    """Save one group of the models block, confirmed: the tests of `test_model_test.py`
    cover the confirmation itself."""
    return client.post(f"/w/{WIKI_ID}/settings/models/{group}", data={**form, "confirm": "1"})


@pytest.fixture
def env_file(app, tmp_path):
    """The application writes the models to a `.env` of the test, not to the project's."""
    path = tmp_path / ".env"
    path.write_text(ENV, encoding="utf-8")
    state = app.state.web
    state.settings = dataclasses.replace(state.settings, env_file=path)
    return path


def test_without_an_env_file_the_models_form_is_not_shown(client):
    assert "/settings/models/" not in client.get(URL).text


def test_the_models_are_shown_and_the_key_is_not(client, env_file):
    html = client.get(URL).text
    assert 'hx-post="/w/tales/settings/models/llm"' in html
    assert 'value="http://old.invalid/v1"' in html and 'value="old-model"' in html
    assert re.search(r'name="llm_api_key" type="password"[^>]*value=""', html)
    assert "secret-old" not in html
    assert "Hay una clave guardada" in html and "No hay ninguna clave guardada." in html
    assert "Modelo de lectura" in html and "Modelo de ingesta" in html


def test_saving_writes_env_keeps_the_key_and_the_other_lines(client, env_file, app):
    html = save(client, "llm", MODELS_FORM).text
    assert "Guardado en .env" in html
    save(client, "wiki_llm", MODELS_FORM)
    text = env_file.read_text(encoding="utf-8")
    assert text.startswith("# Comment that must survive.\nOTHER=1\n")
    assert "LLM_BASE_URL=https://new.invalid/v1\n" in text and "LLM_MODEL=new-model\n" in text
    assert "LLM_API_KEY=secret-old\n" in text  # an empty key field keeps the saved key
    assert "WIKI_LLM_MODEL=big-model\n" in text and "WIKI_LLM_BASE_URL=\n" in text
    s = app.state.web.settings
    assert (s.llm_base_url, s.llm_api_key, s.llm_model) == ("https://new.invalid/v1", "secret-old", "new-model")
    # The blank ingestion values fall back to the reading model.
    assert (s.ingest_base_url, s.ingest_api_key, s.ingest_model) == ("https://new.invalid/v1", "secret-old", "big-model")


def test_a_new_key_replaces_the_saved_one_and_is_not_echoed(client, env_file):
    html = save(client, "llm", {**MODELS_FORM, "llm_api_key": 'sk "new" #1'}).text
    assert "sk " not in html
    assert 'LLM_API_KEY="sk \\"new\\" #1"\n' in env_file.read_text(encoding="utf-8")
    from dotenv import dotenv_values
    assert dotenv_values(env_file)["LLM_API_KEY"] == 'sk "new" #1'


def test_a_url_without_http_writes_nothing_and_says_so(client, env_file):
    html = save(client, "llm", {**MODELS_FORM, "llm_base_url": "new.invalid/v1"}).text
    assert 'class="field-error"' in html and "Escribí una URL que empiece con http:// o https://." in html
    assert env_file.read_text(encoding="utf-8") == ENV


def test_a_missing_env_file_is_created(client, app, tmp_path):
    path = tmp_path / "new" / ".env"
    path.parent.mkdir()
    app.state.web.settings = dataclasses.replace(app.state.web.settings, env_file=path)
    save(client, "llm", {**MODELS_FORM, "llm_api_key": "k1"})
    assert "LLM_API_KEY=k1\n" in path.read_text(encoding="utf-8")


def test_the_next_question_builds_the_agents_with_the_new_model(env_file, settings, tmp_path):
    from fastapi.testclient import TestClient
    from tests.web.conftest import SPANISH

    built = []
    app = create_app(dataclasses.replace(settings, env_file=env_file),
                     agents_factory=lambda wiki: built.append(app.state.web.settings.llm_model) or fake_agents("Ok."))
    client = TestClient(app, follow_redirects=False, cookies=SPANISH)
    state = app.state.web
    state.get_agents(state.get_wiki(WIKI_ID))
    save(client, "llm", MODELS_FORM)
    state.get_agents(state.get_wiki(WIKI_ID))
    assert built == ["model", "new-model"]


# ── The vision model and the OCR engine (design 3) ───────────────────────────

def test_the_vision_model_follows_the_ingestion_model(settings):
    old = dataclasses.replace(settings, vision_base_url="http://old.invalid/v1", vision_api_key="old-key",
                              vision_model="old-model")
    new = old.with_models({"LLM_BASE_URL": "http://read.invalid/v1", "LLM_API_KEY": "k-read",
                           "LLM_MODEL": "read-model", "WIKI_LLM_MODEL": "big-model"})
    assert (new.vision_base_url, new.vision_api_key, new.vision_model) == (
        "http://read.invalid/v1", "k-read", "big-model")
    own = old.with_models({"LLM_BASE_URL": "http://read.invalid/v1", "LLM_API_KEY": "k-read",
                           "LLM_MODEL": "read-model", "VISION_MODEL": "eye-model"})
    assert (own.vision_base_url, own.vision_api_key, own.vision_model) == (
        "http://read.invalid/v1", "k-read", "eye-model")


VISION_FORM = {**MODELS_FORM, "vision_base_url": "https://eye.invalid/v1", "vision_api_key": "secret-eye",
               "vision_model": "eye-model", "ocr_engine": "vision"}
# `config.settings` as the tests found it; the fixture `restore_config_settings` keeps it so.
_AT_IMPORT = config.settings.model_dump()


def test_the_vision_model_and_the_ocr_engine_are_shown_and_the_key_is_not(client, env_file):
    env_file.write_text(ENV + "VISION_MODEL=eye-model\nVISION_API_KEY=secret-eye\nOCR_ENGINE=tesseract\n",
                        encoding="utf-8")
    html = client.get(URL).text
    assert "Modelo de visión" in html and "Motor de OCR" in html
    assert 'name="vision_base_url"' in html and 'value="eye-model"' in html
    assert re.search(r'name="vision_api_key" type="password"[^>]*value=""', html)
    assert "secret-eye" not in html
    assert re.search(r'<option value="tesseract" selected>Tesseract</option>', html)


def test_an_env_without_ocr_engine_shows_none(client, env_file):
    html = client.get(URL).text
    assert re.search(r'<option value="none" selected>Ninguno</option>', html)


def test_saving_writes_the_vision_model_and_the_ocr_engine(client, env_file):
    html = save(client, "vision", VISION_FORM).text
    assert "Guardado en .env" in html and "secret-eye" not in html
    text = env_file.read_text(encoding="utf-8")
    assert "VISION_BASE_URL=https://eye.invalid/v1\n" in text and "VISION_MODEL=eye-model\n" in text
    assert "VISION_API_KEY=secret-eye\n" in text and "OCR_ENGINE=vision\n" in text
    # An empty key field keeps the saved key, as for the other two models.
    save(client, "vision", {**VISION_FORM, "vision_api_key": ""})
    assert "VISION_API_KEY=secret-eye\n" in env_file.read_text(encoding="utf-8")


def test_an_unknown_ocr_engine_is_saved_as_none(client, env_file):
    save(client, "vision", {**VISION_FORM, "ocr_engine": "magic"})
    assert "OCR_ENGINE=none\n" in env_file.read_text(encoding="utf-8")


def test_a_vision_url_without_http_writes_nothing(client, env_file):
    html = save(client, "vision", {**VISION_FORM, "vision_base_url": "eye.invalid/v1"}).text
    assert "Escribí una URL que empiece con http:// o https://." in html
    assert env_file.read_text(encoding="utf-8") == ENV


def test_the_saved_models_apply_without_a_restart(client, env_file, app):
    state = app.state.web
    save(client, "vision", {**VISION_FORM, "ocr_engine": "tesseract"})
    assert config.settings.OCR_ENGINE == "tesseract" and ocr.engine_name() == "tesseract"
    assert config.settings.VISION_MODEL == "eye-model"
    assert app.state.web is state
    vision = state.vision()
    assert vision.model == "eye-model" and str(vision.client.base_url).startswith("https://eye.invalid/v1")
    assert state.vision_model() == "eye-model"

    # With the vision fields empty, the vision model is the new ingestion model.
    save(client, "llm", MODELS_FORM)
    save(client, "wiki_llm", MODELS_FORM)
    save(client, "vision", {**MODELS_FORM, "ocr_engine": "vision"})
    assert state.vision_model() == "big-model"
    assert ocr.vision_config()[2] == "big-model"
    assert ocr.engine_status("es")["label"] == "modelo externo (big-model)"


def test_a_test_that_saves_the_models_leaves_config_settings_as_it_was():
    """Runs after the tests above that save the models: the fixture put every field back."""
    assert config.settings.model_dump() == _AT_IMPORT
