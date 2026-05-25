"""Repair report data types."""

from dataclasses import dataclass, field


@dataclass
class RepairResult:
    check: str    # which lint check triggered this repair
    page: str     # affected page path
    action: str   # "deleted" | "regenerated" | "concept_created" | "skipped" | "failed"
    success: bool
    message: str


@dataclass
class RepairReport:
    results: list[RepairResult] = field(default_factory=list)

    @property
    def fixed(self) -> list[RepairResult]:
        return [r for r in self.results if r.success and r.action != "skipped"]

    @property
    def skipped(self) -> list[RepairResult]:
        return [r for r in self.results if r.action == "skipped"]

    @property
    def failed(self) -> list[RepairResult]:
        return [r for r in self.results if not r.success]

    def summary(self) -> str:
        f, s, fail = len(self.fixed), len(self.skipped), len(self.failed)
        return f"{len(self.results)} issue(s): {f} fixed, {s} skipped, {fail} failed"
