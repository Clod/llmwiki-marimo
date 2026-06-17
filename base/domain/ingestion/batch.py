"""Batch ingestion: process multiple files with deferred expensive operations.

Per-file: extract, chunk, concept pages, summary page, references, index.
Once at end: overview rewrite, log entry, git commit, optional lint.

PURPOSE FOR BEGINNERS:
When a user uploads 10 documents to the Wiki, running the full pipeline individually
for each file can be extremely slow. Each individual ingestion would write logs,
trigger expensive LLM overview rewrites, and create separate Git commits.

This module acts as a "Batch Coordinator". It processes files one-by-one in a light
"batch mode" (skipping the heavy global updates), and then executes those expensive
tasks (LLM overview updates, logs, lint reports, Git commits) exactly ONCE at the
very end. This speeds up processing significantly and keeps the git history clean!
"""

# Import the standard logging library to log batch progress and alerts
import logging
# Import datetime and timezone to generate UTC timestamps for the log files
from datetime import datetime, timezone
# Import Path to handle cross-platform directory paths
from pathlib import Path
# Import Callable to define standard signature types for callback functions
from typing import Callable

# Import core pipeline elements:
# - IngestResult: Dataclass tracking success/fail status for a file
# - _all_concept_names: Fetch list of all known wiki concepts from DB
# - _init_wiki_workspace: Prepares folders like wiki/concepts/ if missing
# - ingest_file: The standard single-file ingestion executor
from . import trace
from .pipeline import IngestResult, _all_concept_names, _init_wiki_workspace, ingest_file
# Import the function that uses LLMs to structure a global wiki overview document
from .wiki_generator import update_overview
# Import filesystem helper to write/append log lines to a page on disk
from domain.tools.wiki_fs import append_to_page
# Import git utilities to initialize tracking and commit all newly updated files
from domain.tools.git_ops import init_wiki_repo, auto_commit

# Initialize a logger for this module
logger = logging.getLogger(__name__)


