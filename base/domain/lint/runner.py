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
    thin_page_check,
    unpaged_source_check,
    vocabulary_check,
)
from .report import LintReport


def lint_wiki(
    db_path: str,
    workspace: Path,
    client=None,
    model: str = "",
    progress_cb=None,
) -> LintReport:
    """Run all lint checks and return a structured report.

    LLM-powered checks (contradiction, data_gap) are skipped when client is None.
    progress_cb (if given) is called before each check so a long run — chiefly the
    pairwise LLM contradiction sweep — reports progress instead of going silent.
    """
    def _p(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    issues = []
    _p("🔎 lint: orphans")
    issues.extend(orphan_check(db_path))
    _p("🔎 lint: staleness")
    issues.extend(staleness_check(db_path))
    _p("🔎 lint: missing cross-refs")
    issues.extend(missing_xref_check(db_path))
    _p("🔎 lint: missing concept links")
    issues.extend(missing_concept_check(db_path, workspace))
    _p("🔎 lint: gap-filled markers")
    issues.extend(gap_filled_check(db_path))
    _p("🔎 lint: vocabulary")
    issues.extend(vocabulary_check(db_path, workspace))
    _p("🔎 lint: thin pages")
    issues.extend(thin_page_check(db_path))
    _p("🔎 lint: sources with no page")
    issues.extend(unpaged_source_check(db_path))
    if client:
        _p("🔎 lint: contradiction sweep (LLM, pairwise)…")
        issues.extend(contradiction_check(db_path, workspace, client, model, progress_cb))
        _p("🔎 lint: data-gap scan (LLM)…")
        issues.extend(data_gap_check(db_path, workspace, client, model, progress_cb))

    return LintReport(
        issues=issues,
        checked_at=datetime.now(timezone.utc).isoformat(),
    )
