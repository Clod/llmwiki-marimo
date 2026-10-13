"""Pre-select the passages a sentence is compared with.

The verifier costs one model call per (passage, sentence) pair. For a wiki page
the context can hold hundreds of passages, so each sentence keeps only the `k`
passages most similar to it. The ranking is the project's full-text search: the
passages go into a temporary FTS5 table, stemmed in the wiki's language as the
index is (`domain/text/stemming.py`), and the sentence is the query, ranked with
BM25. The temporary table lives in memory and is dropped with the call.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence

from domain.text.stemming import stem_text

_WORD = re.compile(r"\w+", re.UNICODE)


def _match_expression(sentence: str, language: str) -> str:
    """An FTS5 expression that matches any stemmed word of the sentence."""
    stems = {w for w in _WORD.findall(stem_text(sentence, language)) if len(w) > 1 or w.isdigit()}
    return " OR ".join(f'"{stem}"' for stem in sorted(stems))


def select_candidates(
    passages: Sequence[str], sentences: Sequence[str], k: int, language: str = "en",
) -> list[list[int]]:
    """For each sentence, the indexes of its `k` best passages, best first.

    A sentence with no word in common with any passage gets an empty list: no
    passage can entail it, and the caller reports it as unsupported without
    calling the verifier. When there are `k` passages or fewer, every passage is
    kept and no ranking runs.
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    if len(passages) <= k:
        return [list(range(len(passages))) for _ in sentences]

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE p USING fts5(content_stemmed, tokenize='unicode61')")
        conn.executemany(
            "INSERT INTO p(rowid, content_stemmed) VALUES (?, ?)",
            [(i + 1, stem_text(text, language)) for i, text in enumerate(passages)],
        )
        chosen: list[list[int]] = []
        for sentence in sentences:
            expression = _match_expression(sentence, language)
            if not expression:
                chosen.append([])
                continue
            rows = conn.execute(
                "SELECT rowid FROM p WHERE p MATCH ? ORDER BY rank LIMIT ?", (expression, k),
            ).fetchall()
            chosen.append([row[0] - 1 for row in rows])
        return chosen
    finally:
        conn.close()
