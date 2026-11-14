"""Value types shared by the verification modules."""

from __future__ import annotations

from dataclasses import dataclass

# A source that is a description of an image carries the image as a fragment:
# `sources/x.pdf#image:imageFile4.png` (docs/design_image_handling.md, D7).
IMAGE_FRAGMENT = "#image:"


def image_source(path: str, file: str | None) -> str:
    """`path` with the fragment that names the image, or `path` when `file` is None."""
    return f"{path}{IMAGE_FRAGMENT}{file}" if file else path


def split_source(source: str | None) -> tuple[str | None, str | None]:
    """(path, image file) of a stored `best_source`: the image file is None when the
    source has no `#image:` fragment. The only place that separates the fragment."""
    if source is None:
        return None, None
    path, _, file = source.partition(IMAGE_FRAGMENT)
    return path, (file or None)


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
    # The file of the image when the passage is a generated description of it.
    image: str | None = None


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

    @property
    def image(self) -> str | None:
        """The image file when the best passage is a generated description."""
        return split_source(self.page)[1]

    @property
    def supported_by_image(self) -> bool:
        """The third state: supported, and only by a generated description of an image."""
        return self.supported and self.image is not None


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

    @property
    def described(self) -> int:
        """Sentences supported only by a generated description of an image."""
        return sum(1 for s in self.sentences if s.supported_by_image)

    def unsupported_sentences(self) -> list[SentenceResult]:
        return [s for s in self.sentences if not s.supported]

    def described_sentences(self) -> list[SentenceResult]:
        return [s for s in self.sentences if s.supported_by_image]
