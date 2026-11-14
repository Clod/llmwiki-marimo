"""Phase 2 of the image design in the web interface (D7, D12; test 11 of section 8):
the third state on the reading screen and in the chat, the folded description of the
Figures section, the Maintain panel "Images" and the four keys of the Settings screen.
The vision model is a fake client; no model, no network."""

from __future__ import annotations

import dataclasses
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from domain.ingestion.describe import Vision
from domain.ingestion.images import images_dir
from domain.ingestion.wiki_generator import figures_section
from domain.tools.db import get_connection
from domain.tools.references import update_references
from domain.tools.wiki_fs import create_page
from domain.verify import FakeVerifier
from tests.helpers.fake_llm import FakeLLMClient
from tests.helpers.fake_vision import FakeVisionClient
from tests.helpers.image_ingest import CHART, COVER, config, fake_extract, ingest
from tests.helpers.pngs import write_png
from tests.web.conftest import SPANISH, WIKI_ID, fake_agents
from tests.web.test_ingest import lines_of
from web.app import create_app
from web.render import ImageSupport, render_markdown, render_page_verified, render_verified
from web.state import AppState

TIP = "Respaldada por la descripción generada de la imagen de la página 3"
BLOCK = (
    "<!-- image-description: imageFile1.png -->\n"
    "> **Descripción generada de la imagen de la página 3** (modelo gpt-test).\n"
    "> **Tipo:** gráfico de barras. **Descripción:** Tasa nominal anual de cinco bancos a treinta días.\n"
    "> **Valores:** Banco Nación 35,0 %; Banco Galicia 34,5 %\n"
    "<!-- /image-description -->"
)
SOURCE = ("El informe analiza los plazos fijos de los bancos argentinos durante el año.\n\n"
          "![Imagen, página 3](.llmwiki/cache/local/src1/images/imageFile1.png)\n\n" + BLOCK)
GOOD = "El informe analiza los plazos fijos de los bancos argentinos durante el año."
FROM_IMAGE = "El Banco Nación paga 35,0 % de tasa nominal anual."
BAD = "Rinde un cuarenta por ciento anual garantizado."


def words(text: str) -> set[str]:
    return {w.strip(".,;%").lower() for w in text.split() if len(w) > 3}


def overlap(passage: str, sentence: str) -> float:
    wanted = words(sentence)
    return len(wanted & words(passage)) / len(wanted) if wanted else 0.0


# ── the reading screen: the third state ──────────────────────────────────────

@pytest.fixture
def app(settings):
    return create_app(settings, agents_factory=lambda wiki: fake_agents("x"), verifier=FakeVerifier(overlap))


@pytest.fixture
def report_page(wiki):
    """A concept page that cites a source whose text holds a description block, with the image kept."""
    with get_connection(wiki.db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, user_id, filename, relative_path, path, source_kind, file_type, status, content) "
            "VALUES ('src1', 'local', 'Report.pdf', 'sources/Report.pdf', '/sources/', 'source', 'pdf', 'ready', ?)",
            (SOURCE,))
        conn.execute("INSERT INTO document_pages (document_id, page, content) VALUES ('src1', 1, ?)", (SOURCE,))
        conn.commit()
    write_png(images_dir(wiki.path, "src1") / "imageFile1.png")
    (wiki.path / ".llmwiki" / "cache" / "local" / "src1" / "figures.json").write_text(
        '[{"page": 3, "file": "imageFile1.png", "caption": ""}]', encoding="utf-8")
    body = f"# Informe\n\n{GOOD}\n\n{FROM_IMAGE}\n\n{BAD}\n\n## Fuentes\n- Report.pdf\n"
    info = create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "informe", "Informe", body, [], sources=["Report.pdf"])
    update_references(wiki.db_path, info["id"], (wiki.path / info["path"]).read_text(encoding="utf-8"), "/wiki/concepts/")
    return info


