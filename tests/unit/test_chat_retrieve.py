"""Tests for the code-driven retrieval wrappers in domain/chat/preretrieval.py.

`retrieve_wiki` / `retrieve_source_chunks` are thin formatters over
`domain.tools.search.search_chunks` (scoped to wiki vs source docs). We
monkeypatch search_chunks so these stay pure unit tests — the FTS correctness is
search_chunks' own concern.
"""

from domain.chat import preretrieval
from domain.chat.preretrieval import (
    retrieve_collection_pages,
    retrieve_source_chunks,
    retrieve_wiki,
)


def test_retrieve_wiki_formats_and_drops_empty(monkeypatch):
    rows = [
        {"content": "Una caución es una operación...", "path": "wiki/concepts/cauciones-bursatiles.md"},
        {"content": "   ", "path": "wiki/concepts/empty.md"},  # empty -> dropped
    ]
    monkeypatch.setattr(preretrieval, "search_chunks", lambda *a, **k: rows)
    hits = retrieve_wiki("db", "caución")
    assert len(hits) == 1
    assert "cauciones-bursatiles.md" in hits[0]
    assert "Una caución es" in hits[0]


def test_retrieve_wiki_uses_wiki_scope(monkeypatch):
    captured = {}

    def fake(db, query, limit=10, scope="all"):
        captured["scope"] = scope
        return []

    monkeypatch.setattr(preretrieval, "search_chunks", fake)
    retrieve_wiki("db", "x")
    assert captured["scope"] == "wiki"


def test_retrieve_source_uses_sources_scope_and_filename(monkeypatch):
    captured = {}

    def fake(db, query, limit=10, scope="all"):
        captured["scope"] = scope
        return [{"content": "chunk crudo", "filename": "05 Bonos.docx"}]

    monkeypatch.setattr(preretrieval, "search_chunks", fake)
    hits = retrieve_source_chunks("db", "cedears")
    assert captured["scope"] == "sources"
    assert "05 Bonos.docx" in hits[0]
    assert "chunk crudo" in hits[0]


def test_retrieve_empty_when_no_hits(monkeypatch):
    monkeypatch.setattr(preretrieval, "search_chunks", lambda *a, **k: [])
    assert retrieve_wiki("db", "x") == []
    assert retrieve_source_chunks("db", "x") == []


def _capture_query(monkeypatch):
    """Monkeypatch search_chunks to record the query string it receives."""
    seen = {}

    def fake(db, query, limit=10, scope="all"):
        seen["query"] = query
        return []

    monkeypatch.setattr(preretrieval, "search_chunks", fake)
    return seen


def test_retrieve_wiki_sanitizes_fts_query(monkeypatch):
    """A raw natural-language question must not reach FTS5 verbatim.

    FTS5 reads ',', '?', '¿' as syntax, so the raw question raised
    OperationalError and silently returned no hits — the pre-retrieval gate then
    fell through to a context-less plan. The wrapper must tokenize the question
    into a safe OR-query before it hits MATCH.
    """
    seen = _capture_query(monkeypatch)
    retrieve_wiki("db", "Si hago un plazo fijo, ¿le estoy ganando a la inflación?")
    q = seen["query"]
    assert not any(ch in q for ch in ",¿?"), f"unsanitized FTS query: {q!r}"
    assert "plazo" in q and "inflación" in q
    assert " OR " in q  # tokens are OR-joined, not passed as one phrase


def test_retrieve_source_sanitizes_fts_query(monkeypatch):
    seen = _capture_query(monkeypatch)
    retrieve_source_chunks("db", "¿Cuánto ganaría con acciones de YPF?")
    q = seen["query"]
    assert not any(ch in q for ch in ",¿?"), f"unsanitized FTS query: {q!r}"
    assert "acciones" in q and "YPF" in q


def test_retrieve_wiki_punctuation_only_query_is_empty(monkeypatch):
    """A query with no word characters sanitizes to '' → search_chunks skips it."""
    seen = _capture_query(monkeypatch)
    retrieve_wiki("db", "¿? , .")
    assert seen["query"] == ""


def test_retrieve_wiki_drops_stopwords_and_short_tokens(monkeypatch):
    """Ubiquitous words (es, la, de, qué…) must not reach FTS.

    OR-joining every token means a stopword that appears on nearly every page
    makes an off-topic question match the whole corpus — defeating the roster
    gate. Only content tokens survive.
    """
    seen = _capture_query(monkeypatch)
    retrieve_wiki("db", "¿Qué es la capital de Francia?", language="es")
    q = seen["query"]
    for junk in ('"es"', '"la"', '"de"', '"Qué"', '"qué"'):
        assert junk not in q, f"stopword leaked into FTS query: {junk} in {q!r}"
    assert '"capital"' in q and '"Francia"' in q


