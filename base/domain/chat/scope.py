"""Deterministic scope gate for the hybrid pre-retrieval.

Pure, config-driven word matching — no LLM, no I/O — used to decide, before (or
instead of) invoking the model:
  - `is_off_limits`: does the question mention a term we know we never cover
    (blacklist / "fuera de alcance")? Checked at the top of the flow so such a
    term is refused immediately, before any document search.
  - `mentions_known_data`: does the question mention data we actually have
    (a dataset category or key, or a whitelisted alias like "billete verde")?
    This is the "chequeo contra la lista de datos".
  - `advisory_intent`, `collection_intent`: is the question an investment
    calculation, or about the wiki as a whole? Their expressions come from the
    `[intent]` section of the wiki or from the base lists (`intent.py`).
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
    return off_limits_term(question, off_limits) is not None


def off_limits_term(question: str, off_limits: Iterable[str]) -> str | None:
    """The first blacklisted term the question mentions, by whole word, or None."""
    q = _normalize(question)
    return next((term for term in off_limits if _mentions(q, term)), None)


def _mentions_stem(question_stemmed: str, term: str, language: str) -> bool:
    """True if the stems of `term` appear as whole words in the stemmed question."""
    from domain.text.stemming import stem_text

    stemmed_term = stem_text(term, language)
    if not stemmed_term:
        return False
    return re.search(rf"\b{re.escape(stemmed_term)}\b", question_stemmed) is not None


def matched_terms(
    question: str,
    vocabulary: Iterable[str],
    aliases: Iterable[str] = (),
    *,
    language: str | None = None,
    exact: Iterable[str] = (),
) -> list[str]:
    """The known data terms and whitelisted aliases the question mentions, each as it
    is written in the roster (`plazo_fijo`, not its stem), without repeats.

    The order is: the `exact` terms, the `vocabulary` terms, the `aliases`.
    With `language`, `vocabulary` and `aliases` are compared by stem in that
    language, so "plazos fijos" matches the category `plazo_fijo`
    (design_stemming.md, decision 15). `exact` holds the terms compared by whole
    normalized word, without stemming — the dataset keys, which are proper names
    and codes (`GGAL`, `Banco Galicia`). Without `language`, every term is
    compared by whole normalized word.
    """
    q = _normalize(question)
    if language is None:
        candidates = [(term, _mentions(q, term)) for term in (*exact, *vocabulary, *aliases)]
    else:
        from domain.text.stemming import stem_text

        q_stemmed = stem_text(question, language)
        candidates = [(term, _mentions(q, term)) for term in exact]
        candidates += [(term, _mentions_stem(q_stemmed, term, language)) for term in (*vocabulary, *aliases)]
    return list(dict.fromkeys(term for term, hit in candidates if hit))


def mentions_known_data(
    question: str,
    vocabulary: Iterable[str],
    aliases: Iterable[str] = (),
    *,
    language: str | None = None,
    exact: Iterable[str] = (),
) -> bool:
    """True if the question mentions a known data term or a whitelisted alias
    (`matched_terms` is not empty)."""
    return bool(matched_terms(question, vocabulary, aliases, language=language, exact=exact))


def cue_matches(question_norm: str, cue: str) -> bool:
    """True if `cue` appears at the start of a word of the normalized question.

    Nothing is required after the cue, so a cut word covers every ending
    ("alternativ" matches "alternativas"), and a cue never matches inside a
    word ("el wiki" does not match "del wiki")."""
    normalized_cue = _normalize(cue)
    if not normalized_cue:
        return False
    return re.search(rf"\b{re.escape(normalized_cue)}", question_norm) is not None


def advisory_intent(question: str, cues: Iterable[str], shapes: Iterable[re.Pattern]) -> bool:
    """True if the question is an investment-advisory request even without naming
    a specific instrument (e.g. "tengo $1M por 3 meses, ¿qué alternativas tengo?").

    The gate routes a data question to the tools by NAMED data term
    (`mentions_known_data`); a generic advisory question names none, so it would
    otherwise refuse. It is recognised by an advisory cue (`cues`: the
    `[intent] advisory` list of the wiki, or the base list of its active domains)
    TOGETHER WITH one of the `shapes` (a money amount or a time horizon, from the
    same domains) — kept conservative so an ordinary question that merely
    contains a number doesn't route to the (amount-hungry) advisory tool. With no
    cue or no shape, the answer is False: a wiki without an advisory domain never
    routes a question to a tool it lacks.
    """
    q = _normalize(question)
    if not any(cue_matches(q, cue) for cue in cues):
        return False
    return any(shape.search(q) for shape in shapes)


# The shapes of a collection question, per language of the wiki. The words that
# can change from one wiki to another are in the `[intent] collection` list; these
# patterns stay in the code. They name no word of a particular corpus.
_COLLECTION_PATTERNS: dict[str, tuple[re.Pattern, ...]] = {
    "en": (
        re.compile(r"\bshares?\b"),
        re.compile(r"\bsummari[sz]e?\b.*\b(each|all)\b"),
        re.compile(r"\bcompar\w*\b.*\b(each|all|both)\b"),
    ),
    "es": (
        re.compile(r"\bcompar\w*\b.*\b(todos|todas)\b"),
    ),
}


def collection_intent(question: str, cues: Iterable[str], language: str) -> bool:
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

    `cues` is the `[intent] collection` list of the wiki, or the base list of its
    language (`domain/chat/intent.py`); the patterns are those of `language`.
    Kept conservative: a cue must name the collective ("each", "all the",
    "this/the wiki", "comparten", "in common", …), not just contain a number or a
    plural. It is better to miss a borderline collection question and fall back
    to the refusal than to fire on an ordinary single-subject question and inject
    the overview where a concept page was wanted.
    """
    q = _normalize(question)
    if any(cue_matches(q, cue) for cue in cues):
        return True
    return any(pattern.search(q) for pattern in _COLLECTION_PATTERNS.get(language, ()))


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
