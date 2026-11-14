"""Phase 1 of the image design: the description of the kept images
(docs/design_image_handling.md, D2 to D6, D8, D13; tests 1 to 9f of section 8).

The extractor is replaced by a fake that writes real PNG files the way `extract_pdf`
does (a temporary folder, then `replace_dir`); the vision model is a fake client. No
Java, no model, no network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from domain import tracing
from domain.ingestion import describe
from domain.ingestion.extractor import check_java
from domain.ingestion.chunker import chunk_pages
from domain.ingestion.describe import Vision
from domain.ingestion.images import (
    file_sha256, image_link, images_dir, replace_dir,
)
from domain.ingestion.pipeline import ingest_file
from domain.tools.db import get_connection
from domain.tools.reindex import reindex_from_disk
from domain.tools.search import search_chunks
from tests.helpers.fake_vision import ANSWER, DECORATIVE_ANSWER, FakeVisionClient, answer
from tests.helpers.image_ingest import CHART, COVER, EXTRACTION, config, fake_extract, figures, ingest, stored_pages
from tests.helpers.pngs import png_bytes

@pytest.fixture
def ws(tmp_workspace):
    return tmp_workspace


# ── 1. figures.json ──────────────────────────────────────────────────────────

def test_figures_json_carries_sha256_width_height_and_description(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART])
    doc_id = ingest(ws, Vision(FakeVisionClient(), "gpt-test"))
    fig = figures(ws, doc_id)["imageFile1.png"]
    assert fig["page"] == 3 and fig["caption"] == "Rates."
    assert fig["width"] == 120 and fig["height"] == 90
    assert re.fullmatch(r"[0-9a-f]{64}", fig["sha256"])
    assert fig["description"] == "imageFile1.png.desc.json"


def test_figures_json_has_the_four_fields_when_nothing_is_described(ws, monkeypatch):
    config(ws, describe=False)
    fake_extract(monkeypatch, [CHART])
    doc_id = ingest(ws, None)
    fig = figures(ws, doc_id)["imageFile1.png"]
    assert (fig["width"], fig["height"], fig["description"]) == (120, 90, "")
    assert fig["sha256"]


# ── 2. the filter of D2 ──────────────────────────────────────────────────────

def test_each_filter_gives_its_reason(ws, monkeypatch):
    config(ws, min_side_px=50, min_bytes=100, max_per_document=2)
    specs = [
        {"page": 1, "file": "small.png", "w": 20, "h": 200, "shade": 1},        # side under 50 px
        {"page": 1, "file": "light.png", "w": 60, "h": 1, "shade": 2},           # file under 100 bytes
        {"page": 2, "file": "a.png", "w": 100, "h": 100, "shade": 3},
        {"page": 2, "file": "a_again.png", "w": 100, "h": 100, "shade": 3},     # the same bytes as a.png
        {"page": 3, "file": "b.png", "w": 100, "h": 100, "shade": 4},
        {"page": 3, "file": "c.png", "w": 100, "h": 100, "shade": 5},           # the third distinct one
    ]
    fake_extract(monkeypatch, specs)
    client = FakeVisionClient()
    doc_id = ingest(ws, Vision(client, "gpt-test"))
    description = {f: fig["description"] for f, fig in figures(ws, doc_id).items()}
    assert description == {
        "small.png": "too_small", "light.png": "too_small", "a.png": "a.png.desc.json",
        "a_again.png": "duplicate", "b.png": "b.png.desc.json", "c.png": "over_limit"}
    assert len(client.calls) == 2


# ── 3. the cache ─────────────────────────────────────────────────────────────

def test_one_call_per_distinct_image_and_none_on_a_second_ingestion(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART, COVER])
    client = FakeVisionClient()
    doc_id = ingest(ws, Vision(client, "gpt-test"))
    assert len(client.calls) == 2
    first = stored_pages(ws, doc_id)

    again = ingest(ws, Vision(client, "gpt-test"), marker=b"2")   # the source changed: ingested again
    assert again == doc_id
    assert len(client.calls) == 2
    assert stored_pages(ws, doc_id) == first


def test_an_image_repeated_in_two_documents_is_described_once(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART])
    client = FakeVisionClient()
    ingest(ws, Vision(client, "gpt-test"))
    other = ws.workspace / "sources" / "other.pdf"
    other.write_bytes(b"%PDF other")
    ws.llm.response_content = EXTRACTION
    result = ingest_file(other, ws.db_path, ws.workspace, ws.llm, "fake", language="es",
                         vision=Vision(client, "gpt-test"))
    assert result.status == "ingested"
    assert len(client.calls) == 1
    assert "<!-- image-description: imageFile1.png -->" in "".join(stored_pages(ws, result.doc_id))
    assert (images_dir(ws.workspace, result.doc_id) / "imageFile1.png.desc.json").is_file()


def test_the_cache_is_keyed_by_image_and_prompt_not_by_model(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART])
    client = FakeVisionClient()
    ingest(ws, Vision(client, "model-one"))
    ingest(ws, Vision(client, "model-two"), marker=b"2")
    assert len(client.calls) == 1                      # a new model does not describe again
    monkeypatch.setattr(describe, "prompt_text", lambda language: "another prompt")
    ingest(ws, Vision(client, "model-two"), marker=b"3")
    assert len(client.calls) == 2                      # a new prompt does


def test_replace_dir_keeps_the_description_of_an_image_that_stays(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(), new.mkdir()
    (old / "a.png").write_bytes(png_bytes(10, 10, 1))
    sha = file_sha256(old / "a.png")
    (old / "a.png.desc.json").write_text(json.dumps({"image_sha256": sha, "answer": {"x": 1}}), encoding="utf-8")
    (new / "renamed.png").write_bytes(png_bytes(10, 10, 1))   # same bytes, another name
    (new / "other.png").write_bytes(png_bytes(10, 10, 9))
    replace_dir(new, old)
    assert (old / "renamed.png.desc.json").is_file()
    assert not (old / "other.png.desc.json").exists()
    assert not (old / "a.png").exists()


# ── 5. the block in the stored text ──────────────────────────────────────────

def test_the_block_is_stored_chunked_and_found_by_search(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART])
    doc_id = ingest(ws, Vision(FakeVisionClient(), "gpt-test"))
    text = "".join(stored_pages(ws, doc_id))
    link = image_link(doc_id, "imageFile1.png", 3)
    assert f"{link}\n\n<!-- image-description: imageFile1.png -->\n> **Descripción generada de la imagen de la página 3** (modelo gpt-test)." in text
    assert "> **Tipo:** bar chart. **Descripción:** Annual nominal rate of five banks at 30 days." in text
    assert "> **Valores:** Banco Nación 35.0 %; Banco Galicia 34.5 %" in text
    assert text.rstrip().endswith("<!-- /image-description -->")
    hits = search_chunks(ws.db_path, "Galicia")
    assert hits and "<!-- image-description: imageFile1.png -->" in hits[0]["content"]


def test_an_english_wiki_writes_the_english_label_and_marker(ws, monkeypatch):
    config(ws, min_bytes=100000)         # nothing described: only the marker
    (ws.workspace / "wiki_config.toml").write_text(
        '[wiki]\nlanguage = "en"\n[images]\nignore = false\ndescribe = true\nmin_bytes = 100000\n', encoding="utf-8")
    fake_extract(monkeypatch, [CHART])
    doc_id = ingest(ws, Vision(FakeVisionClient(), "gpt-test"), language="en")
    # too small: the link line keeps its marker, in English
    assert "\n\n[Image not described, page 3]" in "".join(stored_pages(ws, doc_id))

    (ws.workspace / "wiki_config.toml").write_text(
        '[wiki]\nlanguage = "en"\n[images]\nignore = false\ndescribe = true\nmin_bytes = 10\n', encoding="utf-8")
    doc_id = ingest(ws, Vision(FakeVisionClient(), "gpt-test"), language="en", marker=b"2")
    text = "".join(stored_pages(ws, doc_id))
    assert "> **Generated description of the image on page 3** (model gpt-test)." in text
    assert "**Description:**" in text and "**Values:**" in text


# ── 6. reindex_from_disk ─────────────────────────────────────────────────────

def test_reindex_rebuilds_the_blocks_with_no_model_call(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART, COVER])
    client = FakeVisionClient()
    doc_id = ingest(ws, Vision(client, "gpt-test"))
    before = "".join(stored_pages(ws, doc_id))
    assert len(client.calls) == 2

    reindex_from_disk(ws.workspace, ws.db_path)
    assert len(client.calls) == 2                       # reindex has no client at all
    with get_connection(ws.db_path) as conn:
        new_id = conn.execute("SELECT id FROM documents WHERE source_kind = 'source'").fetchone()["id"]
    assert new_id != doc_id
    after = "".join(stored_pages(ws, new_id))
    assert after.replace(new_id, "ID") == before.replace(doc_id, "ID")
    assert "<!-- image-description: imageFile2.png -->" in after
    assert search_chunks(ws.db_path, "Galicia")


# ── 8. a failed call ─────────────────────────────────────────────────────────

def test_a_failed_call_leaves_the_marker_and_does_not_fail_the_ingestion(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART])
    client = FakeVisionClient([TimeoutError("timed out")])
    doc_id = ingest(ws, Vision(client, "gpt-test"))
    text = "".join(stored_pages(ws, doc_id))
    assert "\n\n[Imagen sin describir, página 3]" in text
    assert "image-description" not in text
    assert figures(ws, doc_id)["imageFile1.png"]["description"] == "failed"
    assert not (images_dir(ws.workspace, doc_id) / "imageFile1.png.desc.json").exists()


# ── 9. goal 5 ────────────────────────────────────────────────────────────────

def test_with_describe_off_the_stored_text_is_what_extraction_wrote(ws, monkeypatch):
    config(ws, describe=False)
    fake_extract(monkeypatch, [CHART, COVER])
    client = FakeVisionClient()
    doc_id = ingest(ws, Vision(client, "gpt-test"))
    assert client.calls == []
    link1, link2 = (image_link(doc_id, f, p) for f, p in (("imageFile1.png", 3), ("imageFile2.png", 1)))
    assert stored_pages(ws, doc_id) == [f"Text of page 1.\n\n{link2}", f"Text of page 3.\n\n{link1}"]


def test_a_wiki_with_no_config_file_does_not_describe(ws, monkeypatch):
    fake_extract(monkeypatch, [CHART])
    client = FakeVisionClient()
    doc_id = ingest(ws, Vision(client, "gpt-test"))
    assert client.calls == [] and "image-description" not in "".join(stored_pages(ws, doc_id))


# ── 9a. decorative ───────────────────────────────────────────────────────────

def test_a_decorative_image_gets_no_block_and_no_marker_and_is_not_asked_again(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [COVER])
    client = FakeVisionClient([json.dumps(DECORATIVE_ANSWER)])
    doc_id = ingest(ws, Vision(client, "gpt-test"))
    text = "".join(stored_pages(ws, doc_id))
    assert text == f"Text of page 1.\n\n{image_link(doc_id, 'imageFile2.png', 1)}"
    assert figures(ws, doc_id)["imageFile2.png"]["description"] == "decorative"
    ingest(ws, Vision(client, "gpt-test"), marker=b"2")
    assert len(client.calls) == 1


# ── 9b. a malformed answer ───────────────────────────────────────────────────

def test_a_malformed_answer_is_asked_for_once_more(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART])
    client = FakeVisionClient(["this is not JSON", answer()])
    doc_id = ingest(ws, Vision(client, "gpt-test"))
    assert len(client.calls) == 2
    assert "<!-- image-description: imageFile1.png -->" in "".join(stored_pages(ws, doc_id))


def test_a_second_malformed_answer_is_the_reason_failed(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART])
    client = FakeVisionClient(["no", '{"description": "x"}'])
    doc_id = ingest(ws, Vision(client, "gpt-test"))
    assert len(client.calls) == 2
    assert figures(ws, doc_id)["imageFile1.png"]["description"] == "failed"


def test_the_answer_may_come_in_a_code_fence():
    assert describe.parse_answer("```json\n" + answer() + "\n```")["type"] == "bar chart"


# ── the description file (D4) ────────────────────────────────────────────────

def test_the_description_file_holds_the_metadata_and_the_answer(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART])
    doc_id = ingest(ws, Vision(FakeVisionClient(), "gpt-test"))
    data = json.loads((images_dir(ws.workspace, doc_id) / "imageFile1.png.desc.json").read_text(encoding="utf-8"))
    assert set(data) == {"image_sha256", "model", "prompt_sha256", "language", "generated_at",
                         "tokens_input", "tokens_output", "answer"}
    assert data["model"] == "gpt-test" and data["language"] == "es"
    assert (data["tokens_input"], data["tokens_output"]) == (812, 146)
    assert data["image_sha256"] == figures(ws, doc_id)["imageFile1.png"]["sha256"]
    assert data["prompt_sha256"] == describe.prompt_sha256("es")
    assert data["answer"] == ANSWER


# ── 9c. the chunker never cuts a block ───────────────────────────────────────

def _block(file="imageFile1.png", words=1200):
    body = " ".join(f"valor{i}" for i in range(words))
    return (f"<!-- image-description: {file} -->\n> **Generated.** (model m).\n> **Description:** {body}\n"
            "<!-- /image-description -->")


def test_the_chunker_keeps_a_long_block_whole_and_alone():
    author = "The author wrote a sentence about the rates. " * 30
    block = _block()
    chunks = chunk_pages([(1, f"{author}\n\n![Imagen, página 1](.llmwiki/cache/local/d/images/a.png)\n\n{block}\n\n{author}")])
    holding = [c for c in chunks if "image-description" in c.content]
    assert len(holding) == 1 and holding[0].content == block
    assert all("author wrote" not in c.content for c in holding)
    assert all("valor1 " not in c.content for c in chunks if c is not holding[0])


def test_a_short_block_survives_the_minimum_size():
    short = "<!-- image-description: a.png -->\n> x\n<!-- /image-description -->"
    chunks = chunk_pages([(1, f"{'Words here. ' * 40}\n\n{short}")])
    assert [c.content for c in chunks if "image-description" in c.content] == [short]


# ── 9e. the trace ────────────────────────────────────────────────────────────

def test_the_prompts_sidecar_of_a_description_holds_a_reference_not_the_image(ws, monkeypatch):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    config(ws)
    fake_extract(monkeypatch, [CHART])
    doc_id = ingest(ws, Vision(FakeVisionClient(), "gpt-test"))
    payloads = list((ws.workspace / ".llmwiki" / "traces" / "payloads").glob("*.json"))
    sidecars = [json.loads(p.read_text(encoding="utf-8")) for p in payloads]
    request = next(m for m in sidecars if isinstance(m, list) and m and isinstance(m[-1].get("content"), list))
    part = request[-1]["content"][0]
    path = images_dir(ws.workspace, doc_id) / "imageFile1.png"
    import hashlib
    assert part == {"image": "imageFile1.png", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "bytes": path.stat().st_size}
    assert request[0]["role"] == "system" and "JSON" in request[0]["content"]
    assert all("base64" not in p.read_text(encoding="utf-8") for p in payloads)


def test_the_request_to_the_model_does_carry_the_image(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART])
    client = FakeVisionClient()
    ingest(ws, Vision(client, "gpt-test"))
    url = client.calls[0]["messages"][-1]["content"][0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")


def test_the_nodes_i17_i18_and_i19_are_recorded(ws, monkeypatch):
    config(ws, max_per_document=1)
    fake_extract(monkeypatch, [CHART, COVER])
    with tracing.collect() as path:
        ingest(ws, Vision(FakeVisionClient(), "gpt-test"))
    nodes = {e["node"]: e["attributes"] for e in path}
    assert nodes["I17"]["count"] == 2 and nodes["I17"]["total_bytes"] > 0
    assert nodes["I18"]["file"] == "imageFile2.png" or nodes["I18"]["file"] == "imageFile1.png"
    assert nodes["I18"]["cached"] is False and nodes["I18"]["tokens_input"] == 812
    assert nodes["I19"]["reason"] == "over_limit"


# ── the ingestion prompts (D6) and the log line (D13) ───────────────────────

def test_the_ingestion_prompts_gain_the_note_only_when_the_text_holds_a_description(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART])
    doc_id = ingest(ws, Vision(FakeVisionClient(), "gpt-test"))
    system = ws.llm.calls[0]["messages"][0]["content"]
    assert describe.note_for("es") in system and "image-description" in system
    config(ws, describe=False)
    ws.llm.calls.clear()
    ingest(ws, None, marker=b"2")
    assert "image-description" not in ws.llm.calls[0]["messages"][0]["content"]
    assert doc_id


def test_the_ingestion_log_counts_the_images(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART, COVER])
    ingest(ws, Vision(FakeVisionClient([answer(), json.dumps(DECORATIVE_ANSWER)]), "gpt-test"))
    assert "2 imágenes, 1 descritas" in (ws.workspace / "wiki" / "log.md").read_text(encoding="utf-8")


def test_the_description_prompt_exists_in_both_languages_and_asks_for_the_fixed_keys():
    from domain.prompts import load_prompt

    for language in ("en", "es"):
        text = load_prompt("image_description", language)
        for key in ("type", "decorative", "description", "visible_text", "values"):
            assert f'"{key}"' in text
    assert load_prompt("image_description", "es") != load_prompt("image_description", "en")


# ── the settings (D2, D3) ────────────────────────────────────────────────────

def test_the_images_section_has_safe_defaults(tmp_path):
    from domain.wiki_settings import ImagesConfig, load_images_config

    assert load_images_config(tmp_path) == ImagesConfig(describe=False, min_side_px=64, min_bytes=5000, max_per_document=40)
    (tmp_path / "wiki_config.toml").write_text("[images]\ndescribe = 1\nmin_side_px = -3\nmax_per_document = 'x'\n")
    assert load_images_config(tmp_path) == ImagesConfig(describe=False, min_side_px=64, min_bytes=5000, max_per_document=40)
    (tmp_path / "wiki_config.toml").write_text("[images]\ndescribe = true\nmin_bytes = 0\nmax_per_document = 7\n")
    assert load_images_config(tmp_path) == ImagesConfig(describe=True, min_side_px=64, min_bytes=0, max_per_document=7)


def test_the_vision_settings_fall_back_to_the_ingestion_then_the_chat_settings():
    from config import Settings

    def settings(**values):
        return Settings(_env_file=None, **values)

    chat = dict(LLM_BASE_URL="http://chat", LLM_API_KEY="k-chat", LLM_MODEL="m-chat")
    assert settings(**chat).vision() == ("http://chat", "k-chat", "m-chat")
    both = settings(**chat, WIKI_LLM_MODEL="m-wiki", WIKI_LLM_BASE_URL="http://wiki")
    assert both.vision() == ("http://wiki", "k-chat", "m-wiki")
    own = settings(**chat, WIKI_LLM_MODEL="m-wiki", VISION_MODEL="m-vision", VISION_API_KEY="k-vision")
    assert own.vision() == ("http://chat", "k-vision", "m-vision")


def test_the_web_settings_carry_the_vision_group(monkeypatch):
    from config import settings
    from web.settings import WebSettings

    monkeypatch.setattr(settings, "VISION_MODEL", "vision-model")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "http://chat")
    web = WebSettings.from_env()
    assert web.vision_model == "vision-model" and web.vision_base_url == "http://chat"


# ── the real converter ───────────────────────────────────────────────────────

REPO = Path(__file__).resolve().parents[2]
SNOW_WHITE = REPO / "examples" / "fairy-tales" / "sources" / "Snow White and the Seven Dwarfs.pdf"


@pytest.mark.skipif(check_java() is None, reason="Java is missing: opendataloader-pdf needs it")
def test_the_ten_images_of_snow_white_are_described_once_and_a_second_ingestion_asks_for_none(ws):
    import shutil

    (ws.workspace / "wiki_config.toml").write_text('[images]\nignore = false\ndescribe = true\n', encoding="utf-8")
    source = ws.workspace / "sources" / SNOW_WHITE.name
    shutil.copy(SNOW_WHITE, source)
    client = FakeVisionClient()
    ws.llm.response_content = EXTRACTION
    first = ingest_file(source, ws.db_path, ws.workspace, ws.llm, "fake", language="es", vision=Vision(client, "gpt-test"))
    assert first.status == "ingested", first.message
    assert len(client.calls) == 10
    text = "".join(stored_pages(ws, first.doc_id))
    assert text.count("<!-- image-description:") == 10
    assert len(list(images_dir(ws.workspace, first.doc_id).glob("*.desc.json"))) == 10

    source.write_bytes(source.read_bytes() + b"\n%%touched")
    second = ingest_file(source, ws.db_path, ws.workspace, ws.llm, "fake", language="es", vision=Vision(client, "gpt-test"))
    assert second.status == "ingested", second.message
    assert len(client.calls) == 10                        # the descriptions survived the new extraction
    assert "".join(stored_pages(ws, second.doc_id)).count("<!-- image-description:") == 10
