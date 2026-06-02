"""Tests for domain/chat/wiki_tools — Phase 4.

Tools are tested by calling the functions directly with a mock RunContext.
Actual agent routing (which tool the LLM chooses to call) requires a real
LLM and is covered by manual / E2E testing.
"""

from unittest.mock import patch

from domain.chat.wiki_tools import file_to_wiki, read_wiki_page, search_wiki_fts, save_to_wiki
from domain.tools.wiki_fs import create_page
from tests.helpers.fake_llm import FakeLLMClient
from tests.helpers.workspace import WorkspaceFixture

_CONTENT = (
    "# Federal Reserve\n\n"
    "The Federal Reserve System is the central banking system of the United States. "
    "It conducts monetary policy, supervises banks, and maintains financial stability. "
    "Key tools include interest rate adjustments and quantitative easing programs.\n"
)

_STRUCTURED = (
    "---\n"
    "tags: [concept]\n"
    "sources: [chat]\n"
    "---\n\n"
    "# Federal Reserve\n\n"
    "## Definition\n\nThe Federal Reserve System is the central banking system of the US.\n\n"
    "## Key Characteristics\n\n- Monetary policy\n- Bank supervision\n\n"
    "## Context\n\nCentral bank operating since 1913.\n\n"
    "## Sources\n\n- [^1]: Chat synthesis\n"
)


class _Ctx:
    """Minimal RunContext mock — tools only need ctx.deps."""
    def __init__(self, deps: str) -> None:
        self.deps = deps


# ── 4.1: read_wiki_page ───────────────────────────────────────────────────────

def test_read_wiki_page_returns_index(tmp_workspace: WorkspaceFixture) -> None:
    result = read_wiki_page(_Ctx(tmp_workspace.db_path), "wiki/index.md")
    assert "Wiki Index" in result


def test_read_wiki_page_prefixes_citation_anchor(tmp_workspace: WorkspaceFixture) -> None:
    """Contract: read_wiki_page returns an inline attribution header so the agent
    has a citation anchor next to the content. Without it, wiki-first answers
    arrive uncited. See docs/manual_test_plan.md §7."""
    result = read_wiki_page(_Ctx(tmp_workspace.db_path), "/wiki/index.md")
    # Canonical path, leading slash normalised away, content still present.
    assert result.startswith("[wiki page: wiki/index.md]")
    assert "Wiki Index" in result


def test_read_wiki_page_returns_concept_content(tmp_workspace: WorkspaceFixture) -> None:
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "federal-reserve", "Federal Reserve", _CONTENT, [])
    result = read_wiki_page(_Ctx(tmp_workspace.db_path), "wiki/concepts/federal-reserve.md")
    assert "Federal Reserve System" in result


def test_read_wiki_page_not_found(tmp_workspace: WorkspaceFixture) -> None:
    result = read_wiki_page(_Ctx(tmp_workspace.db_path), "wiki/concepts/ghost.md")
    assert "not found" in result.lower()


def test_read_wiki_page_with_leading_slash(tmp_workspace: WorkspaceFixture) -> None:
    result = read_wiki_page(_Ctx(tmp_workspace.db_path), "/wiki/index.md")
    assert "Wiki Index" in result


def test_read_wiki_page_rejects_parent_traversal(tmp_workspace: WorkspaceFixture) -> None:
    """M1: a crafted '../' path must not read files outside the wiki tree."""
    secret = tmp_workspace.workspace / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")

    result = read_wiki_page(_Ctx(tmp_workspace.db_path), "wiki/../secret.txt")
    assert "Invalid path" in result
    assert "TOP SECRET" not in result


def test_read_wiki_page_rejects_deep_traversal(tmp_workspace: WorkspaceFixture) -> None:
    """Even multi-level traversal escaping the workspace is rejected."""
    result = read_wiki_page(_Ctx(tmp_workspace.db_path), "wiki/../../../../etc/hosts")
    assert "Invalid path" in result


# ── 4.1: search_wiki_fts ─────────────────────────────────────────────────────

def test_search_wiki_fts_finds_content(tmp_workspace: WorkspaceFixture) -> None:
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "federal-reserve", "Federal Reserve", _CONTENT, [])
    result = search_wiki_fts(_Ctx(tmp_workspace.db_path), "monetary policy")
    assert "result" in result.lower()
    assert "federal-reserve" in result


def test_search_wiki_fts_no_results(tmp_workspace: WorkspaceFixture) -> None:
    result = search_wiki_fts(_Ctx(tmp_workspace.db_path), "xyzzy_impossible_term")
    assert "No wiki pages found" in result


