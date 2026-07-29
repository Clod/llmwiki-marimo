"""Tests for code-written wiki frontmatter (render_frontmatter + create_page).

Moves frontmatter emission out of the LLM prompt and into create_page — the
single choke point every page-creating path goes through. See
base/domain/datasets/frontmatter.py (render_frontmatter) and
base/domain/tools/wiki_fs.py (create_page).
"""

from domain.datasets.frontmatter import parse_frontmatter, render_frontmatter, split_frontmatter
from domain.tools.wiki_fs import create_page
from tests.helpers.workspace import WorkspaceFixture


# ── render_frontmatter ────────────────────────────────────────────────────────

def test_render_frontmatter_fixed_key_order_and_omits_empty() -> None:
    block = render_frontmatter({
        "sources": ["a.pdf"],
        "type": "concept",
        "title": "Federal Reserve",
        "tags": ["entity"],
        "aliases": None,  # omitted: None
        "extra": "",      # omitted: empty string
        "empty_list": [],  # omitted: empty list
    })
    # Fixed order: type, title, tags, sources, then remaining keys alphabetically.
    # No other keys survive here since aliases/extra/empty_list are all empty.
    lines = [line for line in block.splitlines() if line.strip()]
    keys_in_order = [line.split(":", 1)[0] for line in lines if not line.startswith("---")]
    assert keys_in_order == ["type", "title", "tags", "sources"]
    assert "aliases" not in block
    assert "extra" not in block
    assert "empty_list" not in block
    assert "tags: []" not in block


def test_render_frontmatter_title_with_colon_and_accent_roundtrips() -> None:
    fields = {"type": "concept", "title": "Política: Año 2024", "tags": ["theme"]}
    block = render_frontmatter(fields)
    fm_block, _body = split_frontmatter(block)
    assert fm_block is not None
    parsed = parse_frontmatter(fm_block)
    assert parsed == fields


# ── create_page: frontmatter derivation ──────────────────────────────────────

def test_create_page_concept_emits_type_concept(tmp_workspace: WorkspaceFixture) -> None:
    result = create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "federal-reserve", "Federal Reserve",
        "# Federal Reserve\n\nBody text.\n", ["entity"],
    )
    text = (tmp_workspace.workspace / "wiki" / "concepts" / "federal-reserve.md").read_text()
    assert text.startswith("---\n")
    fm_block, body = split_frontmatter(text)
    assert fm_block is not None
    parsed = parse_frontmatter(fm_block)
    assert parsed["type"] == "concept"
    assert parsed["title"] == "Federal Reserve"
    assert parsed["tags"] == ["entity"]
    assert "# Federal Reserve" in body
    assert result["path"] == "wiki/concepts/federal-reserve.md"


def test_create_page_summary_emits_type_summary(tmp_workspace: WorkspaceFixture) -> None:
    """Regression: the six demo summaries had no frontmatter because
    build_summary_page is pure code with no LLM to ask for it."""
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "some-doc", "Some Doc",
        "# Some Doc\n\nBody text.\n", [],
    )
    text = (tmp_workspace.workspace / "wiki" / "summaries" / "some-doc.md").read_text()
    fm_block, _body = split_frontmatter(text)
    assert fm_block is not None
    parsed = parse_frontmatter(fm_block)
    assert parsed["type"] == "summary"


def test_create_page_strips_incoming_frontmatter_no_duplication(tmp_workspace: WorkspaceFixture) -> None:
    """Frontmatter the LLM transcribed into `content` must be discarded, not
    duplicated alongside the code-rendered block."""
    llm_content = (
        "---\n"
        "tags: [concept]\n"
        "sources: [chat]\n"
        "---\n\n"
        "# My Concept\n\nBody one.\n"
    )
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "my-concept", "My Concept",
        llm_content, ["concept"],
    )
    # Overwrite with a second LLM-transcribed block to prove idempotency on re-save.
    llm_content_2 = (
        "---\n"
        "tags: [concept]\n"
        "sources: [chat]\n"
        "---\n\n"
        "# My Concept\n\nBody two, updated.\n"
    )
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "my-concept", "My Concept",
        llm_content_2, ["concept"], overwrite=True,
    )
    text = (tmp_workspace.workspace / "wiki" / "concepts" / "my-concept.md").read_text()
    assert text.count("---\n") == 2  # exactly one frontmatter block (open + close delimiter)
    assert "Body two, updated." in text
    assert "Body one." not in text


def test_create_page_accumulates_sources_existing_first(tmp_workspace: WorkspaceFixture) -> None:
    """Sources are OKF's provenance shape — a list of {"resource": ...} mappings,
    never bare strings — and accumulate existing-first, deduplicated."""
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "my-concept", "My Concept",
        "# My Concept\n\nBody.\n", ["concept"],
        sources=["a.pdf"],
    )
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "my-concept", "My Concept",
        "# My Concept\n\nUpdated body.\n", ["concept"],
        overwrite=True, sources=["b.pdf"],
    )
    text = (tmp_workspace.workspace / "wiki" / "concepts" / "my-concept.md").read_text()
    fm_block, _body = split_frontmatter(text)
    assert fm_block is not None
    parsed = parse_frontmatter(fm_block)
    assert parsed["sources"] == [
        {"resource": "sources/a.pdf"},
        {"resource": "sources/b.pdf"},
    ]


