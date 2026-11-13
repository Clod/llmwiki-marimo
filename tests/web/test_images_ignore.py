"""`[images] ignore` in the web interface: the Settings checkbox, the formats of the ingestion
screen, the refusal of an uploaded image file and the Images panel of the Maintain screen."""

from __future__ import annotations

import dataclasses
import re
import tomllib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from domain.ingestion.describe import Vision
from tests.helpers.fake_llm import FakeLLMClient
from tests.helpers.fake_vision import FakeVisionClient
from tests.helpers.image_ingest import CHART, COVER, config, fake_extract, ingest
from tests.web.conftest import SPANISH, WIKI_ID, fake_agents
from tests.web.test_ingest import lines_of
from web.app import create_app
from web.state import AppState

SETTINGS = f"/w/{WIKI_ID}/settings"
INGEST = f"/w/{WIKI_ID}/ingest"
MAINTAIN = f"/w/{WIKI_ID}/maintain"


def keep_images(wiki) -> None:
    (wiki.path / "wiki_config.toml").write_text("[images]\nignore = false\n", encoding="utf-8")


def config_of(wiki) -> dict:
    return tomllib.loads((wiki.path / "wiki_config.toml").read_text(encoding="utf-8"))


# ── Settings ─────────────────────────────────────────────────────────────────

def test_the_settings_screen_shows_the_checkbox_ticked_by_default(en_client):
    html = en_client.get(SETTINGS).text
    assert re.search(r'<input type="checkbox" id="f-ignore" name="ignore" checked>', html)
    assert "Ignore the images" in html


def test_saving_without_the_checkbox_writes_ignore_false(en_client, wiki):
    html = en_client.post(SETTINGS, data={"condense_question": "on"}).text
    assert config_of(wiki)["images"]["ignore"] is False
    assert re.search(r'<input type="checkbox" id="f-ignore" name="ignore">', html)


def test_saving_with_the_checkbox_ticked_writes_no_key(en_client, wiki):
    en_client.post(SETTINGS, data={"condense_question": "on", "ignore": "on"})
    path = wiki.path / "wiki_config.toml"
    assert not path.exists() or "ignore" not in config_of(wiki).get("images", {})


# ── the ingestion screen ─────────────────────────────────────────────────────

def drop_label(html: str) -> str:
    """The upload field of the ingestion screen: its label and its `accept` attribute."""
    return re.search(r'<label class="drop".*?</label>', html, re.DOTALL).group(0)


def test_the_ingest_screen_does_not_name_the_image_formats_by_default(client):
    label = drop_label(client.get(INGEST).text)
    assert "DOC, DOCX, MD, ODT, PDF, RTF o TXT" in label
    assert 'accept=".doc,.docx,.md,.odt,.pdf,.rtf,.txt"' in label
    assert "PNG" not in label and ".png" not in label


def test_the_ingest_screen_names_the_image_formats_with_ignore_false(client, wiki):
    keep_images(wiki)
    label = drop_label(client.get(INGEST).text)
    assert "PNG" in label and ".png" in label


def test_an_uploaded_image_file_is_refused_by_default(client, wiki):
    response = client.post(INGEST, files=[("files", ("chart.png", b"\x89PNG", "image/png"))])
    lines = lines_of(client, response)
    assert "⚠️ chart.png: formato no admitido; se admiten DOC, DOCX, MD, ODT, PDF, RTF o TXT" in lines
    assert not (wiki.sources_dir / "chart.png").exists()


# ── the Maintain screen ──────────────────────────────────────────────────────

def test_the_maintain_screen_hides_the_images_panel_by_default(client):
    assert 'id="images-panel"' not in client.get(MAINTAIN).text


def test_the_maintain_screen_shows_the_images_panel_with_ignore_false(client, wiki):
    keep_images(wiki)
    assert 'id="images-panel"' in client.get(MAINTAIN).text


# ── "Describe the images" (design 2.5) ───────────────────────────────────────

@pytest.fixture
def vision_client():
    return FakeVisionClient()


@pytest.fixture
def vision_app(settings, vision_client, monkeypatch):
    """The application with a vision model, so that only `ignore` can turn the action off."""
    monkeypatch.setattr(AppState, "vision", lambda self: Vision(vision_client, "gpt-test"))
    return create_app(dataclasses.replace(settings, vision_base_url="http://vision.invalid/v1",
                                          vision_api_key="k", vision_model="gpt-test"),
                      agents_factory=lambda wiki: fake_agents("x"))


@pytest.fixture
def kept(vision_app, monkeypatch):
    """A source with two images kept while `ignore = false`; then the wiki ignores the
    images of the next ingestions, with descriptions on."""
    wiki = vision_app.state.web.get_wiki(WIKI_ID).wiki
    ws = SimpleNamespace(workspace=wiki.path, db_path=wiki.db_path, llm=FakeLLMClient())
    config(ws, describe=False, language=wiki.language)
    fake_extract(monkeypatch, [CHART, COVER])
    ingest(ws, None, language=wiki.language)
    config(ws, describe=True, language=wiki.language)
    path = wiki.path / "wiki_config.toml"
    path.write_text(path.read_text(encoding="utf-8").replace("ignore = false", "ignore = true"), encoding="utf-8")
    return wiki


def describe_button(html: str) -> str:
    start = html.index('id="images-panel"')
    return html[html.index("<button", start):html.index("</button>", start)]


def test_the_images_kept_by_an_earlier_ingestion_can_still_be_described(vision_app, kept):
    """`ignore` acts when a source is ingested. A source ingested before keeps its images,
    and the action that describes them depends on `describe` alone."""
    client = TestClient(vision_app, follow_redirects=False, cookies=SPANISH)
    html = client.get(MAINTAIN).text
    assert 'id="images-panel"' in html
    assert "disabled" not in describe_button(html)
