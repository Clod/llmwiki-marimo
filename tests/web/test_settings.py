"""The Configuración screen: what it shows, what it saves and what saving changes in the chat."""

from __future__ import annotations

import re
import tomllib

import pytest

from domain import prompts
from domain.chat.config import load_config
from tests.web.conftest import WIKI_ID, fake_agents
from web.app import create_app

URL = f"/w/{WIKI_ID}/settings"
CONFIG = ('# Comment that must survive.\n[wiki]\nlanguage = "es"\n\n[assistant]\nsystem_prompt = "Old prompt."\n\n'
          '[pre_retrieval]\nenabled = true\nmin_coverage = 0.3\n\n[fuera_de_alcance]\nterminos = ["cripto"]\n')
FORM = {"system_prompt": "Old prompt.", "suggested_prompts": "Q1\nQ2", "enabled": "on", "min_coverage": "0.3",
        "condense_question": "on", "threshold": "0.5", "candidates": "5"}


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
    for name in ("system_prompt", "suggested_prompts", "enabled", "min_coverage", "condense_question", "threshold", "candidates"):
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
