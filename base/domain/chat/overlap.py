"""Lexical answer-vs-source verification (Tier 2 of the hybrid pre-retrieval).

When an answer is grounded only on a RAW source chunk (not a curated wiki page),
confirm it actually draws on that chunk before showing it — otherwise a
tangential chunk (a bonds doc that merely name-drops "CEDEARs") can be used as a
fig leaf to leak general knowledge.

`coverage(answer, source)` = fraction of the answer's content words that also
appear in the source. `is_supported` compares it to a LENIENT threshold: we only
reject the clearly off-source, so a faithful paraphrase (which still reuses much
of the domain vocabulary) is never false-rejected. Deterministic, no LLM.

Known limitation: single-word coverage with no stemming — a heavy paraphrase in
synonyms scores lower (mitigated by leniency), and shared generic finance words
inflate it (a future refinement is n-gram/phrase overlap). Kept simple on
purpose; tune `min_coverage` or add n-grams if live behavior needs it.
"""

from __future__ import annotations

import re
import unicodedata

# Compact Spanish stop-word set: function words carry no grounding signal.
_STOPWORDS = frozenset({
    "el", "la", "los", "las", "un", "una", "unos", "unas", "lo", "al", "del",
    "de", "en", "a", "y", "o", "u", "e", "que", "se", "su", "sus", "por", "con",
    "sin", "para", "como", "mas", "pero", "si", "no", "es", "son", "ser", "esta",
    "estan", "este", "esta", "estos", "estas", "ese", "esa", "esos", "esas",
    "entre", "sobre", "cuando", "cuanto", "cual", "hay", "ha", "han", "muy", "ya",
    "le", "les", "me", "te", "nos", "fue", "tiene", "tienen", "puede", "pueden",
    "porque", "tambien", "solo", "cada", "sea", "ni", "o",
})


def _tokens(text: str) -> list[str]:
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    words = re.findall(r"[a-z0-9]+", stripped)
    return [w for w in words if len(w) > 1 and w not in _STOPWORDS]


def coverage(answer: str, source: str) -> float:
    """Fraction of the answer's content words present in the source (0.0–1.0).

    Empty answer → 1.0 (nothing to support; don't reject via overlap)."""
    answer_words = set(_tokens(answer))
    if not answer_words:
        return 1.0
    source_words = set(_tokens(source))
    return len(answer_words & source_words) / len(answer_words)


def is_supported(answer: str, source: str, *, min_coverage: float = 0.2) -> bool:
    """True if the answer's coverage of the source meets the (lenient) threshold."""
    return coverage(answer, source) >= min_coverage
