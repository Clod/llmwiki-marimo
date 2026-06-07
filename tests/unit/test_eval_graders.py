"""Tests for the pure chat graders (domain.eval.graders).

These are the regex/string signals shared by the chat-model smoke test and the
eval packet's auto-check pre-screen, so they must reliably spot a leaked
off-corpus answer and detect both wiki-page and source-PDF citation formats.
"""

from domain.eval import graders


def test_off_corpus_leak_detected() -> None:
    assert graders.answered_off_corpus("The capital of France is Paris.") is True
    assert graders.answered_off_corpus("PARIS is the capital.") is True


def test_off_corpus_refusal_passes() -> None:
    assert graders.answered_off_corpus("I couldn't find that in your wiki.") is False


def test_citation_detected_for_wiki_page() -> None:
    assert graders.has_citation("She flees at midnight (wiki/summaries/cinderella.md).") is True


def test_citation_detected_for_source_pdf() -> None:
    assert graders.has_citation("The glass slipper (Cinderella.pdf, p. 3) is left.") is True


def test_no_citation_when_absent() -> None:
    assert graders.has_citation("She flees the ball at midnight.") is False


def test_citation_count_is_distinct() -> None:
    text = (
        "a (wiki/summaries/cinderella.md) b (wiki/summaries/snow-white.md) "
        "c (wiki/summaries/cinderella.md)"
    )
    assert graders.citation_count(text) == 2


def test_extract_citations_preserves_first_seen_order_and_dedupes() -> None:
    text = (
        "x (wiki/summaries/snow-white.md) y (Cinderella.pdf, p. 3) "
        "z (wiki/summaries/snow-white.md)"
    )
    assert graders.extract_citations(text) == [
        "wiki/summaries/snow-white.md",
        "Cinderella.pdf, p. 3",
    ]


def test_extract_citations_empty_when_none() -> None:
    assert graders.extract_citations("No citation here at all.") == []
