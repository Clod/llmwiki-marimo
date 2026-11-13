"""OCR for PDFs with no text layer: detection, the three engines, status.

No test calls a model or the network. The engines run with fakes (a replaced
subprocess, a simulated OpenAI client); the one test of the real local engine
skips when ocrmypdf or tesseract (with the Spanish pack) is missing.
"""

import base64
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from domain.ingestion import extractor, ocr
from domain.ingestion.ocr import (
    OcrNotConfiguredError, TesseractEngine, VisionEngine, apply_ocr, engine_status,
    page_needs_ocr, pages_needing_ocr, strip_fence,
)
from tests.helpers.scanned_pdf import SPANISH_TEXT, scanned_pdf, text_pdf

TEXT = "Una página con texto suficiente para tener capa de texto."


def settings(**values):
    base = dict(OCR_ENGINE="none", OCR_BASE_URL="", OCR_API_KEY="", OCR_MODEL="",
                LLM_BASE_URL="", LLM_API_KEY="", LLM_MODEL="")
    return SimpleNamespace(**{**base, **values})


# ── Detection ────────────────────────────────────────────────────────────────

def test_a_page_with_text_does_not_need_ocr():
    assert not page_needs_ocr(TEXT)


@pytest.mark.parametrize("markdown", ["", "   \n ", "3", "![image](a.png)", "![image](a.png)\n\n12"])
def test_a_page_with_almost_no_text_needs_ocr(markdown):
    assert page_needs_ocr(markdown)


def test_a_text_document_does_not_need_ocr():
    assert pages_needing_ocr([(1, TEXT), (2, TEXT), (3, TEXT)]) == []


def test_one_blank_page_in_a_long_text_document_does_not_trigger_ocr():
    pages = [(n, TEXT) for n in range(1, 10)] + [(10, "")]
    assert pages_needing_ocr(pages) == []


def test_a_scanned_document_needs_every_page():
    assert pages_needing_ocr([(1, ""), (2, ""), (3, "")]) == [1, 2, 3]


def test_a_mixed_document_selects_the_scanned_pages():
    assert pages_needing_ocr([(1, TEXT), (2, ""), (3, TEXT), (4, "")]) == [2, 4]


def test_a_document_with_no_pages_needs_no_ocr():
    assert pages_needing_ocr([]) == []


def test_the_fixtures_are_what_they_claim(tmp_path):
    import pypdfium2

    def layers(path):
        document = pypdfium2.PdfDocument(str(path))
        return [document[i].get_textpage().get_text_range().strip() for i in range(len(document))]

    assert layers(scanned_pdf(tmp_path / "s.pdf", [SPANISH_TEXT, SPANISH_TEXT])) == ["", ""]
    assert all(layers(text_pdf(tmp_path / "t.pdf", [TEXT.replace("á", "a").replace("í", "i")] * 2)))


# ── engine none ──────────────────────────────────────────────────────────────

def test_none_is_the_default_and_selects_no_engine():
    assert ocr.get_engine(settings()) is None
    assert ocr.get_engine(settings(OCR_ENGINE="unknown")) is None


def test_none_fails_a_scanned_pdf_with_a_message_that_names_the_setting_and_the_engines(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "get_engine", lambda: None)
    with pytest.raises(OcrNotConfiguredError) as error:
        apply_ocr(tmp_path / "scan.pdf", [(1, ""), (2, "")], tmp_path / "cache")
    message = str(error.value)
    assert "OCR_ENGINE" in message and "tesseract" in message and "vision" in message
    assert "scan.pdf" in message


def test_none_leaves_a_text_pdf_alone(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "get_engine", lambda: pytest.fail("no engine is needed"))
    pages = [(1, TEXT)]
    assert apply_ocr(tmp_path / "a.pdf", pages, tmp_path / "cache") == (pages, None)


def test_extract_fails_a_scanned_pdf_when_ocr_is_off(monkeypatch, tmp_path):
    monkeypatch.setattr(extractor, "check_java", lambda: "/usr/bin/java")
    monkeypatch.setattr(extractor, "extract_pdf", lambda path, images_dir=None: [(1, ""), (2, "")])
    monkeypatch.setattr(ocr, "get_engine", lambda: None)
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(OcrNotConfiguredError):
        extractor.extract(pdf, tmp_path / "cache")


