"""Refusals that say why (design_refusal_messages.md).

Every refusal states its cause, and every refusal except the blacklist's lists
suggestions: the titles of wiki pages related to the question, without links.
With no page to suggest, the message points to the wiki's index. The code builds
every text, so the text of a cause is the same on every refusal of that cause,
and a refusal is recognised by the fixed opening of its cause (decision 14).

The model's own refusal (cause 5) is recognised by a fixed opening sentence,
which the code asks every chat model to use (`refusal_directive`, appended to
the system prompt by `domain.i18n.apply_chat_directive`).
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field

# ── The model's fixed refusal sentence (decision 8) ──────────────────────────

FIXED_SENTENCE = {
    "es": "No encontré la respuesta en los documentos.",
    "en": "I did not find the answer in the documents.",
}

_DIRECTIVE = {
    "es": (
        "## Rechazo\n"
        "Cuando las páginas y los documentos no contienen la respuesta, comienza "
        "la respuesta exactamente con esta oración: «{sentence}» Esta oración "
        "reemplaza cualquier otra fórmula de rechazo que pida este prompt."
    ),
    "en": (
        "## Refusal\n"
        "When the pages and documents do not contain the answer, open the answer "
        "with exactly this sentence: \"{sentence}\" This sentence replaces any "
        "other refusal wording this prompt asks for."
    ),
}


def _lang(language: str | None) -> str:
    return "es" if (language or "en").lower().startswith("es") else "en"


def fixed_sentence(language: str | None) -> str:
    """The sentence the model opens its own refusal with."""
    return FIXED_SENTENCE[_lang(language)]


def refusal_directive(language: str | None) -> str:
    """The system-prompt instruction that asks the model for the fixed sentence."""
    lang = _lang(language)
    return _DIRECTIVE[lang].format(sentence=FIXED_SENTENCE[lang])


# ── Causes and the refusal value ─────────────────────────────────────────────

class RefusalCause(enum.Enum):
    BLACKLIST = "blacklist"            # cause 1: a [fuera_de_alcance] term
    NOT_IN_ROSTER = "not_in_roster"    # cause 2a: names no topic of the wiki
    NOTHING_FOUND = "nothing_found"    # cause 2b: a topic of the wiki, no text found
    UNSUPPORTED = "unsupported"        # cause 3: Tier-2 answer not grounded in its document
    UNGROUNDED = "ungrounded"          # cause 4: no evidence behind the answer
    MODEL = "model"                    # cause 5: the model refused, with the fixed sentence


@dataclass(frozen=True)
class Refusal:
    """One refusal: its cause and what its message names.

    term:        the matched blacklist term (BLACKLIST).
    document:    the source document the answer came from (UNSUPPORTED).
    model_text:  the model's own text (MODEL), kept as the start of the message.
    suggestions: (title, relative_path) of related wiki pages; the message shows
                 only the title.
    """

    cause: RefusalCause
    term: str | None = None
    document: str | None = None
    model_text: str | None = None
    suggestions: list[tuple[str, str]] = field(default_factory=list)


# ── Message templates (sections 5.5 and 5.6) ─────────────────────────────────

_OPENING = {
    "es": {
        RefusalCause.BLACKLIST: (
            "No puedo responder esta pregunta: menciona «{term}», que está en la "
            "lista de temas sobre los que esta wiki no tiene permitido responder."
        ),
        RefusalCause.NOT_IN_ROSTER: (
            "No puedo responder esta pregunta: no nombra ninguno de los temas de esta wiki."
        ),
        RefusalCause.NOTHING_FOUND: (
            "La pregunta nombra un tema de esta wiki, pero no encontré en sus páginas "
            "ni en sus documentos un texto que la responda."
        ),
        RefusalCause.UNSUPPORTED: (
            "Encontré el documento «{document}», pero no puedo garantizar que la "
            "respuesta generada esté fundamentada en ese documento, así que no la muestro."
        ),
        RefusalCause.UNGROUNDED: (
            "No encontré en las páginas ni en los documentos de esta wiki información "
            "que respalde una respuesta."
        ),
        RefusalCause.MODEL: (
            "El modelo no encontró la respuesta en las páginas ni en los documentos "
            "de esta wiki."
        ),
    },
    "en": {
        RefusalCause.BLACKLIST: (
            "I cannot answer this question: it mentions \"{term}\", which is on the "
            "list of topics this wiki is not allowed to answer."
        ),
        RefusalCause.NOT_IN_ROSTER: (
            "I cannot answer this question: it names none of this wiki's topics."
        ),
        RefusalCause.NOTHING_FOUND: (
            "The question names a topic of this wiki, but I found no text in its "
            "pages or documents that answers it."
        ),
        RefusalCause.UNSUPPORTED: (
            "I found the document \"{document}\", but I cannot guarantee that the "
            "generated answer is grounded in that document, so I am not showing it."
        ),
        RefusalCause.UNGROUNDED: (
            "I found no information in this wiki's pages or documents to support an answer."
        ),
        RefusalCause.MODEL: (
            "The model did not find the answer in this wiki's pages or documents."
        ),
    },
}

_SUGGEST_REWORD = {
    "es": ("Estas páginas tratan temas relacionados. Si alguna corresponde a tu "
           "consulta, reformulá la pregunta usando su título:"),
    "en": ("These pages cover related topics. If one of them matches your "
           "question, ask again using its title:"),
}
_SUGGEST = {
    "es": "Estas páginas tratan temas relacionados:",
    "en": "These pages cover related topics:",
}
_INDEX = {
    "es": "Podés ver todos los temas en el índice de la wiki, en la pestaña de lectura.",
    "en": "You can see every topic in the wiki's index, in the Read tab.",
}


def render(refusal: Refusal, language: str | None) -> str:
    """The message text of `refusal`, in the wiki's language."""
    lang = _lang(language)
    opening = _OPENING[lang][refusal.cause].format(
        term=refusal.term or "", document=refusal.document or "",
    )
    lines = [opening]
    if refusal.cause is not RefusalCause.BLACKLIST:
        if refusal.suggestions:
            header = (_SUGGEST_REWORD if refusal.cause is RefusalCause.NOT_IN_ROSTER
                      else _SUGGEST)[lang]
            lines.append(header)
            lines.extend(f"- {title}" for title, _ in refusal.suggestions)
        else:
            lines.append(_INDEX[lang])
    body = "\n".join(lines)
    if refusal.cause is RefusalCause.MODEL and refusal.model_text:
        return f"{refusal.model_text.rstrip()}\n\n{body}"
    return body