def test_retrieve_wiki_all_stopwords_query_is_empty(monkeypatch):
    """A question made only of stopwords sanitizes to '' (nothing to match on)."""
    seen = _capture_query(monkeypatch)
    retrieve_wiki("db", "¿Qué es esto?", language="es")
    assert seen["query"] == ""


# ── retrieve_collection_pages (reads wiki/overview.md + wiki/index.md from disk)


def test_retrieve_collection_pages_overview_then_index(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "overview.md").write_text("# Knowledge Base Overview\n\nOverview prose.\n")
    (wiki / "index.md").write_text("# Wiki Index\n\n- [A](a.md)\n")

    pages = retrieve_collection_pages(tmp_path)

    assert len(pages) == 2
    assert "wiki/overview.md" in pages[0] and "Overview prose" in pages[0]
    assert "wiki/index.md" in pages[1] and "Wiki Index" in pages[1]


def test_retrieve_collection_pages_strips_front_matter(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "overview.md").write_text(
        "---\ntype: overview\n---\n# Knowledge Base Overview\n\nOverview prose.\n"
    )

    pages = retrieve_collection_pages(tmp_path)

    assert len(pages) == 1
    assert "type: overview" not in pages[0]
    assert "Overview prose" in pages[0]


def test_retrieve_collection_pages_only_overview_present(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "overview.md").write_text("# Knowledge Base Overview\n\nOverview prose.\n")

    pages = retrieve_collection_pages(tmp_path)

    assert len(pages) == 1
    assert "wiki/overview.md" in pages[0]


def test_retrieve_collection_pages_empty_when_neither_exists(tmp_path):
    assert retrieve_collection_pages(tmp_path) == []


# ── stop words are per language ─────────────────────────────────────────────
# Regression tests for the defect that made Tier 2 unreachable in English: the
# stop-word set was Spanish-only, so "the" (3 chars, past the length filter)
# reached FTS and matched nearly every chunk. `wiki_hits` was then never empty,
# and `doc_hits` is computed only when it IS empty.

def test_english_function_words_do_not_reach_fts(monkeypatch):
    """"the"/"what"/"is" must be filtered for an English wiki, as "qué"/"la" are
    for a Spanish one. Before this, they were not, because the only set was
    Spanish."""
    seen = _capture_query(monkeypatch)
    retrieve_wiki("db", "What is the capital of France?", language="en")
    q = seen["query"]
    for junk in ('"What"', '"the"', '"is"', '"of"'):
        assert junk not in q, f"English stop word leaked into FTS query: {junk} in {q!r}"
    assert '"capital"' in q and '"France"' in q


def test_english_question_of_only_function_words_matches_nothing(monkeypatch):
    """The shape of the original bug: a question carrying no content word must
    sanitize to '' so `wiki_hits` comes back empty and Tier 2 stays reachable."""
    seen = _capture_query(monkeypatch)
    retrieve_wiki("db", "What are these?", language="en")
    assert seen["query"] == ""


def test_stopword_sets_are_language_specific_not_merged(monkeypatch):
    """A function word in one language is a content word in another.

    Spanish "son" (they are) is English "son", which appears in the fairy-tale
    corpus. One merged set would drop the key word of "Who is the king's son?".
    """
    seen = _capture_query(monkeypatch)
    retrieve_wiki("db", "Who is the king's son?", language="en")
    assert '"son"' in seen["query"], "English content word dropped by the Spanish set"

    seen = _capture_query(monkeypatch)
    retrieve_wiki("db", "¿Cuáles son las cauciones?", language="es")
    assert '"son"' not in seen["query"], "Spanish function word survived"


def test_unknown_language_falls_back_to_english(monkeypatch):
    """`load_wiki_language` resolves an absent or unknown value to "en", so the
    stop-word lookup matches what such a wiki actually generates."""
    seen = _capture_query(monkeypatch)
    retrieve_wiki("db", "What is the capital of France?", language="it")
    assert '"the"' not in seen["query"]

    seen = _capture_query(monkeypatch)
    retrieve_wiki("db", "What is the capital of France?")  # no language at all
    assert '"the"' not in seen["query"]
