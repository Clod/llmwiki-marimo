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
    FALSE synonyms (e.g. CEDEAR ≠ acción). NOT WIRED — see its own docstring.

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


_ADVISORY_CUES = (
    "alternativ", "invert", "invier", "conviene", "recomend", "rendimiento",
    "cuanto gan", "cuanto rind", "cuanto me da", "donde pong", "que hago con",
    "colocar", "en que pongo",
)
_MONEY_RE = re.compile(
    r"\$\s*\d|\b\d[\d.,]*\s*(?:mil|millon|millones|palos?|lucas?|pesos?|dolares?|usd)\b"
)
_HORIZON_RE = re.compile(
    r"\b\d+\s*(?:dia|dias|mes|meses|ano|anos|semana|semanas|trimestre|trimestres)\b"
)


def advisory_intent(question: str) -> bool:
    """True if the question is an investment-advisory request even without naming
    a specific instrument (e.g. "tengo $1M por 3 meses, ¿qué alternativas tengo?").

    The gate routes a data question to the tools by NAMED data term
    (`mentions_known_data`); a generic advisory question names none, so it would
    otherwise refuse. We recognise it by an advisory cue (alternativas / invertir
    / cuánto ganaría / …) TOGETHER WITH a money amount or a time horizon — kept
    conservative so an ordinary question that merely contains a number doesn't
    route to the (amount-hungry) advisory tool.
    """
    q = _normalize(question)
    if not any(cue in q for cue in _ADVISORY_CUES):
        return False
    return bool(_MONEY_RE.search(q) or _HORIZON_RE.search(q))


_COLLECTION_CUES = (
    # English — the question is about the corpus as a whole, not one subject.
    "this wiki", "the wiki", "each ", "all the ", "every ",
    "what is in", "what's in", "how many", "in common", "list the",
    # Spanish — same shape, same conservatism.
    "este wiki", "esta wiki", "el wiki", "cada ", "todos los", "todas las",
    "que hay en", "cuantos", "en comun", "comparten",
)
_SHARE_RE = re.compile(r"\bshares?\b")
_SUMMARIZE_EACH_RE = re.compile(r"\bsummari[sz]e?\b.*\b(each|all)\b")
_COMPARE_COLLECTIVE_RE = re.compile(
    r"\bcompar\w*\b.*\b(each|all|both|tales?|stories|todos|todas|cuentos|historias)\b"
)


def collection_intent(question: str) -> bool:
    """True if the question is about the WIKI AS A WHOLE rather than about any
    one subject — "what tales are in this wiki?", "compare how each story
    ends" — as opposed to a question that names a subject the roster can cover
    ("what is a glass slipper?").

    This exists because the roster (`in_roster`, built from concept-page and
    dataset-term names) can only ever say yes to a question that NAMES an item;
    a question about the collection names none, so by construction the roster
    can never cover it, and the gate would refuse a newcomer's most natural
    question ("what's in this wiki?") even though the wiki obviously has an
    overview/index page that answers it. This function is the second, narrower
    door for exactly that shape of question.

    Kept as conservative as `advisory_intent`: a cue must name the collective
    ("each", "all the", "this/the wiki", "share"/"comparten", "in common", …),
    not just contain a number or a plural. It is better to miss a borderline
    collection question and fall back to today's refusal than to fire on an
    ordinary single-subject question and inject the overview where a concept
    page was wanted.
    """
    q = _normalize(question)
    if any(cue in q for cue in _COLLECTION_CUES):
        return True
    return bool(
        _SHARE_RE.search(q) or _SUMMARIZE_EACH_RE.search(q) or _COMPARE_COLLECTIVE_RE.search(q)
    )


def drop_false_synonyms(
    question: str, proposals: Iterable[str], false_synonyms: Mapping[str, Iterable[str]]
) -> list[str]:
    """Filter `proposals` (the model's synonym suggestions), dropping any that a
    term present in the question forbids (e.g. question mentions "cedear" and
    `false_synonyms["cedear"]` lists "accion").

    **NOT WIRED — kept deliberately.** Nothing in the production path calls this;
    its only caller is `test_chat_scope.py`. It was written in 6e95b8f alongside
    the rest of the scope gate, for a query-time "synonym rescue" step (widen a
    question's terms when the first search comes back empty, then ask again)
    that was never built. Retained because the step is still a plausible
    addition and this is the piece that would keep it safe: without it, a rescue
    pass would happily bridge `cedear` → `accion` and reintroduce exactly the
    leak the roster gate closes.

    `[falsos_sinonimos]` itself IS live, via a different route:
    `vocabulary.merge_aliases` uses it as a delete filter when the generated and
    hand-written alias maps are merged. So the config section does real work
    today; this particular consumer of it does not.

    If a synonym-rescue step is ever added, call this on the model's proposals
    before searching with them. If it is decided against, delete this function
    and its tests rather than leaving it to rot.
    """
    q = _normalize(question)
    forbidden: set[str] = set()
    for term, bad_targets in false_synonyms.items():
        if _mentions(q, term):
            forbidden.update(_normalize(t) for t in bad_targets)
    return [p for p in proposals if _normalize(p) not in forbidden]
