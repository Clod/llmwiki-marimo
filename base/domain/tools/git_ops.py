"""Git operations for the wiki workspace."""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_GITIGNORE = """.llmwiki/
*.pyc
__pycache__/
"""


def init_wiki_repo(workspace: Path) -> None:
    """Initialize workspace as a git repo if not already. Creates .gitignore."""
    git_dir = workspace / ".git"
    if not git_dir.exists():
        _run(["git", "init"], workspace)
        _run(["git", "config", "user.email", "llmwiki@local"], workspace)
        _run(["git", "config", "user.name", "LLM Wiki"], workspace)
        logger.info("Initialized git repo at %s", workspace)

    gitignore = workspace / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(_GITIGNORE, encoding="utf-8")


def auto_commit(workspace: Path, message: str) -> None:
    """Stage all wiki/ changes and commit. Silent if nothing to commit."""
    _run(["git", "add", "wiki/", ".gitignore"], workspace)
    result = _run(
        ["git", "commit", "-m", message],
        workspace,
        check=False,
    )
    if result.returncode == 0:
        logger.info("Git commit: %s", message)
    else:
        logger.debug("Nothing to commit: %s", message)


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