def test_search_wiki_fts_respects_wiki_scope(tmp_workspace: WorkspaceFixture) -> None:
    """search_wiki_fts must not return source-kind documents."""
    import uuid
    from domain.tools.db import get_connection

    # Insert a source doc with the same content
    doc_id = str(uuid.uuid4())
    with get_connection(tmp_workspace.db_path) as conn:
        user_id = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()["user_id"]
        with conn:
            conn.execute(
                "INSERT INTO documents "
                "(id, user_id, filename, title, path, relative_path, source_kind, "
                "file_type, status, content, document_number) "
                "VALUES (?,?,?,?,'sources/','sources/paper.pdf','source','pdf','ready',?,1)",
                (doc_id, user_id, "paper.pdf", "Paper", _CONTENT),
            )
            conn.execute(
                "INSERT INTO document_chunks "
                "(id, document_id, chunk_index, content, page, start_char, token_count) "
                "VALUES (?,?,0,?,1,0,50)",
                (str(uuid.uuid4()), doc_id, _CONTENT),
            )

    result = search_wiki_fts(_Ctx(tmp_workspace.db_path), "monetary policy")
    # No wiki pages exist — source doc must not appear
    assert "No wiki pages found" in result


# ── 4.3: file_to_wiki ────────────────────────────────────────────────────────

def test_file_to_wiki_creates_new_concept_page(tmp_workspace: WorkspaceFixture) -> None:
    with patch("domain.ingestion.wiki_generator.structure_chat_content", return_value=_STRUCTURED):
        result = file_to_wiki(
            _Ctx(tmp_workspace.db_path),
            title="Yield Curve Analysis",
            content=_CONTENT,
            category="concept",
        )
    assert "Created" in result
    assert (tmp_workspace.workspace / "wiki" / "concepts" / "yield-curve-analysis.md").exists()


def test_file_to_wiki_updates_existing_page(tmp_workspace: WorkspaceFixture) -> None:
    _update = _STRUCTURED + "\n## Update\n\nNew information added by the agent.\n"
    with patch("domain.ingestion.wiki_generator.structure_chat_content", return_value=_STRUCTURED):
        file_to_wiki(_Ctx(tmp_workspace.db_path), "Yield Curve", _CONTENT, "concept")
    with patch("domain.ingestion.wiki_generator.structure_chat_content", return_value=_update):
        result = file_to_wiki(
            _Ctx(tmp_workspace.db_path),
            title="Yield Curve",
            content="## Update\n\nNew information added by the agent.\n",
            category="concept",
        )
    assert "Updated" in result
    on_disk = (tmp_workspace.workspace / "wiki" / "concepts" / "yield-curve.md").read_text()
    assert "New information added" in on_disk
    assert "Definition" in on_disk  # structured content preserved


def test_file_to_wiki_adds_to_index(tmp_workspace: WorkspaceFixture) -> None:
    with patch("domain.ingestion.wiki_generator.structure_chat_content", return_value=_STRUCTURED):
        file_to_wiki(
            _Ctx(tmp_workspace.db_path),
            title="Yield Curve",
            content=_CONTENT,
            category="concept",
        )
    index = (tmp_workspace.workspace / "wiki" / "index.md").read_text()
    assert "yield-curve" in index.lower()


def test_file_to_wiki_page_searchable_via_fts(tmp_workspace: WorkspaceFixture) -> None:
    with patch("domain.ingestion.wiki_generator.structure_chat_content", return_value=_STRUCTURED):
        file_to_wiki(
            _Ctx(tmp_workspace.db_path),
            title="Yield Curve",
            content=_CONTENT,
            category="concept",
        )
    result = search_wiki_fts(_Ctx(tmp_workspace.db_path), "monetary policy")
    assert "result" in result.lower()


def test_file_to_wiki_structured_output_on_disk(tmp_workspace: WorkspaceFixture) -> None:
    """Structured content (frontmatter + sections) must be written to disk."""
    with patch("domain.ingestion.wiki_generator.structure_chat_content", return_value=_STRUCTURED):
        file_to_wiki(_Ctx(tmp_workspace.db_path), "Yield Curve", _CONTENT, "concept")
    on_disk = (tmp_workspace.workspace / "wiki" / "concepts" / "yield-curve.md").read_text()
    assert "tags: [concept]" in on_disk
    assert "## Definition" in on_disk
    assert "## Key Characteristics" in on_disk
    assert "## Sources" in on_disk


# ── 4.2: agent configuration ──────────────────────────────────────────────────

def test_agent_has_only_read_tools_no_autonomous_save() -> None:
    """The agent is built with read-only tools and CANNOT write wiki pages itself.

    Product decision — creating/updating a wiki page is the user's explicit action
    via the "Save to wiki" form (read_app.py → save_to_wiki). This guards against
    re-registering an autonomous write tool (e.g. file_to_wiki) on the agent.
    """
    from domain.chat.agent import create_agent

    agent = create_agent(
        base_url="http://localhost",
        api_key="test",
        model="fake",
    )
    tool_names = set(agent._function_toolset.tools.keys())
    assert "read_wiki_page" in tool_names
    assert "search_wiki_fts" in tool_names
    assert "search_source_chunks" in tool_names
    assert "file_to_wiki" not in tool_names


