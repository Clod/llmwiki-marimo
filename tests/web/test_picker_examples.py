"""The example wikis of the picker: the section, the copy on open and the protection of
the examples folder."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from services.wiki import open_wiki
from tests.web.conftest import SPANISH
from web.app import create_app


@pytest.fixture
def examples(tmp_path):
    """An examples folder with two wikis, one English and one Spanish."""
    root = tmp_path / "examples"
    for name, language in (("fairy-tales", "en"), ("cuentos", "es")):
        wiki = open_wiki(root / name, language=language)
        (wiki.wiki_dir / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    return root


@pytest.fixture
def client(settings, agents, examples):
    app = create_app(replace(settings, examples_dir=examples), agents_factory=lambda wiki: agents)
    return TestClient(app, follow_redirects=False, cookies=SPANISH)


def section(html: str) -> str:
    return html[html.index('id="examples-title"'):html.index('class="open-other"')]


def test_the_picker_lists_the_examples_with_open_a_copy(client):
    html = section(client.get("/").text)
    assert "Wikis de ejemplo" in html
    assert "fairy-tales" in html and "cuentos" in html
    assert html.count("Abrir una copia") == 2


def test_open_a_copy_copies_the_example_and_opens_it(client, settings):
    response = client.post("/examples/open", data={"name": "fairy-tales"})
    assert response.status_code == 303 and response.headers["location"] == "/w/fairy-tales/?opened=1"
    assert (settings.wiki_home / "fairy-tales" / "wiki" / "index.md").is_file()
    assert 'href="/w/fairy-tales/?opened=1"' in client.get("/").text   # now in the list of wikis


def test_a_copied_example_offers_open_and_is_not_copied_again(client, settings):
    client.post("/examples/open", data={"name": "fairy-tales"})
    index = settings.wiki_home / "fairy-tales" / "wiki" / "index.md"
    index.write_text("# Edited\n", encoding="utf-8")
    html = section(client.get("/").text)
    assert html.count("Abrir una copia") == 1 and ">Abrir</button>" in html
    client.post("/examples/open", data={"name": "fairy-tales"})
    assert index.read_text(encoding="utf-8") == "# Edited\n"
    assert not (settings.wiki_home / "fairy-tales-2").exists()


def test_the_examples_folder_is_not_written(client, examples):
    before = sorted((p.relative_to(examples), p.stat().st_mtime_ns) for p in examples.rglob("*") if p.is_file())
    client.post("/examples/open", data={"name": "cuentos"})
    client.get("/w/cuentos/")
    after = sorted((p.relative_to(examples), p.stat().st_mtime_ns) for p in examples.rglob("*") if p.is_file())
    assert before == after


@pytest.mark.parametrize("name", ["unknown", "../examples/cuentos", "", "/etc"])
def test_an_unknown_name_returns_to_the_picker_and_copies_nothing(client, settings, name):
    before = sorted(p.name for p in settings.wiki_home.iterdir())
    response = client.post("/examples/open", data={"name": name})
    assert response.status_code in (303, 422)
    if response.status_code == 303:
        assert response.headers["location"] == "/"
    assert sorted(p.name for p in settings.wiki_home.iterdir()) == before


def test_without_an_examples_folder_the_section_is_absent(settings, agents):
    app = create_app(settings, agents_factory=lambda wiki: agents)
    html = TestClient(app, cookies=SPANISH).get("/").text
    assert 'id="examples-title"' not in html