def test_create_page_replace_sources_overrides_union(tmp_workspace: WorkspaceFixture) -> None:
    """replace_sources=True makes `sources` authoritative instead of unioned with
    disk — this is what lets ingestion rollback restore a page's prior sources
    without also inheriting the source the (now rolled-back) run added."""
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "my-concept", "My Concept",
        "# My Concept\n\nBody.\n", ["concept"],
        sources=["a.pdf"],
    )
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "my-concept", "My Concept",
        "# My Concept\n\nUpdated body.\n", ["concept"],
        overwrite=True, sources=["b.pdf"], replace_sources=True,
    )
    text = (tmp_workspace.workspace / "wiki" / "concepts" / "my-concept.md").read_text()
    fm_block, _body = split_frontmatter(text)
    assert fm_block is not None
    parsed = parse_frontmatter(fm_block)
    assert parsed["sources"] == [{"resource": "sources/b.pdf"}]


# ── sources: OKF provenance shape ─────────────────────────────────────────────

def test_render_frontmatter_sources_as_list_of_mappings_block_style() -> None:
    """OKF's provenance family is a list of mappings, each requiring `resource`.
    Rendered in block style (one `- resource: ...` per entry), not inline —
    a mapping bundled inline (`[{resource: ...}]`) doesn't match any OKF example
    and is unreadable at more than one entry."""
    block = render_frontmatter({
        "type": "concept",
        "title": "My Concept",
        "sources": [
            {"resource": "sources/12 Cauciones Bursátiles.docx"},
            {"resource": "sources/07 FCI Money Market.docx"},
        ],
    })
    assert (
        "sources:\n"
        "  - resource: sources/12 Cauciones Bursátiles.docx\n"
        "  - resource: sources/07 FCI Money Market.docx\n"
    ) in block


def test_create_page_chat_source_resource_is_chat(tmp_workspace: WorkspaceFixture) -> None:
    """Chat-saved pages have no source file — "chat" is the stable identifier
    for that non-file origin, emitted as-is (no `sources/` prefix)."""
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "my-concept", "My Concept",
        "# My Concept\n\nBody.\n", ["concept"],
        sources=["chat"],
    )
    text = (tmp_workspace.workspace / "wiki" / "concepts" / "my-concept.md").read_text()
    fm_block, _body = split_frontmatter(text)
    assert fm_block is not None
    parsed = parse_frontmatter(fm_block)
    assert parsed["sources"] == [{"resource": "chat"}]


def test_create_page_dedups_sources_on_resource(tmp_workspace: WorkspaceFixture) -> None:
    """Re-adding the same source (same resolved `resource`) must not duplicate it."""
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "my-concept", "My Concept",
        "# My Concept\n\nBody.\n", ["concept"],
        sources=["a.pdf"],
    )
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "my-concept", "My Concept",
        "# My Concept\n\nUpdated body.\n", ["concept"],
        overwrite=True, sources=["a.pdf"],
    )
    text = (tmp_workspace.workspace / "wiki" / "concepts" / "my-concept.md").read_text()
    fm_block, _body = split_frontmatter(text)
    assert fm_block is not None
    parsed = parse_frontmatter(fm_block)
    assert parsed["sources"] == [{"resource": "sources/a.pdf"}]


def test_create_page_migrates_legacy_string_sources_on_union(tmp_workspace: WorkspaceFixture) -> None:
    """Every page on disk before this format existed (all of examples/, plus
    anything an earlier build of create_page wrote) has `sources` as bare
    strings. Reading one must not crash, and unioning a new source onto it must
    normalize the legacy entry rather than writing a mixed-type list."""
    legacy_content = (
        "---\n"
        "type: concept\n"
        "title: My Concept\n"
        "tags: [concept]\n"
        "sources: [a.pdf]\n"  # legacy bare-string shape
        "---\n\n"
        "# My Concept\n\nBody.\n"
    )
    page_path = tmp_workspace.workspace / "wiki" / "concepts" / "my-concept.md"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(legacy_content, encoding="utf-8")

    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "my-concept", "My Concept",
        "# My Concept\n\nUpdated body.\n", ["concept"],
        overwrite=True, sources=["b.pdf"],
    )
    text = page_path.read_text()
    fm_block, _body = split_frontmatter(text)
    assert fm_block is not None
    parsed = parse_frontmatter(fm_block)
    assert parsed["sources"] == [
        {"resource": "sources/a.pdf"},
        {"resource": "sources/b.pdf"},
    ]


def test_create_page_replace_sources_migrates_legacy_string_from_content(
    tmp_workspace: WorkspaceFixture,
) -> None:
    """The replace_sources path parses `sources` out of `content`'s own
    frontmatter (this is what ingestion rollback relies on to restore prior
    sources — see _rollback_wiki_pages, pipeline.py) — a rollback snapshot may
    itself be a legacy page, so this must migrate the bare-string shape too."""
    legacy_snapshot = (
        "---\n"
        "type: concept\n"
        "title: My Concept\n"
        "tags: [concept]\n"
        "sources: [prior.pdf]\n"  # legacy bare-string shape
        "---\n\n"
        "# My Concept\n\nRestored body.\n"
    )
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "my-concept", "My Concept",
        legacy_snapshot, ["concept"],
        replace_sources=True,
    )
    text = (tmp_workspace.workspace / "wiki" / "concepts" / "my-concept.md").read_text()
    fm_block, _body = split_frontmatter(text)
    assert fm_block is not None
    parsed = parse_frontmatter(fm_block)
    assert parsed["sources"] == [{"resource": "sources/prior.pdf"}]


# ── frontmatter/body separation ───────────────────────────────────────────────

def test_create_page_frontmatter_followed_by_blank_line(tmp_workspace: WorkspaceFixture) -> None:
    """Every currently-shipped page has a blank line between the closing `---`
    and the title (`---\\n\\n# Title`). Matching that shape means a future
    re-ingest of the demos diffs real content, not a whitespace shift."""
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "my-concept", "My Concept",
        "# My Concept\n\nBody.\n", ["concept"],
    )
    text = (tmp_workspace.workspace / "wiki" / "concepts" / "my-concept.md").read_text()
    assert "\n---\n\n# My Concept" in text
