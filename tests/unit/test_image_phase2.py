"""Phase 2 of the image design: the third state of the verification (D7), the two lint
checks (D9) and the Figures section (D12). No model, no network."""

from __future__ import annotations

import json
import uuid

import pytest

from domain.chat.config import load_config
from domain.chat.settings_writer import current, defaults, parse_form, write_settings
from domain.ingestion import describe
from domain.ingestion.describe import Vision
from domain.ingestion.describe_pass import describe_wiki_images, image_counts, replace_figures_section
from domain.ingestion.images import desc_name, images_dir
from domain.ingestion.wiki_generator import figures_section
from domain.lint.image_checks import (
    image_without_description_check, orphan_image_check, stale_image_description_check,
)
from domain.lint.runner import lint_wiki
from domain.repair.runner import repair_wiki
from domain.tools.db import get_connection, open_db, seed_workspace_row
from domain.verify import ContextPassage, FakeVerifier, verify_answer
from domain.verify.models import image_source, split_source
from domain.verify.passages import split_passages
from domain.verify.store import load_verification
from domain.verify.page import verify_page
from tests.helpers.fake_vision import FakeVisionClient
from tests.helpers.image_ingest import CHART, COVER, config, fake_extract, figures, ingest, stored_pages
from tests.unit.test_verify_page import add_page, add_source

BLOCK = (
    "<!-- image-description: imageFile1.png -->\n"
    "> **Descripción generada de la imagen de la página 3** (modelo gpt-test).\n"
    "> **Tipo:** gráfico de barras. **Descripción:** Tasa nominal anual de cinco bancos a treinta días.\n"
    "> **Valores:** Banco Nación 35,0 %; Banco Galicia 34,5 %\n"
    "<!-- /image-description -->"
)
AUTHOR = "El informe analiza los plazos fijos de los bancos argentinos durante el año."
SOURCE_TEXT = f"{AUTHOR}\n\n![Imagen, página 3](.llmwiki/cache/local/d/images/imageFile1.png)\n\n{BLOCK}\n\nFin del informe."


# ── D5 / D7: the passages ────────────────────────────────────────────────────

def test_a_block_is_one_passage_of_its_own_and_no_passage_mixes_it_with_the_text():
    passages = split_passages(ContextPassage("sources/r.pdf", SOURCE_TEXT), max_chars=200)
    images = [p for p in passages if p.image]
    assert [p.image for p in images] == ["imageFile1.png"]
    assert "Banco Galicia 34,5 %" in images[0].text and "<!--" not in images[0].text and not images[0].text.startswith(">")
    assert all("Banco Galicia" not in p.text for p in passages if not p.image)
    assert any(AUTHOR in p.text for p in passages) and any("Fin del informe." in p.text for p in passages)


def test_a_block_longer_than_the_passage_limit_stays_whole():
    long_block = BLOCK.replace("Banco Galicia 34,5 %", "x" * 3000)
    passages = split_passages(ContextPassage("sources/r.pdf", f"{AUTHOR}\n\n{long_block}"), max_chars=300)
    (image,) = [p for p in passages if p.image]
    assert "x" * 3000 in image.text


def test_the_fragment_of_a_source_is_separated_by_one_function():
    assert image_source("sources/r.pdf", "imageFile1.png") == "sources/r.pdf#image:imageFile1.png"
    assert image_source("sources/r.pdf", None) == "sources/r.pdf"
    assert split_source("sources/r.pdf#image:imageFile1.png") == ("sources/r.pdf", "imageFile1.png")
    assert split_source("sources/r.pdf") == ("sources/r.pdf", None)
    assert split_source(None) == (None, None)


# ── D7: the third state ──────────────────────────────────────────────────────

def words(text: str) -> set[str]:
    return {w.strip(".,;%").lower() for w in text.split() if len(w) > 3}


def overlap(passage: str, sentence: str) -> float:
    wanted = words(sentence)
    return len(wanted & words(passage)) / len(wanted) if wanted else 0.0


def test_a_sentence_supported_only_by_a_description_gets_the_third_state():
    answer = "Banco Nación paga 35,0 % de tasa nominal anual. El informe analiza los plazos fijos de los bancos argentinos."
    report = verify_answer(answer, [ContextPassage("sources/r.pdf", SOURCE_TEXT)],
                           FakeVerifier(overlap), threshold=0.5)
    first, second = report.sentences
    assert first.supported and first.page == "sources/r.pdf#image:imageFile1.png" and first.supported_by_image
    assert second.supported and second.page == "sources/r.pdf" and not second.supported_by_image
    assert (report.described, report.unsupported) == (1, 0)
    assert report.described_sentences() == [first]


