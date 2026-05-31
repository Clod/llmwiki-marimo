"""Git operations for the wiki workspace.

Auto-commit can be disabled by setting WIKI_AUTOCOMMIT to a falsy value
(0/false/no/off) in the environment — then LLM Wiki touches git not at all
(no init, no commit) and the user manages the wiki's git history themselves.
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


def autocommit_enabled() -> bool:
    """True unless WIKI_AUTOCOMMIT is explicitly set to a falsy value."""
    return os.environ.get("WIKI_AUTOCOMMIT", "1").strip().lower() not in _AUTOCOMMIT_OFF


def init_wiki_repo(workspace: Path) -> None:
    """Initialize workspace as a git repo if not already. Creates .gitignore.

    No-op when WIKI_AUTOCOMMIT is disabled (git left entirely to the user).
    """
    if not autocommit_enabled():
        return

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
    """Stage all wiki/ changes and commit. Silent if nothing to commit.

    No-op when WIKI_AUTOCOMMIT is disabled.
    """
    if not autocommit_enabled():
        logger.debug("WIKI_AUTOCOMMIT disabled — skipping commit: %s", message)
        return

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
