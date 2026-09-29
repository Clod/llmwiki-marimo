"""Lexical answer-vs-source verification (Tier 2 of the hybrid pre-retrieval).

When an answer is grounded only on a RAW source chunk (not a curated wiki page),
confirm it actually draws on that chunk before showing it — otherwise a
tangential chunk (a bonds doc that merely name-drops "CEDEARs") can be used as a
fig leaf to leak general knowledge.

`coverage(answer, source)` = fraction of the answer's content words that also
appear in the source. `is_supported` compares it to a LENIENT threshold: we only
reject the clearly off-source, so a faithful paraphrase (which still reuses much
of the domain vocabulary) is never false-rejected. Deterministic, no LLM.

Words are compared by Snowball stem in the wiki's language, after the stop
words of that language are removed. Known limitation: single-word coverage — a
heavy paraphrase in synonyms scores lower (mitigated by leniency), and shared generic finance words
inflate it (a future refinement is n-gram/phrase overlap). Kept simple on
purpose; tune `min_coverage` or add n-grams if live behavior needs it.
"""

from __future__ import annotations

import re
import unicodedata

def _tokens(text: str, language: str) -> list[str]:
    """Content words of `text`, stemmed: stop words removed first, then stemmed.

    The order matters: the stop-word sets hold unstemmed words
    (design_stemming.md, section 4.6).
    """
    from domain.text.stemming import stem_text
    from domain.text.stopwords import stopwords

    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    stop = {_strip(w) for w in stopwords(language)}
    words = [w for w in re.findall(r"[a-z0-9]+", stripped) if len(w) > 1 and w not in stop]
    return stem_text(" ".join(words), language).split()


def _strip(word: str) -> str:
    decomposed = unicodedata.normalize("NFKD", word)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def coverage(answer: str, source: str, language: str = "es") -> float:
    """Fraction of the answer's content words present in the source (0.0–1.0).

    Empty answer → 1.0 (nothing to support; don't reject via overlap)."""
    answer_words = set(_tokens(answer, language))
    if not answer_words:
        return 1.0
    source_words = set(_tokens(source, language))
    return len(answer_words & source_words) / len(answer_words)


def is_supported(
    answer: str, source: str, *, min_coverage: float = 0.2, language: str = "es",
) -> bool:
    """True if the answer's coverage of the source meets the (lenient) threshold."""
    return coverage(answer, source, language) >= min_coverage
