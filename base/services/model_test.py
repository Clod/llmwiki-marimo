"""The model tests of the Settings screen and of `scripts/eval_chat_model.py`.

`run` runs the test of one kind of model (`domain/modeltest/`). `fingerprint` names a
set of values (URL, key, model), so a caller can tell whether those values passed a test.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from domain import modeltest
from domain.modeltest import Check, Report

KINDS = ("reading", "ingestion", "vision")

_TESTS = {
    "reading": modeltest.test_reading,
    "ingestion": modeltest.test_ingestion,
    "vision": modeltest.test_vision,
}


def run(kind: str, base_url: str, api_key: str, model: str, language: str = "en",
        progress: Callable[[Check], None] | None = None) -> Report:
    """Run the test of `kind` ("reading", "ingestion" or "vision") with these values.

    `language` is the content language of the material. `progress` receives each check
    when it ends. Raises `ValueError` for an unknown kind.
    """
    if kind not in _TESTS:
        raise ValueError(f"unknown model test: {kind!r}; expected one of {', '.join(KINDS)}")
    return _TESTS[kind](base_url, api_key, model, language, progress)


def fingerprint(base_url: str, api_key: str, model: str) -> str:
    """The sha256 of the three values. Two sets of values have the same fingerprint only
    when the three values are equal."""
    return hashlib.sha256("\0".join((base_url, api_key, model)).encode("utf-8")).hexdigest()
