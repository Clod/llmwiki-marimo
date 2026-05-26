"""repair_wiki() — run appropriate repair action for each issue in a LintReport."""

import logging
from pathlib import Path

from domain.lint.report import LintReport
from .actions import (
    repair_contradiction,
    repair_data_gap,
    repair_gap_filled,
    repair_missing_concept,
    repair_missing_xref,
    repair_orphan,
    repair_stale,
)
from .report import RepairReport, RepairResult

logger = logging.getLogger(__name__)

_NEEDS_LLM = {"stale", "missing_concept"}
_DISPATCH = {
    "orphan":           repair_orphan,
    "stale":            repair_stale,
    "missing_xref":     repair_missing_xref,
    "missing_concept":  repair_missing_concept,
    "contradiction":    repair_contradiction,
    "data_gap":         repair_data_gap,
    "gap_filled":       repair_gap_filled,
}


def repair_wiki(
    lint_report: LintReport,
    db_path: str,
    workspace: Path,
    llm_client=None,
    model: str = "",
    progress_cb=None,
) -> RepairReport:
    """Apply automatic repairs for each issue in lint_report.

    LLM-dependent repairs (stale, missing_concept) are skipped when
    llm_client is None.

    Args:
        lint_report: Output of lint_wiki().
        db_path:     Path to the SQLite database.
        workspace:   Path to the workspace root.
        llm_client:  OpenAI-compatible client. Pass None to skip LLM repairs.
        model:       Model identifier string.
        progress_cb: Optional callable(str) for live progress messages.

    Returns:
        RepairReport with one RepairResult per issue.
    """
    def _cb(msg: str) -> None:
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    report = RepairReport()

    if not lint_report.issues:
        _cb("✅ No issues to repair.")
        return report

    _cb(f"🔧 Repairing {len(lint_report.issues)} issue(s)...")

    for issue in lint_report.issues:
        handler = _DISPATCH.get(issue.check)
        if handler is None:
            result = RepairResult(
                check=issue.check, page=issue.page,
                action="skipped", success=True,
                message=f"Unknown check type: {issue.check}",
            )
        elif issue.check in _NEEDS_LLM and llm_client is None:
            result = RepairResult(
                check=issue.check, page=issue.page,
                action="skipped", success=True,
                message=f"LLM client required for '{issue.check}' repair — pass llm_client",
            )
        elif issue.check in _NEEDS_LLM:
            result = handler(issue, db_path, workspace, llm_client, model)
        else:
            result = handler(issue, db_path, workspace)

        report.results.append(result)
        icon = "✅" if result.success and result.action != "skipped" else \
               "⏭️" if result.action == "skipped" else "❌"
        _cb(f"  {icon} [{issue.check}] {result.action}: {result.message}")

    _cb(f"🏁 {report.summary()}")
    return report
