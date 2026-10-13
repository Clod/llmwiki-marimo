"""The verifier interface and the fake verifier used by the tests.

A verifier receives (passage, sentence) pairs and returns, for each pair, the
probability that the passage entails the sentence. The core knows nothing else
about it, so a remote verifier needs only these two members.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class Verifier(Protocol):
    """Scores whether a passage entails a sentence."""

    name: str
    # The longest passage, in characters, the verifier takes as a premise.
    max_passage_chars: int

    def entailment(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        """For each `(passage, sentence)` pair, the probability in [0, 1] that
        the passage entails the sentence. The result has one value per pair."""
        ...


_WORD = re.compile(r"\w+", re.UNICODE)


def content_words(text: str) -> set[str]:
    """Lowercase words of four or more letters, without accents."""
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return {w for w in _WORD.findall(folded) if len(w) >= 4 or w.isdigit()}


def lexical_overlap(passage: str, sentence: str) -> float:
    """The share of the sentence's content words that the passage contains."""
    words = content_words(sentence)
    if not words:
        return 0.0
    return len(words & content_words(passage)) / len(words)


class FakeVerifier:
    """A deterministic verifier for tests: the probability is the lexical
    overlap, or the value of `score` when given. It records every call."""

    def __init__(
        self, score: Callable[[str, str], float] | None = None, *,
        name: str = "fake", max_passage_chars: int = 1200,
    ) -> None:
        self.name = name
        self.max_passage_chars = max_passage_chars
        self._score = score or lexical_overlap
        self.calls: list[list[tuple[str, str]]] = []

    def entailment(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        self.calls.append(list(pairs))
        return [float(self._score(passage, sentence)) for passage, sentence in pairs]