def test_system_prompt_contains_routing_instructions() -> None:
    from domain.chat.config import _DEFAULT_SYSTEM_PROMPT

    assert "wiki/index.md" in _DEFAULT_SYSTEM_PROMPT
    assert "search_wiki_fts" in _DEFAULT_SYSTEM_PROMPT
    assert "search_source_chunks" in _DEFAULT_SYSTEM_PROMPT


def test_system_prompt_directs_save_to_the_form_not_autonomous() -> None:
    """Contract: on a save request the agent proposes a title/category and points the
    user at the Save form — it never claims to write pages itself. Guards the
    user-controlled save flow (read_app.py 'Save last response to wiki' form)."""
    from domain.chat.config import _DEFAULT_SYSTEM_PROMPT

    # Must reference the actual form button label so guidance stays in sync with the UI.
    assert "💾 Save to wiki" in _DEFAULT_SYSTEM_PROMPT
    # Must not instruct the agent to call a write tool.
    assert "file_to_wiki" not in _DEFAULT_SYSTEM_PROMPT
    prompt = _DEFAULT_SYSTEM_PROMPT.lower()
    assert "category" in prompt  # agent proposes Concept vs Summary
    assert "never write to the wiki yourself" in prompt


def test_system_prompt_enforces_strict_grounding_and_citations() -> None:
    """Contract: the default agent is corpus-only and traceable.

    Product decision — answers must come from the user's knowledge base (never
    world knowledge) and every fact must be cited. This test guards against the
    grounding/citation mandate being silently softened. See docs/manual_test_plan.md
    §7 and the strict-default rationale.
    """
    from domain.chat.config import _DEFAULT_SYSTEM_PROMPT

    prompt = _DEFAULT_SYSTEM_PROMPT.lower()
    # Must forbid answering from the model's own knowledge.
    assert "world knowledge" in prompt
    assert "never answer from memory" in prompt
    # Must require retrieval before answering and mandatory citations.
    assert "retriev" in prompt  # "retrieve"/"retrieval"
    assert "cite" in prompt


# ── save_to_wiki ──────────────────────────────────────────────────────────────

_LONG = "word " * 50


def test_save_to_wiki_creates_page(tmp_workspace: WorkspaceFixture) -> None:
    fake = FakeLLMClient(response_content=_STRUCTURED)
    result = save_to_wiki(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "My Concept", f"# My Concept\n\n{_LONG}", "concept",
        client=fake, model="fake",
    )
    assert "Created" in result
    assert (tmp_workspace.workspace / "wiki" / "concepts" / "my-concept.md").exists()
    assert "wiki/concepts/my-concept.md" in result


def test_save_to_wiki_updates_if_exists(tmp_workspace: WorkspaceFixture) -> None:
    _update = _STRUCTURED + "\n## Extra section\n\nMore content.\n"
    fake_create = FakeLLMClient(response_content=_STRUCTURED)
    save_to_wiki(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "My Concept", f"# My Concept\n\n{_LONG}", "concept",
        client=fake_create, model="fake",
    )
    fake_update = FakeLLMClient(response_content=_update)
    result = save_to_wiki(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "My Concept", "\n## Extra section\n\nMore content.", "concept",
        client=fake_update, model="fake",
    )
    assert "Updated" in result
    text = (tmp_workspace.workspace / "wiki" / "concepts" / "my-concept.md").read_text()
    assert "Extra section" in text


def test_save_to_wiki_calls_structure_pass(tmp_workspace: WorkspaceFixture) -> None:
    """structure_chat_content must be called exactly once per save."""
    fake = FakeLLMClient(response_content=_STRUCTURED)
    save_to_wiki(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "My Concept", _CONTENT, "concept",
        client=fake, model="fake",
    )
    assert len(fake.calls) == 1
    # Verify the user message includes the title
    user_msg = fake.calls[0]["messages"][-1]["content"]
    assert "My Concept" in user_msg


def test_save_to_wiki_structured_output_on_disk(tmp_workspace: WorkspaceFixture) -> None:
    """YAML frontmatter and standard sections must reach disk."""
    fake = FakeLLMClient(response_content=_STRUCTURED)
    save_to_wiki(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "My Concept", _CONTENT, "concept",
        client=fake, model="fake",
    )
    text = (tmp_workspace.workspace / "wiki" / "concepts" / "my-concept.md").read_text()
    assert "tags: [concept]" in text
    assert "## Definition" in text
    assert "## Sources" in text
