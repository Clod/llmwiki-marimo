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
    repair_vocab_collision,
)
from .report import RepairReport, RepairResult

logger = logging.getLogger(__name__)

_NEEDS_LLM = {"stale", "missing_concept"}
# Handlers that accept a `language` kwarg to localize generated headers/content.
# Other checks emit only diagnostic annotations and stay English in v1.
_NEEDS_LANG = {"stale", "missing_concept", "missing_xref"}
_DISPATCH = {
    "orphan":           repair_orphan,
    "stale":            repair_stale,
    "missing_xref":     repair_missing_xref,
    "missing_concept":  repair_missing_concept,
    "contradiction":    repair_contradiction,
    "data_gap":         repair_data_gap,
    "gap_filled":       repair_gap_filled,
    "vocab_collision":  repair_vocab_collision,
}

# Known checks that surface a finding for a human but have no automatic repair
# (informational/warning only). They are skipped deliberately — NOT "unknown".
#
# `thin_page` belongs here rather than in _DISPATCH: it reports that the wiki
# under-covers a source, and its own suggestion offers two remedies — expand the
# page, or accept the Tier-2 fallback for the uncovered part. Both are judgement
# calls, and the first would need the model to write new prose, so there is
# nothing safe to do automatically.
_ADVISORY_CHECKS = {"vocab_stale", "vocab_covered", "vocab_ambiguous", "thin_page",
                    "unpaged_source"}

# Shown in the ingest app's Activity Log, which is where a human actually reads
# it — so it names the two buttons that supply a model, not the keyword argument
# that does. The previous wording ("pass llm_client") was accurate and useless:
# nobody reading a log has an argument to pass. A skip message should name what
# is missing in the reader's own terms.
_NEEDS_LLM_MESSAGE = (
    "'{check}' repair needs a model, and none was supplied. To fix these: tick "
    "\"Also run full LLM lint & repair after ingest\" before ingesting, or press "
    "\"Run Wiki Lint & Repair\" to sweep the whole wiki now."
)


def repair_wiki(
    lint_report: LintReport,
    db_path: str,
    workspace: Path,
    llm_client=None,
    model: str = "",
    progress_cb=None,
    language: str = "en",
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
        language:    ISO 639-1 wiki content language, forwarded to the
                     content/header-generating repairs (stale, missing_concept,
                     missing_xref). Defaults to English (byte-identical path).

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
            message = (
                "advisory finding — no automatic repair (resolve by hand)"
                if issue.check in _ADVISORY_CHECKS
                else f"Unknown check type: {issue.check}"
            )
            result = RepairResult(
                check=issue.check, page=issue.page,
                action="skipped", success=True,
                message=message,
            )
        elif issue.check in _NEEDS_LLM and llm_client is None:
            result = RepairResult(
                check=issue.check, page=issue.page,
                action="skipped", success=True,
                message=_NEEDS_LLM_MESSAGE.format(check=issue.check),
            )
        elif issue.check in _NEEDS_LLM:
            _lang_kw = {"language": language} if issue.check in _NEEDS_LANG else {}
            result = handler(issue, db_path, workspace, llm_client, model, **_lang_kw)
        else:
            _lang_kw = {"language": language} if issue.check in _NEEDS_LANG else {}
            result = handler(issue, db_path, workspace, **_lang_kw)

        report.results.append(result)
        icon = "✅" if result.success and result.action != "skipped" else \
               "⏭️" if result.action == "skipped" else "❌"
        _cb(f"  {icon} [{issue.check}] {result.action}: {result.message}")

    _cb(f"🏁 {report.summary()}")
    return report
