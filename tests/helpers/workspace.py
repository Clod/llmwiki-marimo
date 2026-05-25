"""Pytest fixture for a clean, isolated test workspace."""

import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass
class WorkspaceFixture:
    workspace: Path
    db_path: str
    llm: object  # FakeLLMClient


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> WorkspaceFixture:
    """Create a fresh isolated workspace with an initialized DB for each test."""
    from domain.tools.db import open_db
    from tests.helpers.fake_llm import FakeLLMClient

    workspace = tmp_path / "workspace"
    (workspace / "sources").mkdir(parents=True)
    (workspace / "wiki" / "summaries").mkdir(parents=True)
    (workspace / "wiki" / "concepts").mkdir(parents=True)
    (workspace / ".llmwiki").mkdir(parents=True)

    (workspace / "wiki" / "index.md").write_text(
        "# Wiki Index\n\n## Summaries\n\n## Concepts\n", encoding="utf-8"
    )
    (workspace / "wiki" / "overview.md").write_text(
        "# Knowledge Base Overview\n\n_No documents ingested yet._\n", encoding="utf-8"
    )
    (workspace / "wiki" / "log.md").write_text(
        "# Ingest Log\n\n", encoding="utf-8"
    )

    db_path = str(workspace / ".llmwiki" / "index.db")
    conn = open_db(db_path)
    conn.execute(
        "INSERT INTO workspace (id, name, user_id) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), "test-workspace", "test-user"),
    )
    conn.commit()
    conn.close()

    return WorkspaceFixture(
        workspace=workspace,
        db_path=db_path,
        llm=FakeLLMClient(),
    )
