"""Lint report data types."""

from dataclasses import dataclass, field


@dataclass
class LintIssue:
    check: str       # "orphan" | "stale" | "missing_xref" | "missing_concept" | "contradiction" | "data_gap" | "gap_filled"
    severity: str    # "error" | "warning" | "info"
    page: str        # affected page path (e.g. "/wiki/concepts/federal-reserve.md")
    description: str
    suggestion: str
    related_page: str = ""  # the "other" page (path_b) for xref/contradiction
    topic: str = ""         # gap topic slug for data_gap / gap_filled


@dataclass
class LintReport:
    issues: list[LintIssue] = field(default_factory=list)
    checked_at: str = ""

    @property
    def errors(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def summary(self) -> str:
        e, w, n = len(self.errors), len(self.warnings), len(self.issues)
        return f"{n} issue(s): {e} error(s), {w} warning(s), {n - e - w} info"
