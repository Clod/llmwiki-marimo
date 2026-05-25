"""Tests for the tmp_workspace fixture and FakeLLMClient."""

from domain.tools.db import get_connection
from tests.helpers.fake_llm import FakeLLMClient
from tests.helpers.workspace import WorkspaceFixture


def test_tmp_workspace_creates_directory_tree(tmp_workspace: WorkspaceFixture) -> None:
    assert (tmp_workspace.workspace / "sources").is_dir()
    assert (tmp_workspace.workspace / "wiki").is_dir()
    assert (tmp_workspace.workspace / ".llmwiki").is_dir()


def test_tmp_workspace_db_has_workspace_row(tmp_workspace: WorkspaceFixture) -> None:
    with get_connection(tmp_workspace.db_path) as conn:
        row = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()
    assert row is not None
    assert row["user_id"] == "test-user"


def test_tmp_workspace_db_has_expected_tables(tmp_workspace: WorkspaceFixture) -> None:
    with get_connection(tmp_workspace.db_path) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"workspace", "documents", "document_chunks", "document_references"} <= tables


def test_fake_llm_returns_default_content(tmp_workspace: WorkspaceFixture) -> None:
    result = tmp_workspace.llm.chat.completions.create(
        model="fake", messages=[{"role": "user", "content": "hello"}]
    )
    assert "Fake Wiki Page" in result.choices[0].message.content


def test_fake_llm_returns_configured_content(tmp_workspace: WorkspaceFixture) -> None:
    tmp_workspace.llm.response_content = "## Custom Response"
    result = tmp_workspace.llm.chat.completions.create(
        model="fake", messages=[{"role": "user", "content": "hello"}]
    )
    assert result.choices[0].message.content == "## Custom Response"


def test_fake_llm_records_calls(tmp_workspace: WorkspaceFixture) -> None:
    tmp_workspace.llm.chat.completions.create(
        model="gpt-4", messages=[{"role": "user", "content": "test"}]
    )
    assert len(tmp_workspace.llm.calls) == 1
    assert tmp_workspace.llm.calls[0]["model"] == "gpt-4"


def test_each_fixture_gets_independent_workspace(
    tmp_workspace: WorkspaceFixture, tmp_path
) -> None:
    # A second fixture call (simulated via tmp_path) produces a different path
    assert tmp_workspace.workspace.parent == tmp_path


def test_fake_llm_standalone() -> None:
    client = FakeLLMClient(response_content="hello")
    resp = client.chat.completions.create(model="x", messages=[])
    assert resp.choices[0].message.content == "hello"
    assert len(client.calls) == 1