# ── Recognising a refusal (decision 14) ──────────────────────────────────────

def _fixed_openings() -> tuple[str, ...]:
    """The part of each opening that never varies: the text before `{`."""
    openings = []
    for by_cause in _OPENING.values():
        for cause, template in by_cause.items():
            if cause is RefusalCause.MODEL:
                continue       # cause 5 opens with the model's text, not ours
            openings.append(template.split("{", 1)[0])
    return tuple(openings) + tuple(FIXED_SENTENCE.values())


_OPENINGS = _fixed_openings()


def is_refusal(text: str) -> bool:
    """True if `text` is a refusal: it opens with the fixed opening of a cause,
    or with the model's fixed sentence (cause 5)."""
    head = (text or "").lstrip()
    return any(head.startswith(opening) for opening in _OPENINGS)


# ── Suggestions (decisions 3, 9, 10 and 13) ──────────────────────────────────

_MAX_SUGGESTIONS = 5


def order_suggestions(
    pages: list[tuple[str, str]], question: str, language: str | None,
) -> list[tuple[str, str]]:
    """Dedupe, order and cap the suggested pages.

    Three groups, in the order of `pages` within each group: concept pages
    whose title shares a stem with a word of the question, the other concept
    pages, then summary pages. At most five.
    """
    from domain.text.stemming import stem_text

    lang = language or "en"
    question_stems = set(stem_text(question, lang).split())
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for title, path in pages:
        if path and path not in seen:
            seen.add(path)
            unique.append((title, path))

    def group(page: tuple[str, str]) -> int:
        title, path = page
        if "/summaries/" in path:
            return 2
        return 0 if question_stems & set(stem_text(title, lang).split()) else 1

    return sorted(unique, key=group)[:_MAX_SUGGESTIONS]


def search_suggestions(db_path: str, question: str, language: str | None) -> list[tuple[str, str]]:
    """Suggestions from the index search with the question (causes 2a, 4, 5)."""
    from domain.chat.preretrieval import _fts_query
    from domain.tools.search import search_chunks

    rows = search_chunks(
        db_path, _fts_query(question, language), limit=6, scope="wiki", language=language,
    )
    pages = [(row.get("title") or "", f"{row.get('path') or ''}{row.get('filename') or ''}")
             for row in rows]
    return order_suggestions(pages, question, language)


def roster_suggestions(
    db_path: str, question: str, categories: set[str], language: str | None,
) -> list[tuple[str, str]]:
    """Suggestions for causes 2b and 3: the concept pages of the roster terms
    the question named (decision 13).

    A concept page is suggested when the question names its title, or names a
    dataset category whose stems appear in the title — "¿Cuánto rinden los
    plazos fijos?" names `plazo_fijo`, whose stems `plaz fij` appear in
    "Plazo fijo tradicional" and "Plazo fijo UVA".
    """
    from domain.chat.scope import mentions_known_data
    from domain.text.stemming import stem_text
    from domain.tools.db import get_connection

    lang = language or "en"
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT title, path || filename AS p FROM documents "
            "WHERE source_kind = 'wiki' AND path = '/wiki/concepts/' AND status != 'failed' "
            "ORDER BY title"
        ).fetchall()
    named_categories = [c for c in categories if mentions_known_data(question, [c], language=lang)]
    category_stems = [stem_text(c, lang) for c in named_categories]
    pages: list[tuple[str, str]] = []
    for row in rows:
        title = row["title"] or ""
        stemmed_title = stem_text(title, lang)
        if mentions_known_data(question, [title], language=lang) or any(
            re.search(rf"\b{re.escape(s)}\b", stemmed_title) for s in category_stems if s
        ):
            pages.append((title, row["p"]))
    return order_suggestions(pages, question, language)
