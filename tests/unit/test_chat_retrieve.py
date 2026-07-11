"""Tests for the code-driven retrieval wrappers in domain/chat/preretrieval.py.

`retrieve_wiki` / `retrieve_source_chunks` are thin formatters over
`domain.tools.search.search_chunks` (scoped to wiki vs source docs). We
monkeypatch search_chunks so these stay pure unit tests — the FTS correctness is
search_chunks' own concern.
"""

from domain.chat import preretrieval
from domain.chat.preretrieval import retrieve_source_chunks, retrieve_wiki


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
