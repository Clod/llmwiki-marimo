"""The interface language: the catalogs, the choice of language, every screen in English, and the two
languages that must not be confused (the interface's and the wiki's)."""

from __future__ import annotations

import re

import pytest

from services import wiki as wiki_service
from tests.web.conftest import WIKI_ID
from tests.web.test_chat import answered
from web import i18n, i18n_extract

ENGLISH = {i18n.COOKIE: "en"}
TARGETS = [lang for lang in i18n.LANGUAGES if lang != i18n.SOURCE_LANGUAGE]


# ── The catalogs ─────────────────────────────────────────────────────────────

def _key(message) -> tuple[str, str | None]:
    return (message.id[0] if isinstance(message.id, tuple) else message.id), message.context


def _used() -> dict:
    return {_key(m): m for m in i18n_extract.extract() if m.id}


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"%\((\w+)\)s", text))


def test_the_extraction_finds_the_messages_of_the_python_modules_and_of_the_templates():
    used = {key[0] for key in _used()}
    assert "Another operation is running on this wiki." in used           # an N_() constant, web/deps.py
    assert "Could not save: %(error)s" in used                            # a _() in a route
    assert "Choose a wiki" in used                                         # a _() in a template
    assert any("Going back deletes the added ones" in m for m in used)     # a {% trans %} block with a plural


@pytest.mark.parametrize("lang", TARGETS)
def test_every_message_has_a_translation_in_every_supported_language(lang):
    catalog = i18n_extract.read(lang)
    translated = {_key(m): m for m in catalog if m.id}
    missing = sorted(key for key in _used() if key not in translated)
    assert not missing, f"{lang}: messages without an entry: {missing}"
    empty = []
    for key, message in translated.items():
        strings = message.string if isinstance(message.string, (tuple, list)) else [message.string]
        if not all((s or "").strip() for s in strings):
            empty.append(key)
    assert not empty, f"{lang}: messages with an empty translation: {sorted(empty)}"


@pytest.mark.parametrize("lang", TARGETS)
def test_no_catalog_holds_a_message_the_code_no_longer_uses(lang):
    used = _used()
    stale = sorted(_key(m) for m in i18n_extract.read(lang) if m.id and _key(m) not in used)
    assert not stale, f"{lang}: run `uv run --group web python -m web.i18n_extract` and delete: {stale}"


@pytest.mark.parametrize("lang", TARGETS)
def test_a_translation_keeps_the_placeholders_of_its_message(lang):
    for message in i18n_extract.read(lang):
        if not message.id:
            continue
        ids = message.id if isinstance(message.id, tuple) else (message.id,)
        strings = message.string if isinstance(message.string, (tuple, list)) else (message.string,)
        wanted = set().union(*(_placeholders(i) for i in ids))
        for string in strings:
            assert _placeholders(string) <= wanted, (lang, message.id)
        assert _placeholders(strings[0]) == _placeholders(ids[0]), (lang, message.id)


def test_every_supported_language_is_listed_in_one_place_and_has_its_catalog():
    assert i18n.DEFAULT_LANGUAGE in i18n.LANGUAGES and i18n.SOURCE_LANGUAGE in i18n.LANGUAGES
    for lang in TARGETS:
        assert i18n_extract.catalog_path(lang).is_file()


# ── Choosing the language ────────────────────────────────────────────────────

def test_the_default_is_english(app):
    from fastapi.testclient import TestClient

    html = TestClient(app).get("/").text
    assert '<html lang="en">' in html and "Choose a wiki" in html


@pytest.mark.parametrize("header,expected", [
    ("es", "es"), ("es-AR,es;q=0.9,en;q=0.5", "es"), ("en-US,en;q=0.9,es;q=0.8", "en"),
    ("fr;q=0.9,es;q=0.4", "es"), ("fr,de", None), ("", None), ("*", None), ("es;q=0,en;q=0.1", "en"),
    ("es;q=abc", None),
])
def test_accept_language_picks_the_supported_language_the_browser_prefers(header, expected):
    assert i18n.parse_accept_language(header) == expected


def test_accept_language_is_honoured_without_a_cookie(app):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    spanish = client.get("/", headers={"Accept-Language": "es-AR,es;q=0.9,en;q=0.5"}).text
    assert '<html lang="es">' in spanish and "Elegí una wiki" in spanish
    english = client.get("/", headers={"Accept-Language": "en-GB,en;q=0.9"}).text
    assert '<html lang="en">' in english and "Choose a wiki" in english
    unknown = client.get("/", headers={"Accept-Language": "fr-FR,fr;q=0.9"}).text
    assert '<html lang="en">' in unknown