def test_a_sentence_supported_by_a_description_has_its_own_mark_tooltip_and_link(client, report_page):
    assert client.post(f"/w/{WIKI_ID}/verify/concepts/informe").status_code == 303
    html = client.get(f"/w/{WIKI_ID}/pages/concepts/informe").text
    assert f'<span class="described" title="{TIP}">{FROM_IMAGE}</span>' in html
    assert (f'<a class="image-link" href="/w/{WIKI_ID}/images/src1/imageFile1.png" target="_blank" '
            'rel="noopener" title="Abrir la imagen"') in html
    assert f'<span class="supported" title="Respaldada por Report.pdf">{GOOD}</span>' in html
    assert "<mark" in html and BAD in html
    assert "1 de 3 oraciones sin respaldo; 1 respaldada por una descripción de imagen" in html


def test_a_page_without_descriptions_keeps_its_summary_line(client, report_page, wiki):
    with get_connection(wiki.db_path) as conn:
        conn.execute("UPDATE document_pages SET content = ? WHERE document_id = 'src1'", (GOOD,))
        conn.commit()
    client.post(f"/w/{WIKI_ID}/verify/concepts/informe")
    html = client.get(f"/w/{WIKI_ID}/pages/concepts/informe").text
    assert "oraciones sin respaldo</p>" in html and "descripci" not in html.split('id="verification-state"')[1].split("</p>")[0]
    assert 'class="described"' not in html


def test_the_image_support_renders_without_a_link_when_the_image_is_unknown():
    text = f"# T\n\n{FROM_IMAGE}\n"
    body_start = text.index(FROM_IMAGE) - len("# T\n\n") + len("# T\n\n")
    marks = [(body_start, body_start + len(FROM_IMAGE), ImageSupport("Report.pdf", "imageFile1.png", None))]
    html = render_page_verified(WIKI_ID, text, "concepts/t", marks, set())
    assert 'class="described" title="Respaldada por la descripción generada de una imagen"' in html
    assert "image-link" not in html


# ── the chat: the third state ────────────────────────────────────────────────

def test_the_chat_marks_a_sentence_supported_by_a_description():
    text = f"{GOOD} {FROM_IMAGE}"
    start = text.index(FROM_IMAGE)
    support = ImageSupport("Report.pdf", "imageFile1.png", 3, f"/w/{WIKI_ID}/images/src1/imageFile1.png")
    html = render_verified(WIKI_ID, text, [], set(), described=[(start, start + len(FROM_IMAGE), support)])
    assert f'<span class="described" title="{TIP}">{FROM_IMAGE}</span>' in html
    assert f'href="/w/{WIKI_ID}/images/src1/imageFile1.png"' in html


# ── the Figures section ──────────────────────────────────────────────────────

def test_the_folded_description_survives_the_sanitizer_and_the_alt_text_carries_the_description():
    fig = {"page": 3, "file": "imageFile1.png", "caption": "Tasas.",
           "answer": {"type": "gráfico", "decorative": False, "description": "Tasas de cinco bancos.",
                      "visible_text": [], "values": ["Nación 35,0 %"]}}
    html = render_markdown(WIKI_ID, "# T\n" + figures_section("src1", [fig], "es"), "summaries/t", set())
    assert "<details><summary>Descripción generada</summary>" in html and "Nación 35,0 %</details>" in html
    assert 'alt="Imagen, página 3. Tasas de cinco bancos."' in html
    assert f'src="/w/{WIKI_ID}/images/src1/imageFile1.png"' in html


# ── the Maintain panel ───────────────────────────────────────────────────────

@pytest.fixture
def vision_settings(settings):
    return dataclasses.replace(settings, vision_base_url="http://vision.invalid/v1", vision_api_key="k",
                               vision_model="gpt-test")


@pytest.fixture
def vision_client():
    return FakeVisionClient()


@pytest.fixture
def maintain_app(vision_settings, vision_client, monkeypatch):
    monkeypatch.setattr(AppState, "vision", lambda self: Vision(vision_client, "gpt-test"))
    return create_app(vision_settings, agents_factory=lambda wiki: fake_agents("x"))


@pytest.fixture
def maintain_client(maintain_app):
    return TestClient(maintain_app, follow_redirects=False, cookies=SPANISH)


@pytest.fixture
def ingested(maintain_app, monkeypatch):
    """A source with two images, ingested while descriptions were off."""
    wiki = maintain_app.state.web.get_wiki(WIKI_ID).wiki
    ws = SimpleNamespace(workspace=wiki.path, db_path=wiki.db_path, llm=FakeLLMClient())
    config(ws, describe=False, language=wiki.language)
    fake_extract(monkeypatch, [CHART, COVER])
    ingest(ws, None, language=wiki.language)
    config(ws, describe=True, language=wiki.language)
    return wiki


