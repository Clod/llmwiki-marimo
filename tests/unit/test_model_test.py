"""The model tests of domain/modeltest/ and services/model_test.py, with no real model.

The chat turn, the vision engine and the vision call are replaced. The ingestion runs
the real pipeline with `FakeLLMClient`.
"""

import json
from pathlib import Path

import httpx
import openai
import pypdfium2
import pytest

from domain.ingestion import describe, ocr
from domain.modeltest import material, runner
from domain.modeltest.material import ASSETS
from domain.modeltest.report import Check, Report
from services import chat as chat_service
from services import model_test
from tests.helpers.fake_llm import FakeLLMClient

# The text of the Spanish pattern page, as `scripts/build_model_test_pattern.py` writes it.
_PATRON_ES_TEXT = """Acta de recepción del depósito Aldebarán

El depósito Aldebarán recibió el lote ZK-4471 el 14 de marzo de 2031. La encargada, Marisol Quintanilla Erbes, \
registró 3.280 cajas en buen estado y 17 cajas con daños. El valor declarado del lote es de $ 1.946.500,75 y el \
seguro cubre hasta $ 2.300.000.

Sector | Cajas | Peso (kg)
Norte | 1.120 | 8.964
Sur | 1.475 | 11.802
Anexo | 685 | 5.471

El próximo control está previsto para el 9 de abril de 2031.
"""

_VALUES = ("http://service.invalid/v1", "key", "some/model")


# ── Material ──────────────────────────────────────────────────────────────────

def test_material_es():
    m = material("es")
    assert [p.slug for p in m.pages] == ["deposito-aldebaran", "lote-de-mercaderia"]
    assert m.fact == "ZK-4471" and m.fact in m.pages[0].content
    assert m.fact not in m.pages[1].content
    assert m.answerable == "¿Cuál es el código interno del depósito Aldebarán?"
    assert m.unanswerable == "¿De qué color es el depósito Aldebarán?"
    assert m.source_name == "control-de-temperatura.md" and "cámara Berilo" in m.source_text
    assert m.pattern == "patron_es"


def test_material_en():
    m = material("en")
    assert [p.slug for p in m.pages] == ["aldebaran-warehouse", "batch-of-goods"]
    assert m.fact == "ZK-4471" and m.fact in m.pages[0].content
    assert m.answerable == "What is the internal code of the Aldebaran warehouse?"
    assert m.unanswerable == "What colour is the Aldebaran warehouse?"
    assert m.source_name == "temperature-control.md" and "Berilo chamber" in m.source_text
    assert m.pattern == "pattern_en"


def test_material_of_another_language_is_the_english_one():
    assert material("it") is material("en")
    assert material(None) is material("en")


# ── Temporary wiki ────────────────────────────────────────────────────────────

def _last_line(page):
    """The last line of the body of `page`, which the written page keeps."""
    return page.content.strip().splitlines()[-1]


@pytest.mark.parametrize("language", ["es", "en"])
def test_temporary_wiki_holds_the_two_pages_and_is_deleted(language):
    with runner.temporary_wiki(language) as wiki:
        root = wiki.path
        for page in material(language).pages:
            text = (wiki.wiki_dir / "concepts" / f"{page.slug}.md").read_text(encoding="utf-8")
            assert page.title in text and _last_line(page) in text
        assert wiki.language == language
        assert root.is_dir()
    assert not root.exists()
    assert not root.parent.exists()


# ── Reading ───────────────────────────────────────────────────────────────────

def _fake_chat(monkeypatch, turns):
    """Replace the agents and the chat turn. `turns` maps (question, mode) to
    (answer, nodes), or to an exception the turn raises."""
    calls = []
    monkeypatch.setattr(chat_service, "build_agents", lambda wiki, base_url, api_key, model: object())

    async def turn(wiki, agents, messages, *, mode, **_):
        message = messages[-1]
        calls.append((message.role, message.content, tuple(message.context), mode))
        outcome = turns[(message.content, mode)]
        if isinstance(outcome, Exception):
            raise outcome
        answer, nodes = outcome
        return chat_service.ChatResult(answer, path=tuple({"node": n} for n in nodes))

    monkeypatch.setattr(chat_service, "chat_turn_with_context", turn)
    return calls


