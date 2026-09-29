"""Stop words per wiki language: the function words that carry no search or
grounding signal.

One set per language, shared by the FTS query builder
(`domain/chat/preretrieval.py:_fts_query`) and the Tier-2 verification
(`domain/chat/overlap.py`). Both compare the set against unstemmed words, before
stemming, because the set holds unstemmed words.
"""

from __future__ import annotations

# ── ADDING A LANGUAGE ────────────────────────────────────────────────────────
# A new wiki language needs an entry here. Without one it falls back to English
# (`stopwords` below), which filters little in another language and leaves the
# original defect: a word as common as "the" matches nearly every chunk, so
# `wiki_hits` is never empty, and Tier 2 — reached only when `wiki_hits` IS
# empty — becomes unreachable. That is exactly what happened to English before
# `en` was added here. See `docs/manual/workflows.md` §6.7.
#
# The sets must stay language-SPECIFIC rather than merged into one. A function
# word in one language is often a content word in another: Spanish "son" (they
# are) is English "son", which appears in six chunks of the fairy-tale corpus.
# Merging the sets would silently drop the most important word of "Who is the
# king's son?".
STOPWORDS: dict[str, frozenset[str]] = {
    "es": frozenset({
        "que", "qué", "los", "las", "una", "unos", "unas", "con", "por", "para",
        "del", "como", "cómo", "son", "sos", "está", "estan", "están", "este",
        "esta", "esto", "estos", "estas", "cual", "cuál", "cuales", "cuáles",
        "quien", "quién", "dame", "hago", "estoy", "más", "mas", "pero", "sus",
        "nos", "les", "ese", "esa", "eso", "aquel", "sobre", "entre", "desde",
        "hasta", "donde", "dónde", "cuando", "cuándo", "muy", "hay", "tengo",
        # From the former Tier-2 list of domain/chat/overlap.py, merged here so
        # each language has one set (design_stemming.md, decision 11).
        "el", "la", "un", "lo", "al", "de", "en", "a", "y", "o", "u", "e", "se",
        "su", "sin", "si", "no", "es", "ser", "estos", "ha", "han", "ya", "le",
        "me", "te", "fue", "tiene", "tienen", "puede", "pueden", "porque",
        "tambien", "también", "solo", "cada", "sea", "ni", "cuanto", "cuánto",
    }),
    # Same categories as the Spanish set: articles, prepositions, pronouns,
    # auxiliaries, question words, and the verbs a question is phrased with
    # ("tell me", "explain") — the counterparts of "dame"/"hago"/"tengo".
    # Deliberately excluded because they can be content in a wiki: "may"
    # (month), "will" is kept (auxiliary use dominates), "son" is NOT here (it
    # is a content word in English — see the note above).
    "en": frozenset({
        "the", "and", "for", "are", "was", "were", "has", "have", "had", "but",
        "not", "you", "your", "this", "that", "these", "those", "with", "from",
        "into", "about", "what", "which", "who", "whom", "whose", "when",
        "where", "why", "how", "does", "did", "can", "could", "would", "should",
        "will", "there", "their", "them", "they", "its", "his", "her", "hers",
        "our", "ours", "any", "all", "some", "more", "most", "than", "then",
        "also", "been", "being", "over", "under", "between", "off", "very",
        "just", "only", "such", "too", "tell", "give", "show", "explain",
        "describe", "please",
    }),
}


def stopwords(language: str | None) -> frozenset[str]:
    """The stop-word set for `language`, falling back to English.

    English is the fallback because it is the project's default wiki language
    (`wiki_settings.load_wiki_language` resolves an absent or unknown value to
    `"en"`), so this matches what such a wiki actually generates.
    """
    return STOPWORDS.get((language or "en").lower(), STOPWORDS["en"])
