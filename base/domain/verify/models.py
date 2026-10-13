"""Value types shared by the verification modules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContextPassage:
    """One piece of text the model read, with the page it comes from.

    `page` is a wiki page (`/wiki/concepts/x.md`), a dataset file
    (`datasets/x.md`) or the name of a source document (Tier 2 fragment).
    """

    page: str
    text: str
    # The tool that returned the text, or None for text the code injected.
    tool: str | None = None


@dataclass(frozen=True)
class Sentence:
    """A sentence of an answer. `start` and `end` index the answer text, so
    `answer[start:end] == text`."""

    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Passage:
    """A bounded piece of a context page, the premise of one NLI pair."""

    page: str
    text: str


@dataclass(frozen=True)
class SentenceResult:
    """The verdict on one checked sentence."""

    start: int
    end: int
    text: str
    supported: bool
    probability: float
    page: str | None
    passage: str | None


@dataclass(frozen=True)
class VerificationReport:
    """The verdict on every checked sentence of an answer."""

    sentences: tuple[SentenceResult, ...]
    verifier: str
    threshold: float
    # Pairs sent to the verifier, and pairs a full comparison would have sent
    # (checked sentences × passages). Equal when no pre-selection ran.
    pairs: int = 0
    pairs_possible: int = 0

    @property
    def checked(self) -> int:
        return len(self.sentences)

    @property
    def supported(self) -> int:
        return sum(1 for s in self.sentences if s.supported)

    @property
    def unsupported(self) -> int:
        return self.checked - self.supported

    def unsupported_sentences(self) -> list[SentenceResult]:
        return [s for s in self.sentences if not s.supported]
