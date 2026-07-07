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


# ── Retrieval check: a citation only counts if a tool was actually called ──────

class _Part:
    def __init__(self, kind: str) -> None:
        self.part_kind = kind


class _Msg:
    def __init__(self, *kinds: str) -> None:
        self.parts = [_Part(k) for k in kinds]


class _Result:
    def __init__(self, *messages: _Msg) -> None:
        self._messages = messages

    def all_messages(self) -> tuple[_Msg, ...]:
        return self._messages


def test_made_tool_call_true_when_a_tool_call_part_is_present() -> None:
    result = _Result(_Msg("text"), _Msg("tool-call", "text"))
    assert _eval._made_tool_call(result) is True


def test_made_tool_call_false_when_only_text_parts() -> None:
    # A model that answers (even with citation-shaped text) without any tool call
    # never retrieved — the fabricated-citation case this whole check exists for.
    result = _Result(_Msg("text"), _Msg("text"))
    assert _eval._made_tool_call(result) is False


def test_made_tool_call_defensive_when_introspection_raises() -> None:
    class _Broken:
        def all_messages(self):  # noqa: ANN202
            raise RuntimeError("unexpected pydantic-ai shape")

    assert _eval._made_tool_call(_Broken()) is False


def test_count_tool_calls_counts_across_messages() -> None:
    result = _Result(_Msg("tool-call", "text"), _Msg("text"), _Msg("tool-call"))
    assert _eval._count_tool_calls(result) == 2
    assert _eval._count_tool_calls(_Result(_Msg("text"))) == 0
