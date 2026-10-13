"""Sentence-level verification of a chat answer against the passages the model read."""

from domain.verify.core import DEFAULT_THRESHOLD, verify_answer
from domain.verify.models import (
    ContextPassage,
    Passage,
    Sentence,
    SentenceResult,
    VerificationReport,
)
from domain.verify.verifier import FakeVerifier, Verifier

__all__ = [
    "DEFAULT_THRESHOLD", "verify_answer", "ContextPassage", "Passage", "Sentence",
    "SentenceResult", "VerificationReport", "FakeVerifier", "Verifier",
]