def test_the_cookie_wins_over_the_browser(app):
    from fastapi.testclient import TestClient

    client = TestClient(app, cookies=ENGLISH)
    html = client.get("/", headers={"Accept-Language": "es"}).text
    assert '<html lang="en">' in html
    assert '<html lang="en">' in TestClient(app, cookies={i18n.COOKIE: "xx"}).get("/").text   # unknown: ignored


def test_the_switch_sets_the_cookie_and_the_next_page_changes_language(app):
    from fastapi.testclient import TestClient

    client = TestClient(app)                                     # no cookie, no header: English
    assert "Choose a wiki" in client.get("/").text
    response = client.post("/lang", data={"lang": "es", "next": f"/w/{WIKI_ID}/ingest"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == f"/w/{WIKI_ID}/ingest"
    assert f"{i18n.COOKIE}=es" in response.headers["set-cookie"]
    page = client.get(f"/w/{WIKI_ID}/ingest").text               # the cookie jar kept it
    assert '<html lang="es">' in page and "Agregar documentos" in page
    client.post("/lang", data={"lang": "en", "next": "/"})
    assert '<html lang="en">' in client.get("/").text


def test_the_switch_ignores_an_unknown_language_and_a_foreign_return_address(client):
    response = client.post("/lang", data={"lang": "xx", "next": "/"})
    assert "set-cookie" not in response.headers
    for target in ("https://example.com/", "//example.com/", "/\\example.com", "relative"):
        assert client.post("/lang", data={"lang": "en", "next": target}).headers["location"] == "/"


def test_every_screen_has_the_switch_and_marks_the_current_language(client, en_client):
    for c, current, other in ((client, "es", "en"), (en_client, "en", "es")):
        for url in ("/", f"/w/{WIKI_ID}/pages/overview", f"/w/{WIKI_ID}/ingest", f"/w/{WIKI_ID}/history"):
            html = c.get(url).text
            assert f'<input type="hidden" name="next" value="{url}">' in html
            assert f'name="lang" value="{current}" lang="{current}" title="{i18n.LANGUAGES[current]}" aria-pressed="true"' in html
            assert f'name="lang" value="{other}"' in html and html.count('aria-pressed="true"') >= 1


def test_the_switch_returns_to_the_same_url_with_its_query_but_not_the_one_time_notice(en_client):
    html = en_client.get(f"/w/{WIKI_ID}/relations?page=concepts/prince&depth=2&notice=Hi").text
    assert 'name="next" value="/w/tales/relations?page=concepts%2Fprince&amp;depth=2"' in html


# ── Every screen in English ──────────────────────────────────────────────────

@pytest.fixture
def commits(client, wiki):
    assert client.post(f"/w/{WIKI_ID}/delete/concepts/prince", data={"title": "Prince"}).status_code == 303
    wiki_service.save_page(wiki, "concepts/cinderella", "A new body about the slipper.", None)
    return wiki_service.history(wiki)


@pytest.fixture
def draft_stub(monkeypatch):
    import domain.chat.wiki_tools as wiki_tools

    calls = []

    def draft_wiki_page(db_path, workspace, title, content, category, **kwargs):
        calls.append({"content": content, "language": kwargs.get("language")})
        return wiki_tools.PageDraft(title, category, "slipper-story", "wiki/concepts/slipper-story.md",
                                    f"# {title}\n\nDRAFT: {content}", False)

    monkeypatch.setattr(wiki_tools, "draft_wiki_page", draft_wiki_page)
    return calls


# One Spanish label of each screen. None may appear when the interface is English.
SCREENS = [
    ("picker", "/", ["Elegí una wiki", "Idioma", "Fuentes", "Páginas"]),
    ("reading", f"/w/{WIKI_ID}/pages/concepts/cinderella",
     ["Conversación", "Índice", "Preguntá", "Editar", "Relaciones", "Borrar", "Guardar", "Modo", "Enviar"]),
    ("editor", f"/w/{WIKI_ID}/edit/concepts/cinderella", ["Editando", "Vista previa", "Cancelar", "Guardar"]),
    ("ingest", f"/w/{WIKI_ID}/ingest", ["Agregar documentos", "Escanear", "Progreso", "Fuentes"]),
    ("maintain", f"/w/{WIKI_ID}/maintain",
     ["Mantenimiento", "Reconstruir el índice", "Páginas de resumen", "Páginas obsoletas", "Modelo"]),
    ("relations", f"/w/{WIKI_ID}/relations", ["Mostrar resúmenes", "Encuadrar", "Leyenda", "Acercar"]),
    ("local graph", f"/w/{WIKI_ID}/relations?page=concepts/cinderella", ["Profundidad", "Ver todo el grafo", "Leer la página"]),
    ("history", f"/w/{WIKI_ID}/history", ["Puntos de la wiki", "Volver a este punto", "copia del índice", "Progreso"]),
    ("page history", f"/w/{WIKI_ID}/history/page/concepts/cinderella", ["Versiones", "Ver esta versión", "Historial de"]),
]
TABS_ES = [">Leer<", ">Ingestar<", ">Mantener<", ">Ver relaciones<", ">Historial<"]
TABS_EN = [">Read<", ">Ingest<", ">Maintain<", ">Relations<", ">History<"]


@pytest.mark.parametrize("name,url,spanish", SCREENS, ids=[s[0] for s in SCREENS])
def test_a_screen_in_english_holds_no_spanish_label(en_client, commits, name, url, spanish):
    response = en_client.get(url)
    assert response.status_code == 200
    html = response.text
    assert '<html lang="en">' in html
    for label in spanish:
        assert label not in html, f"{name}: {label!r} appears in the English interface"
    if name != "picker":
        assert all(tab in html for tab in TABS_EN) and not any(tab in html for tab in TABS_ES)


def test_the_same_screens_in_spanish_keep_their_labels(client, commits):
    for name, url, spanish in SCREENS:
        html = client.get(url).text
        assert '<html lang="es">' in html
        assert any(label in html for label in spanish), name


def test_the_history_version_the_diff_and_the_confirmation_in_english(en_client, commits):
    sha = commits[-1].sha
    version = en_client.get(f"/w/{WIKI_ID}/history/page/concepts/cinderella/at/{sha}").text
    assert '<html lang="en">' in version and "Read-only: to go back to this point" in version
    assert "Compare with the current version" in version and "Solo lectura" not in version
    diff = en_client.get(f"/w/{WIKI_ID}/history/page/concepts/cinderella/diff/{sha}").text
    assert "Lines with" in diff and "Las líneas" not in diff
    confirm = en_client.get(f"/w/{WIKI_ID}/history/commit/{commits[1].sha}/confirm").text
    assert "Take the wiki back to the point" in confirm and "Volver la wiki" not in confirm
    assert re.search(r"added \d+</b>, <b>changed \d+</b> and <b>removed \d+</b>\s+pages?\.", confirm)


def test_the_dialog_the_notices_and_the_console_lines_in_english(en_client, agents, wiki, draft_stub):
    answered(en_client, agents)
    dialog = en_client.post(f"/w/{WIKI_ID}/chat/draft", data={
        "conversation_id": "c1", "title": "Slipper story", "open_page": ""}).text
    assert "Review the page" in dialog and "Save to wiki" in dialog and "Revisar la página" not in dialog
    assert 'data-label-edit="Edit" data-label-preview="Preview"' in dialog       # what the script writes
    assert "Escribí" not in en_client.post(f"/w/{WIKI_ID}/chat/draft", data={
        "conversation_id": "c1", "title": " ", "open_page": ""}).text
    saved = en_client.post(f"/w/{WIKI_ID}/chat/save", data={
        "title": "Slipper story", "markdown": "# Slipper story\n\nText.", "open_page": ""}).text
    assert "Created the page “Slipper story”." in saved and ">Open the page</a>" in saved
    notice = en_client.post(f"/w/{WIKI_ID}/stale/delete").headers["location"]
    assert "Deleted" in notice and "Se borr" not in notice
    unconfigured = en_client.post(f"/w/{WIKI_ID}/delete/concepts/missing", data={"title": "x"})
    assert unconfigured.status_code == 404 and unconfigured.json()["detail"] == "The page does not exist."


def test_the_texts_the_scripts_write_come_from_attributes_not_from_the_scripts():
    from pathlib import Path

    for script in Path("web/static/js").glob("*.js"):
        if script.name.endswith(".min.js") or script.name.startswith("htmx"):
            continue
        source = script.read_text(encoding="utf-8")
        assert not re.search(r"[áéíóúñ¿¡]|\b(Editar|Vista previa|nodos|relaciones)\b", source), script.name


def test_the_source_status_is_translated_with_a_lookup(en_client, client):
    from web.deps import status_label

    with i18n.use_language("en"):
        assert [status_label(s) for s in ("pending", "processing", "ready", "failed")] == [
            "pending", "processing", "ready", "failed"]
        assert status_label("odd") == "odd"
    with i18n.use_language("es"):
        assert status_label("ready") == "lista" and status_label("failed") == "fallida"


def test_the_english_interface_writes_english_closing_lines(en_client, wiki, monkeypatch):
    from services import ingest

    monkeypatch.setattr(ingest, "regenerate_summaries", lambda *a, **k: [])
    monkeypatch.setattr(wiki_service, "reindex", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    response = en_client.post(f"/w/{WIKI_ID}/reindex")
    op = re.search(r'/ops/(\w+)/events', response.text).group(1)
    body = en_client.get(f"/w/{WIKI_ID}/ops/{op}/events").text
    assert "boom" in body and "❌" in body
    from web.routes.ingest import summary_line

    with i18n.use_language("en"):
        class R:
            status = "ingested"
        assert summary_line(i18n.gettext("Scan"), [R(), R()]) == "🏁 Scan: 2 ingested, 0 skipped, 0 failed"


# ── Two languages that must not be confused ──────────────────────────────────

def _saved_conversation(client, agents, draft_stub, wiki_language: str) -> str:
    agents.config.language = wiki_language
    answered(client, agents)
    client.post(f"/w/{WIKI_ID}/chat/draft", data={"conversation_id": "c1", "title": "Story", "open_page": ""})
    return draft_stub[-1]["content"]


def test_a_spanish_wiki_with_the_english_interface_saves_the_conversation_in_spanish(
        en_client, agents, wiki, draft_stub):
    content = _saved_conversation(en_client, agents, draft_stub, "es")
    assert "**Pregunta:**" in content and "**Respuesta:**" in content
    assert "**Question:**" not in content and draft_stub[-1]["language"] == "es"
    assert "Review the page" in en_client.post(f"/w/{WIKI_ID}/chat/draft", data={
        "conversation_id": "c1", "title": "Story", "open_page": ""}).text          # the dialog is English


def test_an_english_wiki_with_the_spanish_interface_saves_the_conversation_in_english(
        client, agents, wiki, draft_stub):
    content = _saved_conversation(client, agents, draft_stub, "en")
    assert "**Question:**" in content and "**Answer:**" in content
    assert "**Pregunta:**" not in content and draft_stub[-1]["language"] == "en"
    assert "Revisar la página" in client.post(f"/w/{WIKI_ID}/chat/draft", data={
        "conversation_id": "c1", "title": "Story", "open_page": ""}).text          # the dialog is Spanish


def test_the_interface_language_never_reaches_the_wiki(en_client, client, wiki):
    """The commit a deletion records is written by the wiki's code: it names the page, in no interface language."""
    for c, page, title in ((en_client, "concepts/prince", "Prince"), (client, "summaries/tale", "Tale")):
        assert c.post(f"/w/{WIKI_ID}/delete/{page}", data={"title": title}).status_code == 303
    assert [c.message for c in wiki_service.history(wiki)[:2]] == ["delete: summaries/tale", "delete: concepts/prince"]


def test_a_response_varies_with_the_cookie_and_the_browser_language(client):
    assert client.get("/").headers["vary"] == "Cookie, Accept-Language"


def test_the_stylesheets_hold_no_text():
    """A `content:` that carries letters is a human text in a stylesheet: it would stay in one language."""
    from pathlib import Path

    for sheet in Path("web/static/css").glob("*.css"):
        for text in re.findall(r'content:\s*"([^"]*)"', sheet.read_text(encoding="utf-8")):
            assert not re.search(r"[A-Za-zÀ-ÿ]", text), f"{sheet.name}: {text!r}"


def test_the_console_placeholder_is_a_translated_attribute(client, en_client):
    assert 'data-empty="The progress of the operation appears here."' in en_client.get(f"/w/{WIKI_ID}/ingest").text
    assert 'data-empty="El progreso de la operación aparece acá."' in client.get(f"/w/{WIKI_ID}/history").text
