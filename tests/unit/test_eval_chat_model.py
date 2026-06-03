"""Tests for the chat-model eval graders (scripts/eval_chat_model.py).

These are the pure scoring functions behind `scripts/eval_chat_model.py`. They
decide PASS/FAIL from an answer string, so they must reliably (a) spot a leaked
off-corpus answer and (b) detect both wiki-page and source-PDF citation formats.
"""

import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "eval_chat_model.py"


def _load():
    spec = importlib.util.spec_from_file_location("eval_chat_model", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_eval = _load()


def test_off_corpus_leak_detected() -> None:
    assert _eval.answered_off_corpus("The capital of France is Paris.") is True
    assert _eval.answered_off_corpus("PARIS is the capital.") is True  # case-insensitive


def test_off_corpus_refusal_passes() -> None:
    assert _eval.answered_off_corpus("I couldn't find anything about that in your wiki.") is False


def test_citation_detected_for_wiki_page() -> None:
    assert _eval.has_citation("She flees at midnight (wiki/summaries/cinderella.md).") is True


def test_citation_detected_for_source_pdf() -> None:
    assert _eval.has_citation("The glass slipper (Cinderella.pdf, p. 3) is left behind.") is True


def test_no_citation_when_absent() -> None:
    assert _eval.has_citation("She flees the ball at midnight.") is False


def test_citation_count_is_distinct() -> None:
    text = (
        "a (wiki/summaries/cinderella.md) b (wiki/summaries/snow-white.md) "
        "c (wiki/summaries/cinderella.md)"
    )
    assert _eval.citation_count(text) == 2


def test_synthesis_grader_requires_at_least_one_citation() -> None:
    passed_none, _ = _eval._check_synthesis_cited("Both have a wicked stepmother.")
    assert passed_none is False
    passed_two, _ = _eval._check_synthesis_cited(
        "Stepmother (wiki/summaries/cinderella.md); rescued by a prince "
        "(wiki/summaries/snow-white.md)."
    )
    assert passed_two is True
