"""Lint orchestrator."""

from datetime import datetime, timezone
from pathlib import Path

from .checks import (
    contradiction_check,
    data_gap_check,
    gap_filled_check,
    missing_concept_check,
    missing_xref_check,
    orphan_check,
    staleness_check,
)
from .report import LintReport


def lint_wiki(
    db_path: str,
    workspace: Path,
    client=None,
    model: str = "",
) -> LintReport:
    """Run all lint checks and return a structured report.

    LLM-powered checks (contradiction, data_gap) are skipped when client is None.
    """
    issues = []
    issues.extend(orphan_check(db_path))
    issues.extend(staleness_check(db_path))
    issues.extend(missing_xref_check(db_path))
    issues.extend(missing_concept_check(db_path, workspace))
    issues.extend(gap_filled_check(db_path))
    if client:
        issues.extend(contradiction_check(db_path, workspace, client, model))
        issues.extend(data_gap_check(db_path, workspace, client, model))

    return LintReport(
        issues=issues,
        checked_at=datetime.now(timezone.utc).isoformat(),
    )