def test_a_docx_never_goes_through_ocr(monkeypatch, tmp_path):
    monkeypatch.setattr(extractor, "check_java", lambda: "/usr/bin/java")
    monkeypatch.setattr(extractor, "convert_docx_to_pdf", lambda path, cache: tmp_path / "converted.pdf")
    monkeypatch.setattr(extractor, "extract_pdf", lambda path, images_dir=None: [(1, "")])
    monkeypatch.setattr(extractor, "apply_ocr", lambda *a, **k: pytest.fail("OCR on a DOCX"))
    docx = tmp_path / "a.docx"
    docx.write_bytes(b"PK")
    pages, parser = extractor.extract(docx, tmp_path / "cache")
    assert pages == [(1, "")] and parser == "libreoffice+opendataloader"


# ── Applying an engine ───────────────────────────────────────────────────────

class FakeEngine:
    name, label = "fake", "Fake"

    def __init__(self, supports_pages=True):
        self.supports_pages = supports_pages
        self.calls = []

    def run(self, pdf_path, pages, language, *, cache_dir, progress=None):
        self.calls.append((pdf_path, list(pages), language))
        return [(n, f"ocr {n}") for n in pages]


def test_a_mixed_pdf_runs_the_engine_only_on_the_scanned_pages(tmp_path):
    engine, lines = FakeEngine(), []
    pages, name = apply_ocr(tmp_path / "m.pdf", [(1, TEXT), (2, ""), (3, TEXT), (4, "")], tmp_path / "c", "es",
                            progress=lines.append, engine=engine)
    assert engine.calls == [(tmp_path / "m.pdf", [2, 4], "es")]
    assert pages == [(1, TEXT), (2, "ocr 2"), (3, TEXT), (4, "ocr 4")] and name == "fake"
    assert lines == ["🔍 OCR (Fake) en 2 páginas de m.pdf"]


def test_an_engine_that_cannot_read_a_subset_reads_every_page_and_the_text_pages_keep_their_text(tmp_path):
    engine = FakeEngine(supports_pages=False)
    pages, _ = apply_ocr(tmp_path / "m.pdf", [(1, TEXT), (2, "")], tmp_path / "c", engine=engine)
    assert engine.calls[0][1] == [1, 2]
    assert pages == [(1, TEXT), (2, "ocr 2")]


def test_a_stale_ocr_copy_is_removed_when_the_document_no_longer_needs_ocr(tmp_path):
    cache = tmp_path / "c"
    cache.mkdir()
    (cache / "ocr.pdf").write_bytes(b"%PDF")
    apply_ocr(tmp_path / "a.pdf", [(1, TEXT)], cache)
    assert not (cache / "ocr.pdf").exists()


# ── engine tesseract ─────────────────────────────────────────────────────────

def test_tesseract_runs_ocrmypdf_with_the_wiki_language_and_writes_ocr_pdf_in_the_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0, stderr=b""))
    monkeypatch.setattr(ocr, "extract_pdf", lambda path: [(1, TEXT), (2, "texto leído"), (3, "otro texto")])
    cache = tmp_path / "cache" / "doc"
    result = TesseractEngine().run(tmp_path / "scan.pdf", [3, 2], "es", cache_dir=cache)
    command = calls[0]
    assert command[0] == "/usr/bin/ocrmypdf"
    assert command[command.index("-l") + 1] == "spa"
    assert command[command.index("--pages") + 1] == "2,3"
    assert "--skip-text" in command
    assert command[-2:] == [str(tmp_path / "scan.pdf"), str(cache / "ocr.pdf")]
    assert result == [(2, "texto leído"), (3, "otro texto")]


@pytest.mark.parametrize("language, pack", [("es", "spa"), ("en", "eng"), ("fr", "eng")])
def test_the_tesseract_language_follows_the_wiki_language(language, pack):
    assert ocr.tesseract_language(language) == pack


def test_tesseract_reports_a_failed_ocrmypdf(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: SimpleNamespace(returncode=6, stderr=b"boom"))
    with pytest.raises(RuntimeError, match="exit 6.*boom"):
        TesseractEngine().run(tmp_path / "a.pdf", [1], "en", cache_dir=tmp_path)


def test_tesseract_without_the_programs_says_how_to_install_them(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr.shutil, "which", lambda name: None)
    with pytest.raises(ocr.OcrEngineUnavailableError, match="apt-get install ocrmypdf"):
        TesseractEngine().run(tmp_path / "a.pdf", [1], "en", cache_dir=tmp_path)


