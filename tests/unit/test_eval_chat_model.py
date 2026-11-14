"""Tests of `scripts/eval_chat_model.py`, with `services.model_test.run` replaced.

No test calls a model or reads `.env`: `load_dotenv` is replaced by a function that does
nothing, and `config.settings` by a `Settings` built from explicit values.
"""

import importlib.util
from pathlib import Path

import pytest

from domain.modeltest import Check, Report

_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "eval_chat_model.py"

_LLM = {"LLM_BASE_URL": "http://chat.invalid/v1", "LLM_API_KEY": "chat-key", "LLM_MODEL": "chat/model"}
_BLANK = {"WIKI_LLM_BASE_URL": "", "WIKI_LLM_API_KEY": "", "WIKI_LLM_MODEL": "",
          "VISION_BASE_URL": "", "VISION_API_KEY": "", "VISION_MODEL": "", "OCR_ENGINE": "none"}

# The checks of each kind of test (`domain.modeltest.runner`).
_KEYS = {"reading": ("answers", "declines", "uses_tools"), "ingestion": ("ingests",),
         "vision": ("reads_page", "describes")}


def _load():
    spec = importlib.util.spec_from_file_location("eval_chat_model", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_eval = _load()


class _FakeRun:
    """Replaces `model_test.run`: records each call, reports each check to `progress`, and
    returns a `Report` whose checks have the outcome of `outcome(kind, key)`."""

    def __init__(self, outcome=lambda kind, key: Check(key, True, f"{key} passed")):
        self.outcome = outcome
        self.calls: list[tuple[str, str, str, str, str]] = []

    def __call__(self, kind, base_url, api_key, model, language="en", progress=None):
        self.calls.append((kind, base_url, api_key, model, language))
        checks = tuple(self.outcome(kind, key) for key in _KEYS[kind])
        for check in checks:
            if progress is not None:
                progress(check)
        return Report(kind, model, checks, 1.5)


@pytest.fixture
def setup(monkeypatch):
    """Returns a function that sets the values of `.env` and replaces `model_test.run`."""
    import dotenv

    from services import model_test

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)

    def _setup(values=None, run=None):
        import config

        fake_settings = config.Settings(_env_file=None, **{**_LLM, **_BLANK, **(values or {})})
        monkeypatch.setattr(config, "settings", fake_settings)
        fake_run = run or _FakeRun()
        monkeypatch.setattr(model_test, "run", fake_run)
        return fake_run

    return _setup


# ── Exit codes ────────────────────────────────────────────────────────────────

def test_exit_0_when_every_check_passes(setup, capsys):
    setup()
    assert _eval.main([]) == 0
    out = capsys.readouterr().out
    assert "✓ It answers from the page and cites it" in out
    assert "VERDICT: ✓ the 4 checks passed" in out


def test_exit_1_when_a_check_that_ran_fails(setup, capsys):
    def outcome(kind, key):
        if key == "declines":
            return Check(key, False, "the path ends in Q21, not R5")
        return Check(key, True, "ok")

    setup(run=_FakeRun(outcome))
    assert _eval.main([]) == 1
    out = capsys.readouterr().out
    assert "✗ It declines when the page has no answer: the path ends in Q21, not R5" in out
    assert "✗ The model failed 1 of 3 checks (1.5 s)." in out
    assert "VERDICT: ✗ 1 of 4 checks failed." in out


def test_exit_1_when_a_check_fails_and_another_did_not_run(setup):
    def outcome(kind, key):
        if kind == "ingestion":
            return Check(key, False, "Connection error.", ran=False)
        return Check(key, key != "answers", "detail")

    setup(run=_FakeRun(outcome))
    assert _eval.main([]) == 1


def test_exit_2_when_the_service_gives_no_answer(setup, capsys):
    setup(run=_FakeRun(lambda kind, key: Check(key, False, "Connection error.", ran=False)))
    assert _eval.main([]) == 2
    out = capsys.readouterr().out
    assert "? It answers from the page and cites it: no answer from the service (Connection error.)" in out
    assert "? The tests could not run: the service gave no answer." in out
    assert "VERDICT: ? the tests could not run" in out


