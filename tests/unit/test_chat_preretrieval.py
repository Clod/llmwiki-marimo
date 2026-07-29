"""Tests for domain/chat/preretrieval.py — the hybrid pre-retrieval orchestration.

Starts with `build_vocabulary`: the closed list of data terms (dataset
categories + their keys) that the scope gate checks a question against. Built
from a `DatasetSource` (duck-typed here with a fake — no I/O, no LLM).
"""

from types import SimpleNamespace

from domain.chat.preretrieval import build_vocabulary


class _FakeSource:
    """Minimal DatasetSource: categories() + query() returning rows with .clave."""

    def __init__(self, data: dict[str, list[str]]) -> None:
        self._data = data

    def categories(self) -> list[str]:
        return list(self._data)

    def query(self, categoria, clave=None, metrica=None, dims=None):
        # A key may appear on several rows (one per metrica); the builder dedupes.
        return [SimpleNamespace(clave=c) for c in self._data.get(categoria, [])]


def test_vocabulary_is_categories_plus_keys():
    source = _FakeSource({
        "dolar": ["MEP", "MEP", "CCL"],          # MEP twice (compra/venta rows)
        "plazo_fijo": ["Banco Nación", "Credicoop"],
    })
    vocab = build_vocabulary(source)
    assert vocab == {"dolar", "MEP", "CCL", "plazo_fijo", "Banco Nación", "Credicoop"}


def test_empty_source_is_empty_vocabulary():
    assert build_vocabulary(_FakeSource({})) == set()
