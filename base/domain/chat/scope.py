"""Deterministic scope gate for the hybrid pre-retrieval.

Pure, config-driven word matching — no LLM, no I/O — used to decide, before (or
instead of) invoking the model:
  - `is_off_limits`: does the question mention a term we know we never cover
    (blacklist / "fuera de alcance")? Checked at the top of the flow so such a
    term is refused immediately, before any document search.
  - `mentions_known_data`: does the question mention data we actually have
    (a dataset category or key, or a whitelisted alias like "billete verde")?
    This is the "chequeo contra la lista de datos".
  - `drop_false_synonyms`: filter a model's proposed synonyms against known
    FALSE synonyms (e.g. CEDEAR ≠ acción), so the synonym-rescue step can't
    bridge two different instruments.

Matching is accent- and case-insensitive, treats `_` as a space, and is by
WHOLE WORD so short keys ("cer", "mep") don't match inside other words. The
lists come from per-wiki config; every function takes them as arguments.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping


def _normalize(text: str) -> str:
    """Lowercase, strip accents, `_`→space, collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped.lower().replace("_", " ")).strip()


def _mentions(question_norm: str, term: str) -> bool:
    """True if `term` appears as a whole word/phrase in the normalized question."""
    normalized_term = _normalize(term)
    if not normalized_term:
        return False
    return re.search(rf"\b{re.escape(normalized_term)}\b", question_norm) is not None


def is_off_limits(question: str, off_limits: Iterable[str]) -> bool:
    """True if the question mentions a blacklisted (never-covered) term."""
    q = _normalize(question)
    return any(_mentions(q, term) for term in off_limits)


def mentions_known_data(
    question: str, vocabulary: Iterable[str], aliases: Iterable[str] = ()
) -> bool:
    """True if the question mentions a known data term (dataset category/key) or
    a whitelisted alias."""
    q = _normalize(question)
    return any(_mentions(q, term) for term in vocabulary) or any(
        _mentions(q, alias) for alias in aliases
    )


def drop_false_synonyms(
    question: str, proposals: Iterable[str], false_synonyms: Mapping[str, Iterable[str]]
) -> list[str]:
    """Filter `proposals` (the model's synonym suggestions), dropping any that a
    term present in the question forbids (e.g. question mentions "cedear" and
    `false_synonyms["cedear"]` lists "accion")."""
    q = _normalize(question)
    forbidden: set[str] = set()
    for term, bad_targets in false_synonyms.items():
        if _mentions(q, term):
            forbidden.update(_normalize(t) for t in bad_targets)
    return [p for p in proposals if _normalize(p) not in forbidden]