def _turns(m, *, answers=None, declines=None, uses_tools=None):
    cited = f"The code is {m.fact} (/wiki/concepts/{m.pages[0].slug}.md)."
    return {
        (m.answerable, chat_service.PRE_RETRIEVAL): answers or (cited, ["C1", "C2", "Q13", "Q20", "C3", "Q21"]),
        (m.unanswerable, chat_service.PRE_RETRIEVAL): declines or ("No answer.", ["C1", "C2", "Q13", "Q20", "R5"]),
        (m.answerable, chat_service.STRICT): uses_tools or (
            cited, ["C1", "C2", "T1", "K1", "K2", "K3", "T2", "T4", "C3", "T5"]),
    }


def test_reading_passes_the_three_checks(monkeypatch):
    m = material("es")
    calls = _fake_chat(monkeypatch, _turns(m))
    seen = []
    report = runner.test_reading(*_VALUES, language="es", progress=seen.append)
    assert report.kind == "reading" and report.model == "some/model"
    assert [c.key for c in report.checks] == ["answers", "declines", "uses_tools"]
    assert report.passed and report.could_run
    assert [c.key for c in seen] == ["answers", "declines", "uses_tools"]
    assert calls == [
        ("user", m.answerable, (), chat_service.PRE_RETRIEVAL),
        ("user", m.unanswerable, (), chat_service.PRE_RETRIEVAL),
        ("user", m.answerable, (), chat_service.STRICT),
    ]


def test_reading_fails_when_the_path_ends_elsewhere(monkeypatch):
    m = material("en")
    _fake_chat(monkeypatch, _turns(m, declines=("It is red.", ["C1", "C2", "Q13", "Q20", "C3", "Q21"])))
    report = runner.test_reading(*_VALUES)
    declines = report.checks[1]
    assert not declines.passed and declines.ran
    assert "ends in Q21, not in R5" in declines.detail
    assert not report.passed and report.could_run


def test_reading_fails_when_the_answer_lacks_the_fact(monkeypatch):
    m = material("en")
    nodes = ["C1", "C2", "Q13", "Q20", "C3", "Q21"]
    _fake_chat(monkeypatch, _turns(m, answers=(f"See /wiki/concepts/{m.pages[0].slug}.md.", nodes)))
    answers = runner.test_reading(*_VALUES).checks[0]
    assert not answers.passed and answers.ran
    assert "does not hold ZK-4471" in answers.detail


def test_reading_fails_when_the_answer_does_not_cite_the_page(monkeypatch):
    m = material("en")
    nodes = ["C1", "C2", "Q13", "Q20", "C3", "Q21"]
    _fake_chat(monkeypatch, _turns(m, answers=("The code is ZK-4471.", nodes)))
    answers = runner.test_reading(*_VALUES).checks[0]
    assert not answers.passed
    assert "does not cite aldebaran-warehouse" in answers.detail


def test_reading_fails_without_a_k_node(monkeypatch):
    m = material("en")
    _fake_chat(monkeypatch, _turns(m, uses_tools=("The code is ZK-4471.", ["C1", "C2", "T1", "T2", "C3", "T5"])))
    uses_tools = runner.test_reading(*_VALUES).checks[2]
    assert not uses_tools.passed and uses_tools.ran
    assert "no K node" in uses_tools.detail


def test_reading_check_that_raises_did_not_run(monkeypatch):
    m = material("en")
    error = RuntimeError("status_code: 404, model_name: some/model, body: not found")
    _fake_chat(monkeypatch, {key: error for key in _turns(m)})
    report = runner.test_reading(*_VALUES)
    assert [c.ran for c in report.checks] == [False, False, False]
    assert all(not c.passed for c in report.checks)
    assert "404" in report.checks[0].detail
    assert not report.could_run and not report.passed