def batch_ingest(
    files: list[Path],
    db_path: str,
    workspace: Path,
    llm_client,
    model: str,
    progress_cb: Callable[[str], None] | None = None,
    run_lint: bool = False,
    language: str = "en",
) -> list[IngestResult]:
    """Ingest multiple files, deferring overview/log/git to the end of the batch.

    Concept pages compound across files — if two documents mention the same
    concept, the second ingest updates the existing concept page rather than
    creating a duplicate.

    Args:
        files: List of file paths to ingest.
        db_path: Path to the SQLite database.
        workspace: Path to the wiki workspace root.
        llm_client: OpenAI-compatible LLM client.
        model: Model identifier string.
        progress_cb: Optional callback function to report progress back to a UI.
        run_lint: Run deterministic lint checks at the end and append to log.
        language: ISO 639-1 code controlling the language of generated content.

    Returns:
        List of IngestResult (one per file, in order).
    """

    # Internal helper function to route progress update messages
    # both to standard console logs and to the optional UI progress callback
    def _cb(msg: str) -> None:
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    # 1. Initialize the workspace structure (ensuring /wiki/, /wiki/concepts/ etc exist)
    _init_wiki_workspace(workspace, language)
    _cb(f"📦 Batch ingesting {len(files)} file(s)...")

    # Own a single trace run for the whole batch so every file (and the batch
    # overview LLM call) lands in one trace.jsonl. No-op when WIKI_TRACE is unset.
    # The `with` guarantees the run is finalized even if a later step raises.
    with trace.run_scope(workspace, db_path) as tracer:
        llm_client = tracer.wrap(llm_client)

        results: list[IngestResult] = []
        summaries: list[str] = []

        # 2. Iterate through each file and process it
        for i, file_path in enumerate(files, 1):
            # Report progress (e.g. "[1/5] report.pdf")
            _cb(f"[{i}/{len(files)}] {file_path.name}")

            # Execute ingestion for this single file.
            # We pass `_batch_mode=True` to tell the pipeline:
            # "Don't run global updates or git commits yet—I will handle those at the end."
            result = ingest_file(
                file_path, db_path, workspace, llm_client, model,
                progress_cb=progress_cb,
                _batch_mode=True,
                language=language,
            )
            results.append(result)

            # If the file was successfully processed, add its name to our summaries
            if result.status == "ingested":
                summaries.append(file_path.name)

        # 3. If no files were actually ingested (e.g., they were all skipped or failed),
        #    we terminate early to avoid useless Git commits or overview rewrites.
        if not summaries:
            _cb("⚠️  No files were ingested — skipping overview and commit.")
            return results

        # ── Overview rewrite (once for all files) ────────────────────────────────
        # Rather than updating the overview page 10 times (causing massive LLM cost/time),
        # we rewrite the global wiki introduction exactly once using the combined summaries.
        _cb("🌐 Rewriting overview for batch...")
        try:
            with tracer.stage("overview"):
                # A. Read the current overview markdown content
                current_overview = (workspace / "wiki" / "overview.md").read_text(encoding="utf-8")

                # B. Formulate a combined summary line of the batch changes
                combined_summary = f"Batch ingested {len(summaries)} file(s): {', '.join(summaries)}"

                # C. Retrieve all existing concept names from the DB to support linking
                all_concept_names = _all_concept_names(db_path)

                # D. Request the LLM to update and re-structure the wiki overview document
                new_overview = update_overview(
                    current_overview, combined_summary, all_concept_names, llm_client, model,
                    language=language,
                )
                # E. Save the updated overview back to disk
                (workspace / "wiki" / "overview.md").write_text(new_overview, encoding="utf-8")
                tracer.artifact(
                    "markdown", "overview", new_overview, ext="md",
                    relative_path="wiki/overview.md",
                )
        except Exception as exc:
            logger.warning("Batch overview update failed: %s", exc)

        # ── Optional lint ─────────────────────────────────────────────────────────
        # If run_lint is True, validate the entire wiki folder structure for broken links,
        # orphaned files, or configuration issues before finalizing.
        if run_lint:
            try:
                from domain.lint.runner import lint_wiki
                _cb("🩺 Running lint on batch result...")
                lint_report = lint_wiki(db_path, workspace)
                _cb(f"🩺 Lint: {lint_report.summary()}")
            except Exception as exc:
                logger.warning("Batch lint failed: %s", exc)
                lint_report = None
        else:
            lint_report = None

        # ── Single log entry for the whole batch ─────────────────────────────────
        # Group results into status categories to print a beautiful markdown log section.
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        ingested = [r for r in results if r.status == "ingested"]
        skipped  = [r for r in results if r.status == "skipped"]
        failed   = [r for r in results if r.status == "failed"]

        # Build the markdown content block
        log_lines = [
            f"## [{timestamp}] Batch ingested | {len(files)} file(s)\n",
            f"- Ingested: {len(ingested)} | Skipped: {len(skipped)} | Failed: {len(failed)}\n",
        ]
        # List successful files
        for r in ingested:
            log_lines.append(f"  - ✅ {r.file_path.name}\n")
        # List failed files along with their specific error messages
        for r in failed:
            log_lines.append(f"  - ❌ {r.file_path.name}: {r.message}\n")
        # If linting was performed and issues were found, add the summary to the log
        if lint_report and lint_report.issues:
            log_lines.append(f"- Lint: {lint_report.summary()}\n")

        # Append this structured markdown block into "/wiki/log.md" on disk and in the DB
        append_to_page(db_path, workspace, "/wiki/", "log", "".join(log_lines))

        # ── Single git commit ─────────────────────────────────────────────────────
        # Make a single git snapshot commit containing all updated/inserted files
        # to record this batch operation.
        init_wiki_repo(workspace)
        auto_commit(workspace, f"batch ingest: {len(ingested)} file(s)")

        ingested_count = len(ingested)
        _cb(f"🏁 Batch complete — ingested: {ingested_count}, skipped: {len(skipped)}, failed: {len(failed)}")
        return results
