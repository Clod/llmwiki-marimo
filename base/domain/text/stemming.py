"""The one stemming function of the wiki: Snowball, per wiki language.

Every text that is compared by root goes through `stem_text`: the fragments the
FTS index stores (`document_chunks.content_stemmed`), the questions, the coverage
roster (dataset categories, concept-page titles, aliases) and the Tier-2
verification. Because every side reaches the same string, "plazos fijos" and
"plazo fijo" both become "plaz fij" and match.

`stem_text` lowercases, strips accents, splits into word tokens, stems each
token and joins them with single spaces. Accents are stripped before stemming,
so a question typed without them ("inversion") reaches the same root as the
text that has them ("inversión").

Two lists are NOT stemmed, by design: the blacklist and the dataset keys
(design_stemming.md, decisions 5 and 15). They compare whole normalized words.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

import snowballstemmer

# ISO 639-1 code → Snowball algorithm name. An unknown code falls back to
# English, the same fallback `load_wiki_language` applies to `[wiki].language`.
_ALGORITHMS = {
    "en": "english",
    "es": "spanish",
    "it": "italian",
    "pt": "portuguese",
    "fr": "french",
    "de": "german",
}

# FTS5 operators, which must reach MATCH unstemmed and in upper case.
_FTS_OPERATORS = frozenset({"OR", "AND", "NOT", "NEAR"})

_WORD_RE = re.compile(r"\w+", flags=re.UNICODE)


@lru_cache(maxsize=None)
def _stemmer(language: str) -> snowballstemmer.stemmer:
    """The Snowball stemmer of `language`, created once per language."""
    algorithm = _ALGORITHMS.get((language or "en").lower(), "english")
    return snowballstemmer.stemmer(algorithm)


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _stem_word(word: str, language: str) -> str:
    return _stemmer(language).stemWord(_strip_accents(word.lower()))


def stem_text(text: str, language: str) -> str:
    """Lowercase, strip accents, split into words, stem each, join with spaces.

    `_` counts as a separator, so the dataset category `plazo_fijo` stems like
    the phrase "plazo fijo".
    """
    words = _WORD_RE.findall((text or "").replace("_", " "))
    return " ".join(_stem_word(w, language) for w in words)


def stem_fts_query(expr: str, language: str) -> str:
    """Stem every word of an FTS5 expression and leave its syntax intact.

    Double quotes, parentheses, `*` and the operators OR, AND, NOT and NEAR are
    kept as written; each other word is replaced by its stem. The stems are
    lower case, so a stemmed word can never be read as an operator.
    """
    def _replace(match: re.Match[str]) -> str:
        word = match.group(0)
        if word in _FTS_OPERATORS:
            return word
        return _stem_word(word, language)

    return _WORD_RE.sub(_replace, (expr or "").replace("_", " "))