def test_apply_ocr_with_tesseract_reports_the_engine_in_the_parser_name(monkeypatch, tmp_path):
    monkeypatch.setattr(extractor, "check_java", lambda: "/usr/bin/java")
    monkeypatch.setattr(extractor, "extract_pdf", lambda path, images_dir=None: [(1, "")])
    monkeypatch.setattr(ocr, "get_engine", lambda: FakeEngine())
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF")
    lines = []
    pages, parser = extractor.extract(pdf, tmp_path / "cache", "es", lines.append)
    assert pages == [(1, "ocr 1")] and parser == "opendataloader+ocr-fake"
    assert lines == ["🔍 OCR (Fake) en 1 páginas de scan.pdf"]


# ── engine vision ────────────────────────────────────────────────────────────

class FakeClient:
    """A simulated OpenAI client: records each request, answers from a script."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


def test_vision_sends_each_page_as_a_png_image_and_returns_the_text(tmp_path):
    pdf = scanned_pdf(tmp_path / "scan.pdf", ["uno", "dos", "tres"])
    client = FakeClient(["texto 1", "texto 3"])
    engine = VisionEngine("http://x/v1", "key", "vision-model", client=client)
    result = engine.run(pdf, [1, 3], "es", cache_dir=tmp_path)
    assert result == [(1, "texto 1"), (3, "texto 3")]
    assert len(client.requests) == 2
    request = client.requests[0]
    assert request["model"] == "vision-model"
    content = request["messages"][0]["content"]
    assert content[0]["type"] == "text" and "Español" in content[0]["text"] and "only" in content[0]["text"]
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]).startswith(b"\x89PNG")


def test_vision_strips_a_markdown_fence_the_model_adds(tmp_path):
    pdf = scanned_pdf(tmp_path / "scan.pdf", ["uno"])
    client = FakeClient(["```markdown\n# Título\n\nCuerpo\n```"])
    result = VisionEngine("u", "k", "m", client=client).run(pdf, [1], "es", cache_dir=tmp_path)
    assert result == [(1, "# Título\n\nCuerpo")]


@pytest.mark.parametrize("text, expected", [
    ("```\nuno\n```", "uno"), ("sin cerco", "sin cerco"), ("  texto  ", "texto"),
    ("antes\n```\ncódigo\n```\ndespués", "antes\n```\ncódigo\n```\ndespués"),
])
def test_strip_fence(text, expected):
    assert strip_fence(text) == expected


def test_one_failing_page_is_reported_and_the_rest_continue(tmp_path):
    pdf = scanned_pdf(tmp_path / "scan.pdf", ["uno", "dos", "tres"])
    client = FakeClient(["texto 1", RuntimeError("rate limited"), "texto 3"])
    lines = []
    result = VisionEngine("u", "k", "m", client=client).run(pdf, [1, 2, 3], "es", cache_dir=tmp_path,
                                                          progress=lines.append)
    assert result == [(1, "texto 1"), (2, ""), (3, "texto 3")]
    assert len(lines) == 1 and "página 2" in lines[0] and "rate limited" in lines[0]


def test_vision_fails_when_every_page_fails(tmp_path):
    pdf = scanned_pdf(tmp_path / "scan.pdf", ["uno"])
    with pytest.raises(RuntimeError, match="every page"):
        VisionEngine("u", "k", "m", client=FakeClient([RuntimeError("down")])).run(pdf, [1], "es", cache_dir=tmp_path)


def test_vision_settings_fall_back_to_the_llm_values():
    s = settings(OCR_ENGINE="vision", LLM_BASE_URL="http://llm/v1", LLM_API_KEY="k", LLM_MODEL="chat")
    assert ocr.vision_config(s) == ("http://llm/v1", "k", "chat")
    s = settings(OCR_ENGINE="vision", OCR_MODEL="vision", OCR_BASE_URL="http://ocr/v1",
                 LLM_BASE_URL="http://llm/v1", LLM_API_KEY="k", LLM_MODEL="chat")
    assert ocr.vision_config(s) == ("http://ocr/v1", "k", "vision")
    engine = ocr.get_engine(s)
    assert isinstance(engine, VisionEngine) and (engine.base_url, engine.model) == ("http://ocr/v1", "vision")


def test_vision_settings_follow_the_chain_ocr_vision_ingestion_chat():
    chat = dict(OCR_ENGINE="vision", LLM_BASE_URL="http://llm/v1", LLM_API_KEY="k-llm", LLM_MODEL="chat")
    ingestion = dict(WIKI_LLM_BASE_URL="http://wiki/v1", WIKI_LLM_API_KEY="k-wiki", WIKI_LLM_MODEL="wiki")
    vision = dict(VISION_BASE_URL="http://vision/v1", VISION_API_KEY="k-vision", VISION_MODEL="vision")
    own = dict(OCR_BASE_URL="http://ocr/v1", OCR_API_KEY="k-ocr", OCR_MODEL="ocr")
    assert ocr.vision_config(settings(**chat, **ingestion)) == ("http://wiki/v1", "k-wiki", "wiki")
    assert ocr.vision_config(settings(**chat, **ingestion, **vision)) == ("http://vision/v1", "k-vision", "vision")
    assert ocr.vision_config(settings(**chat, **ingestion, **vision, **own)) == ("http://ocr/v1", "k-ocr", "ocr")
    # Each value falls back on its own: a blank VISION_MODEL takes the ingestion model.
    mixed = settings(**chat, **ingestion, **{**vision, "VISION_MODEL": ""}, OCR_MODEL="ocr")
    assert ocr.vision_config(mixed) == ("http://vision/v1", "k-vision", "ocr")
    mixed = settings(**chat, **{**vision, "VISION_MODEL": " "})
    assert ocr.vision_config(mixed) == ("http://vision/v1", "k-vision", "chat")


def test_the_engine_follows_config_settings_after_apply_values(monkeypatch):
    import config

    for key in ("OCR_ENGINE", "OCR_BASE_URL", "OCR_API_KEY", "OCR_MODEL", "VISION_BASE_URL",
                "VISION_API_KEY", "VISION_MODEL"):
        monkeypatch.setattr(config.settings, key, "")   # restored by monkeypatch after the test
    config.apply_values({"OCR_ENGINE": "vision", "VISION_BASE_URL": "http://vision/v1",
                         "VISION_API_KEY": "k-vision", "VISION_MODEL": "vision", "NOT_A_FIELD": "x"})
    engine = ocr.get_engine()
    assert isinstance(engine, VisionEngine) and (engine.base_url, engine.model) == ("http://vision/v1", "vision")
    assert engine_status("es")["label"] == "modelo externo (vision)"
    assert not hasattr(config.settings, "NOT_A_FIELD")


def test_vision_without_settings_names_the_missing_ones():
    with pytest.raises(ocr.OcrEngineUnavailableError, match="OCR_API_KEY.*OCR_MODEL"):
        ocr.get_engine(settings(OCR_ENGINE="vision", LLM_BASE_URL="http://llm/v1"))


# ── status ───────────────────────────────────────────────────────────────────

def test_status_for_none():
    assert engine_status("es", settings()) == {"engine": "none", "label": "no", "usable": True, "problem": ""}


def _installed(monkeypatch, *, programs=("ocrmypdf", "tesseract"), packs=("eng", "osd", "spa")):
    monkeypatch.setattr(ocr.shutil, "which", lambda name: f"/usr/bin/{name}" if name in programs else None)
    listing = "List of available languages (3):\n" + "\n".join(packs) + "\n"
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: SimpleNamespace(stdout=listing, returncode=0))


def test_status_for_tesseract_when_everything_is_installed(monkeypatch):
    _installed(monkeypatch)
    status = engine_status("es", settings(OCR_ENGINE="tesseract"))
    assert status["usable"] and status["label"] == "Tesseract" and status["problem"] == ""


def test_status_for_tesseract_names_the_missing_program(monkeypatch):
    _installed(monkeypatch, programs=("tesseract",))
    status = engine_status("es", settings(OCR_ENGINE="tesseract"))
    assert not status["usable"] and "ocrmypdf" in status["problem"] and "apt-get" in status["problem"]


def test_status_for_tesseract_names_the_missing_language_pack_of_the_wiki_language(monkeypatch):
    _installed(monkeypatch, packs=("eng", "osd"))
    assert not engine_status("es", settings(OCR_ENGINE="tesseract"))["usable"]
    assert "tesseract-ocr-spa" in engine_status("es", settings(OCR_ENGINE="tesseract"))["problem"]
    assert engine_status("en", settings(OCR_ENGINE="tesseract"))["usable"]


def test_status_for_vision_is_usable_with_the_fallback_and_never_prints_the_key():
    s = settings(OCR_ENGINE="vision", OCR_MODEL="gpt-v", LLM_BASE_URL="http://llm/v1", LLM_API_KEY="SECRET-KEY")
    status = engine_status("es", s)
    assert status["usable"] and status["label"] == "modelo externo (gpt-v)"
    assert "SECRET-KEY" not in repr(status)


def test_status_for_vision_names_the_missing_settings():
    status = engine_status("es", settings(OCR_ENGINE="vision"))
    assert not status["usable"]
    assert all(name in status["problem"] for name in ("OCR_BASE_URL", "OCR_API_KEY", "OCR_MODEL"))


def test_tool_status_carries_the_ocr_status(monkeypatch):
    from services import ingest

    monkeypatch.setattr(ocr, "engine_status", lambda language="en", settings=None: {"engine": language})
    assert ingest.tool_status("es")["ocr"] == {"engine": "es"}


# ── the real local engine ────────────────────────────────────────────────────

def _real_tesseract_available() -> bool:
    if not (shutil.which("ocrmypdf") and shutil.which("tesseract")):
        return False
    # A broken install (a Python package the script cannot import) counts as missing.
    if subprocess.run(["ocrmypdf", "--version"], capture_output=True).returncode != 0:
        return False
    return "spa" in ocr._tesseract_languages(shutil.which("tesseract"))


@pytest.mark.skipif(not _real_tesseract_available(), reason="ocrmypdf or tesseract (with the spa pack) is missing")
@pytest.mark.skipif(not extractor.check_java(), reason="the PDF extractor needs a Java runtime")
def test_real_tesseract_reads_a_known_spanish_word_from_a_scanned_pdf(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config.settings, "OCR_ENGINE", "tesseract")
    pdf = scanned_pdf(tmp_path / "scan.pdf", [SPANISH_TEXT])
    pages, parser = extractor.extract(pdf, tmp_path / "cache", "es")
    assert parser == "opendataloader+ocr-tesseract"
    assert "ciudad" in pages[0][1].lower()
    assert (tmp_path / "cache" / "ocr.pdf").is_file()


# ── the pipeline ─────────────────────────────────────────────────────────────

def _scanned_source(tmp_workspace, monkeypatch):
    monkeypatch.setattr(extractor, "check_java", lambda: "/usr/bin/java")
    monkeypatch.setattr(extractor, "extract_pdf", lambda path, images_dir=None: [(1, ""), (2, "")])  # a scan has no text layer
    return scanned_pdf(tmp_workspace.workspace / "sources" / "Escaneo.pdf", ["uno", "dos"])


def test_a_scanned_pdf_fails_with_the_clear_message_when_ocr_is_off(tmp_workspace, monkeypatch):
    from domain.ingestion.pipeline import ingest_file

    pdf = _scanned_source(tmp_workspace, monkeypatch)
    monkeypatch.setattr(ocr, "get_engine", lambda: None)
    lines = []
    result = ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace, tmp_workspace.llm, "fake",
                         progress_cb=lines.append)
    assert result.status == "failed" and "OCR_ENGINE" in result.message
    assert any(line.startswith("❌ Failed: Escaneo.pdf") and "OCR_ENGINE" in line for line in lines)


def test_a_scanned_pdf_is_ingested_through_the_engine_and_the_console_says_so(tmp_workspace, monkeypatch):
    import json

    from domain.ingestion.pipeline import ingest_file

    pdf = _scanned_source(tmp_workspace, monkeypatch)
    monkeypatch.setattr(ocr, "get_engine", lambda: FakeEngine())
    tmp_workspace.llm.responses = [
        json.dumps({"document_summary": "Un documento escaneado de prueba.", "concepts": []}),
        "# Resumen general\n\nUn documento escaneado de prueba.\n",
    ]
    lines = []
    result = ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace, tmp_workspace.llm, "fake",
                         progress_cb=lines.append, language="es")
    assert result.status == "ingested", result.message
    assert "🔍 OCR (Fake) en 2 páginas de Escaneo.pdf" in lines


def test_a_failed_ingestion_is_tried_again_when_the_setup_changes(tmp_workspace, monkeypatch):
    """The user sets OCR_ENGINE after the failure and ingests the same file again."""
    import json

    from domain.ingestion.pipeline import ingest_file

    pdf = _scanned_source(tmp_workspace, monkeypatch)
    args = (pdf, tmp_workspace.db_path, tmp_workspace.workspace, tmp_workspace.llm, "fake")
    monkeypatch.setattr(ocr, "get_engine", lambda: None)
    assert ingest_file(*args).status == "failed"

    monkeypatch.setattr(ocr, "get_engine", lambda: FakeEngine())
    tmp_workspace.llm.responses = [
        json.dumps({"document_summary": "Un documento escaneado de prueba.", "concepts": []}),
        "# Resumen general\n\nUn documento escaneado de prueba.\n",
    ]
    assert ingest_file(*args).status == "ingested"
    assert ingest_file(*args).status == "skipped"  # a success is not repeated