def test_the_source_text_wins_over_a_description_that_repeats_the_same_figure():
    text = f"El plazo fijo del Banco Nación paga 35,0 % de tasa nominal anual.\n\n{BLOCK}"
    answer = "El Banco Nación paga 35,0 % de tasa nominal anual."
    # The description scores higher than the text, and both reach the threshold.
    verifier = FakeVerifier(lambda passage, sentence: 0.9 if "Descripción" in passage or "Tasa nominal" in passage else 0.7)
    report = verify_answer(answer, [ContextPassage("sources/r.pdf", text)], verifier, threshold=0.6)
    (sentence,) = report.sentences
    assert sentence.supported and sentence.page == "sources/r.pdf" and not sentence.supported_by_image
    assert report.described == 0


def test_a_description_is_the_best_passage_only_when_no_text_passage_reaches_the_threshold():
    text = f"Un texto sin relación con la pregunta.\n\n{BLOCK}"
    verifier = FakeVerifier(lambda passage, sentence: 0.8 if "Banco Galicia" in passage else 0.3)
    report = verify_answer("El Banco Galicia paga 34,5 % de tasa.", [ContextPassage("sources/r.pdf", text)],
                           verifier, threshold=0.6)
    assert report.sentences[0].supported_by_image
    low = verify_answer("El Banco Galicia paga 34,5 % de tasa.", [ContextPassage("sources/r.pdf", text)],
                        FakeVerifier(lambda passage, sentence: 0.2), threshold=0.6)
    assert low.unsupported == 1 and low.described == 0


def test_a_text_only_source_gives_the_same_result_as_before():
    report = verify_answer(AUTHOR, [ContextPassage("sources/r.pdf", AUTHOR)], FakeVerifier(overlap), threshold=0.5)
    assert report.sentences[0].page == "sources/r.pdf" and report.described == 0


# 9d: the chat verification
def test_the_chat_verification_gives_the_third_state_to_a_passage_inside_a_block():
    from domain.verify.context import passages_from_tool_return

    hit = f"**sources/r.pdf** p.3\n```\n{BLOCK}\n```"
    (passage,) = passages_from_tool_return("search_source_chunks", hit)
    report = verify_answer("El Banco Nación paga 35,0 % de tasa nominal anual.", [passage],
                           FakeVerifier(overlap), threshold=0.5)
    (sentence,) = report.sentences
    assert sentence.supported_by_image and sentence.page == "sources/r.pdf#image:imageFile1.png"


# ── D7: stored with the page verification ───────────────────────────────────

@pytest.fixture
def wiki(tmp_path):
    db = str(tmp_path / ".llmwiki" / "index.db")
    open_db(db).close()
    seed_workspace_row(db, "w")
    (tmp_path / "wiki").mkdir()
    return db, tmp_path


def test_the_page_verification_stores_the_image_as_a_fragment_of_best_source(wiki):
    src = add_source(wiki[0], "r.pdf", [SOURCE_TEXT])
    body = ("## Resumen\n\nEl Banco Nación paga 35,0 % de tasa nominal anual.\n\n"
            "El informe analiza los plazos fijos de los bancos argentinos durante el año.\n")
    info = add_page(wiki, "summaries", "r", body, sources=["r.pdf"], source_document_id=src)
    db, root = wiki
    outcome = verify_page(db, root, info["path"], FakeVerifier(overlap), threshold=0.5, candidates=5, language="es")
    assert outcome.report.described == 1
    with get_connection(db) as conn:
        stored = load_verification(conn, info["id"])
    assert [s.source for s in stored.sentences] == ["sources/r.pdf#image:imageFile1.png", "sources/r.pdf"]
    assert all(s.supported(0.5) for s in stored.sentences)


def test_the_figures_section_is_not_verified_because_the_code_wrote_it():
    from domain.verify.page import generated_texts

    body = "# T\n\nTexto.\n\n## Figuras\n\n- Descripción copiada de la imagen.\n\n## Fuentes\n- r.pdf\n"
    skipped = " ".join(generated_texts(body))
    assert "Descripción copiada" in skipped and "Texto." not in skipped


# ── D12: the Figures section ─────────────────────────────────────────────────

FIG = {"page": 3, "file": "imageFile1.png", "caption": "Tasas.",
       "answer": {"type": "gráfico", "decorative": False, "description": "Tasas de cinco bancos [a 30 días].",
                  "visible_text": ["TNA"], "values": ["Nación 35,0 %"]}, "model": "gpt-test"}


