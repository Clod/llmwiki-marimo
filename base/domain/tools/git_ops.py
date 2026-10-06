"""Git operations for the wiki workspace.

Auto-commit can be disabled by setting WIKI_AUTOCOMMIT to a falsy value
(0/false/no/off) in the environment — then LLM Wiki touches git not at all
(no init, no commit) and the user manages the wiki's git history themselves.

git is an OPTIONAL dependency: if the `git` binary is missing or a git command
fails, the version-history snapshot is skipped with a one-time warning. It never
fails an otherwise-successful ingest — the commit is a convenience, not core.
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_GITIGNORE = """.llmwiki/
*.pyc
__pycache__/
"""

_AUTOCOMMIT_OFF = {"0", "false", "no", "off"}

# Git failures (missing binary, command error) are swallowed so they can't fail
# an ingest. Warn only once per process so a batch run doesn't spam the log.
_git_warned = False


def autocommit_enabled() -> bool:
    """True unless WIKI_AUTOCOMMIT is explicitly set to a falsy value."""
    return os.environ.get("WIKI_AUTOCOMMIT", "1").strip().lower() not in _AUTOCOMMIT_OFF


def _warn_git_unavailable(exc: Exception) -> None:
    global _git_warned
    if not _git_warned:
        logger.warning(
            "git unavailable (%s) — skipping wiki version snapshots. Install git "
            "for history, or set WIKI_AUTOCOMMIT=0 to silence this.", exc,
        )
        _git_warned = True


def init_wiki_repo(workspace: Path) -> None:
    """Initialize workspace as a git repo if not already. Creates .gitignore.

    No-op when WIKI_AUTOCOMMIT is disabled (git left entirely to the user), and a
    graceful skip (warning, no raise) when git is unavailable.
    """
    if not autocommit_enabled():
        return

    try:
        git_dir = workspace / ".git"
        if not git_dir.exists():
            _run(["git", "init"], workspace)
            _run(["git", "config", "user.email", "llmwiki@local"], workspace)
            _run(["git", "config", "user.name", "LLM Wiki"], workspace)
            logger.info("Initialized git repo at %s", workspace)

        gitignore = workspace / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(_GITIGNORE, encoding="utf-8")
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        _warn_git_unavailable(exc)


def auto_commit(workspace: Path, message: str) -> None:
    """Stage wiki/, .gitignore and wiki_config.toml, and commit. Silent if nothing to commit.

    No-op when WIKI_AUTOCOMMIT is disabled; graceful skip when git is unavailable.
    A git failure here never propagates — it must not fail an ingest.
    """
    if not autocommit_enabled():
        logger.debug("WIKI_AUTOCOMMIT disabled — skipping commit: %s", message)
        return

    try:
        # wiki_config.toml is part of a point: its vocabulary lists are edited from
        # the Vocabulario screen, and going back to a point restores it.
        paths = ["wiki/", ".gitignore"] + (["wiki_config.toml"] if (workspace / "wiki_config.toml").exists() else [])
        _run(["git", "add", *paths], workspace)
        result = _run(
            ["git", "commit", "-m", message],
            workspace,
            check=False,
        )
        if result.returncode == 0:
            logger.info("Git commit: %s", message)
            _snapshot_index(workspace)
        else:
            logger.debug("Nothing to commit: %s", message)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        _warn_git_unavailable(exc)


def head_sha(workspace: Path) -> str | None:
    """The full sha of HEAD, or None when there is no commit or git is unavailable."""
    try:
        result = _run(["git", "rev-parse", "--verify", "-q", "HEAD"], workspace, check=False)
    except FileNotFoundError:
        return None
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else None


def _snapshot_index(workspace: Path) -> None:
    """Pair the commit just made with a copy of the index, and prune the old copies.

    The index already matches the pages at commit time. A failure here is logged
    and swallowed: the operation that committed has succeeded and must not fail.
    """
    try:
        from domain.rollback import snapshots

        if not snapshots.snapshots_enabled():
            return
        sha = head_sha(workspace)
        if sha:
            snapshots.capture(workspace, sha)
            snapshots.prune(workspace, snapshots.snapshot_keep())
    except Exception:  # noqa: BLE001 — the snapshot is a cache, never a reason to fail
        logger.warning("Index snapshot failed; the commit stands", exc_info=True)


def _run(
    cmd: list[str],
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )
