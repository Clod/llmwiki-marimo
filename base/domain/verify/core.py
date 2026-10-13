"""Verify the sentences of an answer against the passages the model read.

For each checked sentence the core pairs it with the passages most like it,
asks the verifier for the entailment probability of each pair and keeps the best
passage. A sentence is supported when that probability reaches the threshold.
The answer is not changed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from domain.chat import refusal
from domain.chat.config import DEFAULT_VERIFY_THRESHOLD
from domain.verify.checkable import generated_spans, is_checkable, plain_text
from domain.verify.models import ContextPassage, SentenceResult, VerificationReport
from domain.verify.passages import split_context
from domain.verify.preselect import select_candidates
from domain.verify.sentences import split_sentences
from domain.verify.verifier import Verifier, lexical_overlap

DEFAULT_THRESHOLD = DEFAULT_VERIFY_THRESHOLD
# Pairs per sentence: the passages with the highest lexical overlap. It bounds
# the cost on the CPU: a page of 40 passages and 10 sentences would be 400 pairs.
MAX_PASSAGES_PER_SENTENCE = 6


def verify_answer(
    answer: str,
    context: Sequence[ContextPassage],
    verifier: Verifier,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    generated: Iterable[str] = (),
    max_passages_per_sentence: int = MAX_PASSAGES_PER_SENTENCE,
    candidates: int | None = None,
    language: str = "en",
) -> VerificationReport:
    """The report of `answer` against `context`.

    `generated` holds texts the code appended to the answer (the advisory
    table); the verifier skips them. With `candidates`, each sentence keeps its
    `candidates` passages best ranked by full-text search in `language`
    (`domain/verify/preselect.py`) instead of the lexical-overlap cap.
    """
    if refusal.is_refusal(answer):
        return VerificationReport((), verifier.name, threshold)   # the code wrote it
    spans = generated_spans(answer, generated)
    sentences = [s for s in split_sentences(answer) if is_checkable(s, answer, spans)]
    if not sentences:
        return VerificationReport((), verifier.name, threshold)

    passages = split_context(context, verifier.max_passage_chars)
    plain = [plain_text(s.text) for s in sentences]
    pairs: list[tuple[str, str]] = []
    owners: list[tuple[int, int]] = []
    if candidates is not None:
        kept = select_candidates([p.text for p in passages], plain, candidates, language)
    else:
        kept = [
            sorted(range(len(passages)), key=lambda j: -lexical_overlap(passages[j].text, text))
            [:max_passages_per_sentence]
            for text in plain
        ]
    for i, text in enumerate(plain):
        for j in kept[i]:
            pairs.append((passages[j].text, text))
            owners.append((i, j))
    scores = verifier.entailment(pairs) if pairs else []
    if len(scores) != len(pairs):
        raise ValueError(f"the verifier returned {len(scores)} scores for {len(pairs)} pairs")

    best: dict[int, tuple[float, int]] = {}
    for (i, j), score in zip(owners, scores):
        if i not in best or score > best[i][0]:
            best[i] = (score, j)

    results = []
    for i, sentence in enumerate(sentences):
        score, j = best.get(i, (0.0, -1))
        passage = passages[j] if j >= 0 else None
        results.append(SentenceResult(
            sentence.start, sentence.end, sentence.text,
            supported=score >= threshold, probability=score,
            page=passage.page if passage else None,
            passage=passage.text if passage else None,
        ))
    return VerificationReport(
        tuple(results), verifier.name, threshold,
        pairs=len(pairs), pairs_possible=len(sentences) * len(passages),
    )