def test_the_figures_section_folds_the_description_and_puts_it_in_the_alt_text():
    section = figures_section("d", [FIG], "es")
    assert "![Imagen, página 3. Tasas de cinco bancos (a 30 días).](.llmwiki/cache/local/d/images/imageFile1.png)" in section
    assert "<details><summary>Descripción generada</summary>" in section
    assert "Valores: Nación 35,0 %" in section and section.rstrip().endswith("</details>")
    english = figures_section("d", [FIG], "en")
    assert "<summary>Generated description</summary>" in english and "Values: Nación 35,0 %" in english


def test_a_decorative_or_undescribed_image_keeps_the_plain_entry():
    plain = figures_section("d", [{"page": 3, "file": "a.png", "caption": ""}], "en")
    decorative = figures_section("d", [{"page": 3, "file": "a.png", "caption": "",
                                        "answer": {"decorative": True, "description": "frame"}}], "en")
    assert plain == decorative and "<details" not in plain
    assert "![Imagen, página 3](" in plain


def test_markup_inside_a_description_cannot_leave_the_folded_block():
    fig = {**FIG, "answer": {**FIG["answer"], "description": "</details><script>x</script> *bold* _it_"}}
    section = figures_section("d", [fig], "en")
    assert section.count("</details>") == 1 and "<script>" not in section and "*bold*" not in section


def test_replacing_the_figures_section_keeps_the_rest_of_the_page():
    page = "# T\n\nResumen.\n\n## Figures\n\n- old\n\n## Source Information\n\n- File: a\n"
    new = replace_figures_section(page, "\n## Figures\n\n- new\n", "en")
    assert new == "# T\n\nResumen.\n\n## Figures\n\n- new\n\n## Source Information\n\n- File: a\n"
    assert replace_figures_section("# T\n\nNo figures.\n", "\n## Figures\n\n- x\n", "en") == "# T\n\nNo figures.\n"


# ── D9: the lint checks ──────────────────────────────────────────────────────

@pytest.fixture
def ws(tmp_workspace):
    return tmp_workspace


def test_images_kept_before_describe_was_turned_on_are_reported(ws, monkeypatch):
    config(ws, describe=False)
    fake_extract(monkeypatch, [CHART, COVER])
    doc_id = ingest(ws, None)
    assert image_without_description_check(ws.db_path, ws.workspace) == []   # describe is off

    config(ws, describe=True)
    (issue,) = image_without_description_check(ws.db_path, ws.workspace)
    assert issue.check == "image_without_description" and "2 image(s)" in issue.description
    assert issue.page == "sources/report.pdf"

    client = FakeVisionClient()
    describe_wiki_images(ws.workspace, ws.db_path, Vision(client, "gpt-test"))
    assert len(client.calls) == 2
    assert image_without_description_check(ws.db_path, ws.workspace) == []
    assert "<!-- image-description: imageFile1.png -->" in "".join(stored_pages(ws, doc_id))


def test_an_image_with_a_skip_reason_is_not_reported(ws, monkeypatch):
    config(ws, min_bytes=100000)
    fake_extract(monkeypatch, [CHART])
    ingest(ws, Vision(FakeVisionClient(), "gpt-test"))
    assert image_without_description_check(ws.db_path, ws.workspace) == []


def test_a_changed_prompt_makes_the_descriptions_stale_and_the_pass_describes_again(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART])
    client = FakeVisionClient()
    doc_id = ingest(ws, Vision(client, "gpt-test"))
    assert stale_image_description_check(ws.db_path, ws.workspace) == []

    monkeypatch.setattr(describe, "prompt_text", lambda language: "an improved prompt")
    (issue,) = stale_image_description_check(ws.db_path, ws.workspace)
    assert issue.check == "stale_image_description" and "1 description(s)" in issue.description
    assert image_counts(ws.workspace, ws.db_path).stale == 1
    assert image_counts(ws.workspace, ws.db_path).to_describe == 1

    describe_wiki_images(ws.workspace, ws.db_path, Vision(client, "gpt-test"))
    assert len(client.calls) == 2
    assert stale_image_description_check(ws.db_path, ws.workspace) == []
    assert doc_id


def test_a_changed_image_makes_its_description_stale(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART])
    doc_id = ingest(ws, Vision(FakeVisionClient(), "gpt-test"))
    (images_dir(ws.workspace, doc_id) / "imageFile1.png").write_bytes(b"another image")
    assert len(stale_image_description_check(ws.db_path, ws.workspace)) == 1