def panel(html: str) -> str:
    return html[html.index('id="images-panel"'):html.index("</section>", html.index('id="images-panel"'))]


def test_the_panel_counts_the_images_and_asks_to_confirm_with_the_number_and_the_model(maintain_client, ingested):
    html = panel(maintain_client.get(f"/w/{WIKI_ID}/maintain").text)
    assert "Imágenes · 2" in html
    assert re.search(r"<dt>Sin descripción</dt><dd>2</dd>", html) and re.search(r"<dt>Descritas</dt><dd>0</dd>", html)
    assert "¿Describir 2 imágenes con gpt-test?" in html
    assert f'hx-post="/w/{WIKI_ID}/describe-images"' in html
    assert "disabled" not in html.split("<button")[1]   # the action is on


def test_with_descriptions_off_the_action_is_disabled(maintain_client, ingested):
    config(SimpleNamespace(workspace=ingested.path), describe=False, language=ingested.language)
    html = panel(maintain_client.get(f"/w/{WIKI_ID}/maintain").text)
    assert "Las descripciones están desactivadas" in html and "disabled" in html


def test_the_action_streams_one_call_per_image_and_then_the_panel_shows_them_described(
        maintain_client, ingested, vision_client):
    response = maintain_client.post(f"/w/{WIKI_ID}/describe-images")
    lines = lines_of(maintain_client, response)
    assert any("report.pdf" in line for line in lines)
    assert lines[-1].startswith("🏁 Imágenes: 2 descritas")
    assert len(vision_client.calls) == 2
    html = panel(maintain_client.get(f"/w/{WIKI_ID}/maintain").text)
    assert re.search(r"<dt>Descritas</dt><dd>2</dd>", html) and re.search(r"<dt>Sin descripción</dt><dd>0</dd>", html)
    assert "disabled" in html.split("<button")[1]                      # nothing left to describe
    summary = (ingested.path / "wiki" / "summaries" / "report.md")
    assert "<details><summary>Generated description</summary>" in summary.read_text(encoding="utf-8")


def test_the_action_without_a_vision_model_says_so(client):
    html = client.post(f"/w/{WIKI_ID}/describe-images").text
    assert "El modelo de visión no está configurado" in html


# ── the Settings screen ──────────────────────────────────────────────────────

URL = f"/w/{WIKI_ID}/settings"


def test_the_four_images_keys_are_shown_and_saved(client, wiki):
    html = client.get(URL).text
    for name in ("describe", "min_side_px", "min_bytes", "max_per_document"):
        assert f'name="{name}"' in html
    assert 'value="64"' in html and 'value="5000"' in html and 'value="40"' in html
    client.post(URL, data={"describe": "on", "min_side_px": "32", "min_bytes": "100", "max_per_document": "9"})
    text = (wiki.path / "wiki_config.toml").read_text(encoding="utf-8")
    assert "[images]" in text and "describe = true" in text and "max_per_document = 9" in text
    again = client.get(URL).text
    assert re.search(r'name="describe"[^>]*checked', again) and 'value="32"' in again


def test_an_invalid_images_value_is_refused_with_its_message(client, wiki):
    html = client.post(URL, data={"describe": "on", "min_side_px": "-5"}).text
    assert "Escribí un número entero, 0 o más." in html
    assert not (wiki.path / "wiki_config.toml").exists() or "[images]" not in (wiki.path / "wiki_config.toml").read_text()


# ── image files as sources (D11) ────────────────────────────────────────────

def test_an_image_source_is_shown_inline_with_its_media_type(client, wiki):
    write_png(wiki.sources_dir / "grafico-tasas.png", 20, 20)
    response = client.get(f"/w/{WIKI_ID}/view/grafico-tasas.png")
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert "attachment" not in response.headers.get("content-disposition", "")


def test_the_ingest_screen_offers_the_image_formats(client, wiki):
    (wiki.path / "wiki_config.toml").write_text("[images]\nignore = false\n", encoding="utf-8")
    html = client.get(f"/w/{WIKI_ID}/ingest").text
    assert ".png" in html and ".webp" in html and "GIF, JPEG, JPG" in html