def test_exit_2_when_a_check_did_not_run_and_none_failed(setup):
    def outcome(kind, key):
        if kind == "ingestion":
            return Check(key, False, "Connection error.", ran=False)
        return Check(key, True, "ok")

    setup(run=_FakeRun(outcome))
    assert _eval.main([]) == 2


def test_exit_2_when_a_value_is_missing_and_no_test_runs(setup, capsys):
    fake_run = setup({"LLM_MODEL": ""})
    assert _eval.main([]) == 2
    assert fake_run.calls == []
    assert "Model not configured for reading (LLM_*)" in capsys.readouterr().out


# ── Which tests run ───────────────────────────────────────────────────────────

def test_without_model_reading_and_ingestion_with_the_fallback_values(setup):
    fake_run = setup({"WIKI_LLM_MODEL": "ingest/model"})
    _eval.main([])
    assert fake_run.calls == [
        ("reading", "http://chat.invalid/v1", "chat-key", "chat/model", "en"),
        ("ingestion", "http://chat.invalid/v1", "chat-key", "ingest/model", "en"),
    ]


def test_without_model_the_ingestion_test_uses_its_own_values(setup):
    fake_run = setup({"WIKI_LLM_BASE_URL": "http://ingest.invalid/v1", "WIKI_LLM_API_KEY": "ingest-key",
                      "WIKI_LLM_MODEL": "ingest/model"})
    _eval.main([])
    assert fake_run.calls[1] == ("ingestion", "http://ingest.invalid/v1", "ingest-key", "ingest/model", "en")


def test_the_vision_test_runs_when_a_vision_model_is_set(setup):
    fake_run = setup({"WIKI_LLM_MODEL": "ingest/model", "VISION_MODEL": "vision/model"})
    _eval.main([])
    assert [c[0] for c in fake_run.calls] == ["reading", "ingestion", "vision"]
    assert fake_run.calls[2] == ("vision", "http://chat.invalid/v1", "chat-key", "vision/model", "en")


def test_the_vision_test_runs_when_the_ocr_engine_is_vision(setup):
    fake_run = setup({"WIKI_LLM_MODEL": "ingest/model", "OCR_ENGINE": "vision"})
    _eval.main([])
    # No VISION_MODEL: the vision test takes the ingestion model, as `settings.vision()` does.
    assert fake_run.calls[2] == ("vision", "http://chat.invalid/v1", "chat-key", "ingest/model", "en")


def test_the_vision_test_does_not_run_without_a_vision_model_or_engine(setup):
    fake_run = setup({"OCR_ENGINE": "tesseract"})
    _eval.main([])
    assert [c[0] for c in fake_run.calls] == ["reading", "ingestion"]


def test_model_runs_the_reading_test_with_the_service_of_llm(setup):
    fake_run = setup({"WIKI_LLM_MODEL": "ingest/model", "VISION_MODEL": "vision/model"})
    assert _eval.main(["--model", "a/one", "--model", "b/two"]) == 0
    assert fake_run.calls == [
        ("reading", "http://chat.invalid/v1", "chat-key", "a/one", "en"),
        ("reading", "http://chat.invalid/v1", "chat-key", "b/two", "en"),
    ]


def test_language_is_passed_to_the_tests(setup):
    fake_run = setup()
    _eval.main(["--language", "es"])
    assert {c[4] for c in fake_run.calls} == {"es"}


# ── Output ────────────────────────────────────────────────────────────────────

def test_brief_prints_no_detail_for_a_check_that_passed(setup, capsys):
    setup()
    _eval.main(["--brief"])
    brief = capsys.readouterr().out
    _eval.main([])
    full = capsys.readouterr().out
    assert "answers passed" not in brief
    assert "answers passed" in full
    assert brief.count("✓ It answers from the page and cites it") == 1


def test_the_script_does_not_read_the_sample_wiki_of_the_tests():
    source = _MODULE_PATH.read_text(encoding="utf-8")
    assert "tests.helpers" not in source
    assert "golden" not in source