def test_neither_check_is_repaired_by_the_repair(ws, monkeypatch):
    config(ws, describe=False)
    fake_extract(monkeypatch, [CHART])
    ingest(ws, None)
    config(ws, describe=True)
    report = lint_wiki(ws.db_path, ws.workspace)
    found = [i for i in report.issues if i.check == "image_without_description"]
    assert len(found) == 1
    repairs = repair_wiki(report, ws.db_path, ws.workspace)
    mine = [r for r in repairs.results if r.check == "image_without_description"]
    assert [(r.action, r.success) for r in mine] == [("skipped", True)]
    assert not (images_dir(ws.workspace, report and found[0] and _doc_id(ws)) / "imageFile1.png.desc.json").exists()


def _doc_id(ws) -> str:
    with get_connection(ws.db_path) as conn:
        return conn.execute("SELECT id FROM documents WHERE source_kind = 'source'").fetchone()["id"]


def test_the_orphan_check_deletes_the_description_of_an_image_that_no_longer_exists(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART])
    doc_id = ingest(ws, Vision(FakeVisionClient(), "gpt-test"))
    folder = images_dir(ws.workspace, doc_id)
    assert orphan_image_check(ws.db_path, ws.workspace) == []   # the description of a live image is not an orphan

    (folder / "imageFile1.png").unlink()
    (issue,) = orphan_image_check(ws.db_path, ws.workspace)
    assert issue.target == "imageFile1.png.desc.json"
    report = lint_wiki(ws.db_path, ws.workspace)
    report.issues = [i for i in report.issues if i.check == "orphan_image"]
    repairs = repair_wiki(report, ws.db_path, ws.workspace)
    assert [(r.check, r.action) for r in repairs.results] == [("orphan_image", "deleted")]
    assert not (folder / "imageFile1.png.desc.json").exists()


def test_the_lint_runs_the_two_new_checks(ws, monkeypatch):
    config(ws)
    fake_extract(monkeypatch, [CHART])
    ingest(ws, Vision(FakeVisionClient(), "gpt-test"))
    progress: list[str] = []
    lint_wiki(ws.db_path, ws.workspace, progress_cb=progress.append)
    assert "🔎 lint: images without description" in progress and "🔎 lint: stale image descriptions" in progress


def test_the_counts_of_the_images(ws, monkeypatch):
    config(ws, min_bytes=100, max_per_document=2)
    specs = [CHART, COVER,
             {"page": 2, "file": "tiny.png", "w": 4, "h": 4, "shade": 7},
             {"page": 2, "file": "third.png", "w": 90, "h": 90, "shade": 8}]
    fake_extract(monkeypatch, specs)
    client = FakeVisionClient([json.dumps({"type": "frame", "decorative": True, "description": "x",
                                           "visible_text": [], "values": []}), "{}", "{}"])
    ingest(ws, Vision(client, "gpt-test"))
    counts = image_counts(ws.workspace, ws.db_path)
    assert counts.kept == 4 and counts.enabled
    assert counts.skipped["too_small"] == 1 and counts.skipped["over_limit"] == 1
    assert counts.skipped["decorative"] + counts.described + counts.skipped["failed"] == 2
    assert counts.to_describe >= 0


# ── D12: the four keys of the Settings screen ───────────────────────────────

def test_the_four_images_keys_are_edited_like_the_other_settings(tmp_path):
    (tmp_path / "wiki_config.toml").write_text('[wiki]\nlanguage = "es"\n', encoding="utf-8")
    assert current(load_config(tmp_path))["images.describe"] is False
    assert defaults("es")["images.max_per_document"] == 40
    values, errors = parse_form({"images.describe": True, "images.min_side_px": "32", "images.min_bytes": "0",
                                 "images.max_per_document": "10"})
    assert errors == {}
    assert write_settings(tmp_path, values) == [
        "images.describe", "images.min_side_px", "images.min_bytes", "images.max_per_document"]
    config_now = load_config(tmp_path)
    assert (config_now.images.describe, config_now.images.min_side_px, config_now.images.min_bytes,
            config_now.images.max_per_document) == (True, 32, 0, 10)


@pytest.mark.parametrize("raw,key,code", [
    ({"images.min_side_px": "-1"}, "images.min_side_px", "negative"),
    ({"images.min_bytes": "abc"}, "images.min_bytes", "negative"),
    ({"images.max_per_document": "0"}, "images.max_per_document", "not_positive"),
])
def test_invalid_images_values_are_refused(raw, key, code):
    _, errors = parse_form(raw)
    assert errors == {key: code}


def test_desc_name_is_the_image_name_plus_the_suffix():
    assert desc_name("imageFile4.png") == "imageFile4.png.desc.json"
    assert figures and uuid  # the helpers stay imported for the fixtures above