# ── Ingestion ─────────────────────────────────────────────────────────────────

def _ingestion_llm(m):
    extraction = json.dumps({
        "document_summary": "The Berilo chamber keeps the batches cold.",
        "concepts": [{"name": "Berilo chamber", "category": "entity",
                      "insight": "The cold chamber of the Aldebaran warehouse"}],
    })
    concept = f"# Berilo chamber\n\nThe cold chamber of the warehouse.\n\n## Sources\n- {m.source_name}\n"
    return FakeLLMClient(responses=[extraction, concept, "# Overview\n\nOne chamber.\n"])


def test_ingestion_passes_with_one_concept(monkeypatch):
    m = material("en")
    fake = _ingestion_llm(m)
    monkeypatch.setattr(runner, "OpenAI", lambda **_: fake)
    seen = []
    report = runner.test_ingestion(*_VALUES, progress=seen.append)
    assert report.kind == "ingestion"
    assert [c.key for c in report.checks] == ["ingests"]
    check = report.checks[0]
    assert check.passed, check.detail
    assert "1 new concept page" in check.detail
    assert seen == [check]
    assert fake.calls and fake.calls[0]["model"] == "some/model"


def test_ingestion_fails_when_the_status_is_failed(monkeypatch):
    class Broken(FakeLLMClient):
        def next_response(self):
            raise ValueError("the model answered nothing usable")

    monkeypatch.setattr(runner, "OpenAI", lambda **_: Broken())
    check = runner.test_ingestion(*_VALUES).checks[0]
    assert not check.passed and check.ran
    assert "ended as failed" in check.detail


def test_ingestion_without_an_answer_of_the_service_did_not_run(monkeypatch):
    class Unreachable(FakeLLMClient):
        def next_response(self):
            raise openai.APIConnectionError(request=httpx.Request("POST", "http://service.invalid/v1"))

    monkeypatch.setattr(runner, "OpenAI", lambda **_: Unreachable())
    report = runner.test_ingestion(*_VALUES)
    assert not report.checks[0].ran and not report.could_run
    assert "Connection error" in report.checks[0].detail


# ── Vision ────────────────────────────────────────────────────────────────────

_ANSWER = {"type": "document", "decorative": False, "description": "A reception record.",
           "visible_text": [], "values": []}


def _fake_vision(monkeypatch, text=_PATRON_ES_TEXT, describe_error=None):
    calls = {}

    def run(self, pdf_path, pages, language, *, cache_dir, progress=None):
        calls["run"] = (self.model, Path(pdf_path).name, list(pages), language)
        return [(1, text)]

    def call_vision(vision, image, language):
        calls["describe"] = (vision.model, image.suffix, image.stat().st_size > 0, language)
        if describe_error is not None:
            raise describe_error
        return dict(_ANSWER), 10, 5

    monkeypatch.setattr(ocr.VisionEngine, "run", run)
    monkeypatch.setattr(describe, "call_vision", call_vision)
    return calls


def test_vision_passes_with_the_text_of_the_page(monkeypatch):
    calls = _fake_vision(monkeypatch)
    seen = []
    report = runner.test_vision(*_VALUES, language="es", progress=seen.append)
    assert report.kind == "vision"
    assert [c.key for c in report.checks] == ["reads_page", "describes"]
    assert report.passed, report.checks
    assert "14 data" in report.checks[0].detail
    assert [c.key for c in seen] == ["reads_page", "describes"]
    assert calls["run"] == ("some/model", "patron_es.pdf", [1], "es")
    assert calls["describe"] == ("some/model", ".png", True, "es")


def test_vision_fails_when_a_datum_is_missing(monkeypatch):
    _fake_vision(monkeypatch, text=_PATRON_ES_TEXT.replace("11.802", "11.302"))
    reads = runner.test_vision(*_VALUES, language="es").checks[0]
    assert not reads.passed and reads.ran
    assert "1 of the 14" in reads.detail and "11.802" in reads.detail


