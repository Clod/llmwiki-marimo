"""The result of a model test: one `Check` per condition, and the `Report` of the test."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    """One condition of a test.

    key:    the name of the condition: "answers", "declines", "uses_tools", "ingests",
            "reads_page" or "describes".
    passed: True when the model met the condition.
    detail: one sentence, in English, with the measured values.
    ran:    False when the model gave no answer, because the call to the service failed.
            A check that did not run has `passed` False and does not count as a
            failure of the model.
    """

    key: str
    passed: bool
    detail: str
    ran: bool = True


@dataclass(frozen=True)
class Report:
    """The checks of one test of one model, and its duration in seconds."""

    kind: str                 # "reading" | "ingestion" | "vision"
    model: str
    checks: tuple[Check, ...]
    seconds: float

    @property
    def passed(self) -> bool:
        """True when every check passed."""
        return bool(self.checks) and all(c.passed for c in self.checks)

    @property
    def could_run(self) -> bool:
        """True when at least one check ran."""
        return any(c.ran for c in self.checks)