def test_vision_fails_with_a_bad_answer(monkeypatch):
    _fake_vision(monkeypatch, describe_error=describe.BadAnswer("the answer is not JSON"))
    describes = runner.test_vision(*_VALUES, language="es").checks[1]
    assert not describes.passed and describes.ran
    assert "not JSON" in describes.detail


def test_vision_without_an_answer_of_the_service_did_not_run(monkeypatch):
    error = openai.APIConnectionError(request=httpx.Request("POST", "http://service.invalid/v1"))
    _fake_vision(monkeypatch, describe_error=error)
    describes = runner.test_vision(*_VALUES, language="es").checks[1]
    assert not describes.ran and not describes.passed


def test_vision_reading_failure_reports_the_error_of_the_service(monkeypatch):
    # The real engine: every page fails, and the engine raises its own RuntimeError.
    def create(**_):
        raise openai.APIConnectionError(request=httpx.Request("POST", "http://service.invalid/v1"))

    class Unreachable:
        chat = type("Chat", (), {"completions": type("Completions", (), {"create": staticmethod(create)})})

    monkeypatch.setattr(runner, "OpenAI", lambda **_: Unreachable())
    monkeypatch.setattr(describe, "call_vision", lambda vision, image, language: (dict(_ANSWER), 0, 0))
    reads = runner.test_vision(*_VALUES).checks[0]
    assert not reads.ran
    assert "Connection error" in reads.detail


# ── The pattern PDF ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["patron_es", "pattern_en"])
def test_pattern_pdf_is_one_page_with_no_text_layer(name):
    document = pypdfium2.PdfDocument(str(ASSETS / f"{name}.pdf"))
    try:
        assert len(document) == 1
        textpage = document[0].get_textpage()
        assert textpage.count_chars() == 0
    finally:
        document.close()
    data = json.loads((ASSETS / f"{name}.json").read_text(encoding="utf-8"))["data"]
    assert len(data) == 14
    assert all(isinstance(d, str) and d.strip() for d in data)


def test_spanish_pattern_text_holds_its_data():
    data = json.loads((ASSETS / "patron_es.json").read_text(encoding="utf-8"))["data"]
    assert all(d in _PATRON_ES_TEXT for d in data)


# ── Report and service ────────────────────────────────────────────────────────

def test_report_passed_and_could_run():
    ok, bad, idle = Check("a", True, ""), Check("b", False, ""), Check("c", False, "", ran=False)
    assert Report("reading", "m", (ok, ok), 1.0).passed
    assert not Report("reading", "m", (ok, bad), 1.0).passed
    assert Report("reading", "m", (bad, idle), 1.0).could_run
    assert not Report("reading", "m", (idle, idle), 1.0).could_run
    assert not Report("reading", "m", (), 1.0).passed


def test_service_runs_the_test_of_each_kind(monkeypatch):
    seen = []
    for kind in model_test.KINDS:
        def fake(base_url, api_key, model, language, progress, _kind=kind):
            seen.append((_kind, base_url, api_key, model, language, progress))
            return Report(_kind, model, (), 0.0)
        monkeypatch.setitem(model_test._TESTS, kind, fake)
    for kind in model_test.KINDS:
        assert model_test.run(kind, *_VALUES, language="es").kind == kind
    assert [s[0] for s in seen] == ["reading", "ingestion", "vision"]
    assert seen[0][1:5] == (*_VALUES, "es")
    with pytest.raises(ValueError):
        model_test.run("hearing", *_VALUES)


def test_fingerprint_changes_with_each_value():
    base = model_test.fingerprint(*_VALUES)
    assert base == model_test.fingerprint(*_VALUES)
    assert len(base) == 64
    assert base != model_test.fingerprint("http://other.invalid/v1", "key", "some/model")
    assert base != model_test.fingerprint(*_VALUES[:2], "other/model")
    assert base != model_test.fingerprint(_VALUES[0], "other", _VALUES[2])
    # The separator keeps the values apart: "ab"+"c" is not "a"+"bc".
    assert model_test.fingerprint("ab", "c", "m") != model_test.fingerprint("a", "bc", "m")
