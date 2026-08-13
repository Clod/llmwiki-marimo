"""Tests for the wiki repair system (domain/repair/)."""

import json
import uuid


from domain.chat.vocabulary import read_generated_aliases, write_generated_aliases
from domain.lint.report import LintIssue
from domain.repair.actions import (
    repair_contradiction,
    repair_data_gap,
    repair_missing_concept,
    repair_missing_xref,
    repair_orphan,
    repair_stale,
    repair_vocab_collision,
)
from domain.repair.runner import repair_wiki
from domain.lint.report import LintReport
from domain.tools.db import get_connection
from domain.tools.wiki_fs import create_page
from tests.helpers.workspace import WorkspaceFixture

_LONG = "word " * 50  # padding to exceed MIN_CHUNK_TOKENS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _insert_source(conn, workspace_id: str, filename: str, content: str) -> str:
    doc_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO documents "
        "(id, user_id, filename, title, path, relative_path, source_kind, "
        "file_type, status, content, page_count, document_number) "
        "VALUES (?,?,?,?,?,?,'source','pdf','ready',?,1,1)",
        (doc_id, workspace_id, filename, filename, "/sources/",
         f"sources/{filename}", content),
    )
    conn.execute(
        "INSERT INTO document_pages (id, document_id, page, content) VALUES (?,?,1,?)",
        (str(uuid.uuid4()), doc_id, content),
    )
    conn.commit()
    return doc_id


def _workspace_id(db_path: str) -> str:
    with get_connection(db_path) as conn:
        return conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()["user_id"]


# ── repair_orphan ─────────────────────────────────────────────────────────────

def test_repair_orphan_deletes_concept_page(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "lonely-concept",
        "Lonely Concept", f"# Lonely Concept\n\n{_LONG}", [],
    )
    assert (tmp_workspace.workspace / "wiki" / "concepts" / "lonely-concept.md").exists()

    issue = LintIssue(
        check="orphan", severity="warning",
        page="/wiki/concepts/lonely-concept.md",
        description="No other pages link to 'Lonely Concept'",
        suggestion="",
    )
    result = repair_orphan(issue, tmp_workspace.db_path, tmp_workspace.workspace)

    assert result.success
    assert result.action == "deleted"
    assert not (tmp_workspace.workspace / "wiki" / "concepts" / "lonely-concept.md").exists()


def test_repair_orphan_missing_page_returns_failed(tmp_workspace: WorkspaceFixture) -> None:
    issue = LintIssue(
        check="orphan", severity="warning",
        page="/wiki/concepts/ghost.md",
        description="", suggestion="",
    )
    result = repair_orphan(issue, tmp_workspace.db_path, tmp_workspace.workspace)
    assert not result.success
    assert result.action == "failed"


# ── repair_stale ──────────────────────────────────────────────────────────────

def test_repair_stale_regenerates_summary_page(tmp_workspace: WorkspaceFixture) -> None:
    uid = _workspace_id(tmp_workspace.db_path)
    with get_connection(tmp_workspace.db_path) as conn:
        src_id = _insert_source(conn, uid, "fairy-tale.pdf", f"Once upon a time. {_LONG}")

    summary_content = f"# Fairy Tale\n\nA classic story. {_LONG}"
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "fairy-tale",
        "Fairy Tale", summary_content, [],
        overwrite=False, source_document_id=src_id,
    )

    tmp_workspace.llm.responses = [
        json.dumps({
            "document_summary": "An updated fairy tale summary.",
            "concepts": [],
        }),
    ]

    issue = LintIssue(
        check="stale", severity="warning",
        page="/wiki/summaries/fairy-tale.md",
        description="Source was updated after wiki page",
        suggestion="",
    )
    result = repair_stale(
        issue, tmp_workspace.db_path, tmp_workspace.workspace,
        tmp_workspace.llm, "fake",
    )

    assert result.success
    assert result.action == "regenerated"
    assert (tmp_workspace.workspace / "wiki" / "summaries" / "fairy-tale.md").exists()


def test_repair_stale_skips_without_source_document_id(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "unlinked-concept",
        "Unlinked", f"# Unlinked\n\n{_LONG}", [],
    )
    issue = LintIssue(
        check="stale", severity="warning",
        page="/wiki/concepts/unlinked-concept.md",
        description="", suggestion="",
    )
    result = repair_stale(
        issue, tmp_workspace.db_path, tmp_workspace.workspace,
        tmp_workspace.llm, "fake",
    )
    assert result.success
    assert result.action == "skipped"


# ── repair_missing_xref ───────────────────────────────────────────────────────

def test_repair_missing_xref_is_skipped(tmp_workspace: WorkspaceFixture) -> None:
    issue = LintIssue(
        check="missing_xref", severity="info",
        page="/wiki/concepts/a.md",
        description="'A' and 'B' share cited sources but don't link to each other",
        suggestion="",
    )
    result = repair_missing_xref(issue, tmp_workspace.db_path, tmp_workspace.workspace)
    assert result.success
    assert result.action == "skipped"


def test_repair_missing_xref_writes_under_see_also_not_under_sources(
    tmp_workspace: WorkspaceFixture,
) -> None:
    """The new link must land in the See also section, wherever that sits.

    Pages are generated with `## See also` BEFORE `## Sources`. Appending the
    bullet to the end of the file therefore drops it under Sources, which is a
    list of the documents the page was written from. A wiki page is not one, and
    the reference parser reads everything under that heading as a citation — so
    the misplaced bullet is recorded as "this page came from that page".

    In the shipped fairy-tale demo this produced 13 such records out of 30.
    """
    body = (
        "# A\n\n"
        f"{_LONG}\n\n"
        "## See also\n\n"
        "- [Existing](existing.md)\n\n"
        "## Sources\n"
        "- Cinderella.pdf\n"
    )
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "a", "A", body, [])
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "b", "Glass Slipper",
                f"# Glass Slipper\n\n{_LONG}", [])

    issue = LintIssue(
        check="missing_xref", severity="info", page="/wiki/concepts/a.md",
        related_page="/wiki/concepts/b.md",
        description="'A' and 'Glass Slipper' share cited sources but don't link",
        suggestion="",
    )
    result = repair_missing_xref(issue, tmp_workspace.db_path, tmp_workspace.workspace)
    assert result.action == "xref_added", result.message

    text = (tmp_workspace.workspace / "wiki" / "concepts" / "a.md").read_text(encoding="utf-8")
    see_also_at = text.index("## See also")
    sources_at = text.index("## Sources")
    link_at = text.index("(b.md)")
    assert see_also_at < link_at < sources_at, (
        "the new link was written outside the See also section:\n" + text
    )


def test_a_cites_record_never_points_at_a_wiki_page(
    tmp_workspace: WorkspaceFixture,
) -> None:
    """`cites` means "this page was written from that source document".

    Nothing else is a valid target. The reference parser matches a citation
    against every document by filename *and by title*, so a bullet naming a wiki
    page — however it got there — used to resolve to that page and be stored as a
    citation. Deletion, lint and provenance all read these records and all assume
    the target is a source.
    """
    from domain.tools.references import update_references

    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "glass-slipper", "Glass Slipper",
                f"# Glass Slipper\n\n{_LONG}", [])
    body = (
        "# A\n\n"
        f"{_LONG}\n\n"
        "## Sources\n"
        "- [Glass Slipper](glass-slipper.md)\n"
    )
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "a", "A", body, [])

    with get_connection(tmp_workspace.db_path) as conn:
        doc_id = conn.execute(
            "SELECT id FROM documents WHERE path||filename='/wiki/concepts/a.md'"
        ).fetchone()["id"]
    update_references(tmp_workspace.db_path, doc_id, body, "/wiki/concepts/")

    with get_connection(tmp_workspace.db_path) as conn:
        bad = conn.execute(
            "SELECT count(*) AS n FROM document_references r "
            "JOIN documents t ON t.id = r.target_document_id "
            "WHERE r.reference_type = 'cites' AND t.source_kind = 'wiki'"
        ).fetchone()["n"]
    assert bad == 0, f"{bad} cites record(s) point at a wiki page"


# ── repair_missing_concept ────────────────────────────────────────────────────

def test_repair_missing_concept_creates_page(tmp_workspace: WorkspaceFixture) -> None:
    tmp_workspace.llm.response_content = f"# Snow White\n\nA classic fairy tale. {_LONG}"

    issue = LintIssue(
        check="missing_concept", severity="warning",
        page="/wiki/summaries/blancanieves.md",
        description="Links to 'concepts/snow-white.md' which doesn't exist on disk",
        suggestion="",
    )
    result = repair_missing_concept(
        issue, tmp_workspace.db_path, tmp_workspace.workspace,
        tmp_workspace.llm, "fake",
    )

    assert result.success
    assert result.action == "concept_created"
    assert (tmp_workspace.workspace / "wiki" / "concepts" / "snow-white.md").exists()


def test_repair_missing_concept_skips_if_already_exists(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "snow-white",
        "Snow White", f"# Snow White\n\n{_LONG}", [],
    )
    tmp_workspace.llm.response_content = f"# Snow White\n\nUpdated. {_LONG}"

    issue = LintIssue(
        check="missing_concept", severity="warning",
        page="/wiki/summaries/blancanieves.md",
        description="Links to 'concepts/snow-white.md' which doesn't exist on disk",
        suggestion="",
    )
    result = repair_missing_concept(
        issue, tmp_workspace.db_path, tmp_workspace.workspace,
        tmp_workspace.llm, "fake",
    )
    assert result.success
    assert result.action == "skipped"


# ── repair_contradiction / repair_data_gap ────────────────────────────────────

def test_repair_contradiction_flags_existing_page(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "a",
        "A", f"# A\n\n{_LONG}", [],
    )
    issue = LintIssue(
        check="contradiction", severity="error",
        page="/wiki/concepts/a.md",
        description="Contradiction with /wiki/concepts/b.md",
        suggestion="",
        related_page="/wiki/concepts/b.md",
    )
    result = repair_contradiction(issue, tmp_workspace.db_path, tmp_workspace.workspace)
    assert result.success
    assert result.action == "contradiction_flagged"


def test_repair_data_gap_skips_empty_topic(tmp_workspace: WorkspaceFixture) -> None:
    issue = LintIssue(
        check="data_gap", severity="info",
        page="/wiki/concepts/interest-rates.md",
        description="Missing topic: Quantitative Easing",
        suggestion="",
        topic="",  # empty topic → skipped
    )
    result = repair_data_gap(issue, tmp_workspace.db_path, tmp_workspace.workspace)
    assert result.success
    assert result.action == "skipped"


# ── repair_wiki runner ────────────────────────────────────────────────────────

def test_repair_wiki_empty_report(tmp_workspace: WorkspaceFixture) -> None:
    report = repair_wiki(LintReport(), tmp_workspace.db_path, tmp_workspace.workspace)
    assert report.results == []


def test_repair_wiki_dispatches_and_returns_report(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", "orphan-page",
        "Orphan Page", f"# Orphan\n\n{_LONG}", [],
    )
    tmp_workspace.llm.response_content = f"# New Concept\n\n{_LONG}"

    lint = LintReport(issues=[
        LintIssue(
            check="orphan", severity="warning",
            page="/wiki/concepts/orphan-page.md",
            description="", suggestion="",
        ),
        LintIssue(
            check="missing_concept", severity="warning",
            page="/wiki/summaries/x.md",
            description="Links to 'concepts/new-concept.md' which doesn't exist on disk",
            suggestion="",
        ),
        LintIssue(
            check="contradiction", severity="error",
            page="/wiki/concepts/a.md",
            description="Conflicting claims", suggestion="",
        ),
    ])

    report = repair_wiki(
        lint, tmp_workspace.db_path, tmp_workspace.workspace,
        tmp_workspace.llm, "fake",
    )

    assert len(report.results) == 3
    actions = {r.check: r.action for r in report.results}
    assert actions["orphan"] == "deleted"
    assert actions["missing_concept"] == "concept_created"
    # page "/wiki/concepts/a.md" doesn't exist → repair returns failed
    assert actions["contradiction"] == "failed"
    assert len(report.fixed) == 2
    assert len(report.skipped) == 0
    assert len(report.failed) == 1


def test_repair_wiki_skips_llm_repairs_without_client(tmp_workspace: WorkspaceFixture) -> None:
    lint = LintReport(issues=[
        LintIssue(
            check="stale", severity="warning",
            page="/wiki/summaries/x.md",
            description="", suggestion="",
        ),
        LintIssue(
            check="missing_concept", severity="warning",
            page="/wiki/summaries/y.md",
            description="Links to 'concepts/foo.md' which doesn't exist on disk",
            suggestion="",
        ),
    ])
    report = repair_wiki(lint, tmp_workspace.db_path, tmp_workspace.workspace)
    assert all(r.action == "skipped" for r in report.results)


# ── repair_vocab_collision (Piece 5 auto-repair) ──────────────────────────────

def _collision_issue(alias: str) -> LintIssue:
    return LintIssue(
        check="vocab_collision", severity="error",
        page=".llmwiki/aliases.generated.toml",
        description=f"Alias '{alias}' is really the name of something else",
        suggestion="Remove it",
        alias=alias,
    )


def test_repair_vocab_collision_drops_alias_from_artifact(tmp_workspace: WorkspaceFixture) -> None:
    write_generated_aliases(tmp_workspace.workspace, {"CEDEAR": ["acciones", "Certificado"]})
    result = repair_vocab_collision(
        _collision_issue("acciones"), tmp_workspace.db_path, tmp_workspace.workspace
    )
    assert result.success
    assert result.action == "deleted"
    assert read_generated_aliases(tmp_workspace.workspace) == {"CEDEAR": ["Certificado"]}


def test_repair_vocab_collision_removes_now_empty_canonical(tmp_workspace: WorkspaceFixture) -> None:
    write_generated_aliases(tmp_workspace.workspace, {"CEDEAR": ["acciones"]})
    result = repair_vocab_collision(
        _collision_issue("acciones"), tmp_workspace.db_path, tmp_workspace.workspace
    )
    assert result.success
    assert result.action == "deleted"
    assert read_generated_aliases(tmp_workspace.workspace) == {}


def test_repair_vocab_collision_skips_hand_override(tmp_workspace: WorkspaceFixture) -> None:
    # The collision lives in wiki_config.toml (hand override), NOT the generated
    # artifact → the repair must not touch the human's file; it surfaces and skips.
    write_generated_aliases(tmp_workspace.workspace, {"Dólar": ["billete verde"]})
    result = repair_vocab_collision(
        _collision_issue("acciones"), tmp_workspace.db_path, tmp_workspace.workspace
    )
    assert result.success
    assert result.action == "skipped"
    assert "wiki_config" in result.message
    # untouched
    assert read_generated_aliases(tmp_workspace.workspace) == {"Dólar": ["billete verde"]}


def test_repair_vocab_collision_via_dispatch(tmp_workspace: WorkspaceFixture) -> None:
    write_generated_aliases(tmp_workspace.workspace, {"CEDEAR": ["acciones", "Certificado"]})
    lint = LintReport(issues=[_collision_issue("acciones")])
    report = repair_wiki(lint, tmp_workspace.db_path, tmp_workspace.workspace)  # no LLM needed
    assert len(report.fixed) == 1
    assert read_generated_aliases(tmp_workspace.workspace) == {"CEDEAR": ["Certificado"]}


def test_repair_skips_advisory_vocab_checks_with_clear_message(
    tmp_workspace: WorkspaceFixture,
) -> None:
    # vocab_stale / vocab_covered are informational — they have no auto-repair, but
    # they are KNOWN checks and must not be labelled "Unknown check type".
    lint = LintReport(issues=[
        LintIssue(check="vocab_stale", severity="warning",
                  page=".llmwiki/aliases.generated.toml",
                  description="Aliases for 'X', which has no page or dataset",
                  suggestion="Remove it"),
        LintIssue(check="vocab_covered", severity="info", page="wiki_config.toml",
                  description="'cripto' is in [fuera_de_alcance] but now has a page",
                  suggestion="Remove it"),
    ])
    report = repair_wiki(lint, tmp_workspace.db_path, tmp_workspace.workspace)
    assert len(report.skipped) == 2
    for r in report.skipped:
        assert "unknown check type" not in r.message.lower()
        assert "advisory" in r.message.lower()


def test_repair_skips_thin_page_as_advisory_not_unknown(
    tmp_workspace: WorkspaceFixture,
) -> None:
    """thin_page reports that the wiki under-covers a source. It has no automatic
    repair on purpose: its own suggestion is "expand or regenerate the page, or
    accept the Tier-2 fallback", and choosing between those is a human call that
    would need the model to write new prose either way.

    So it belongs with the other advisory checks. Labelling it "Unknown check
    type" printed a bug report into the ingestion log of every run — including the
    walkthrough appendix that ships with the project.
    """
    lint = LintReport(issues=[
        LintIssue(
            check="thin_page", severity="warning",
            page="wiki/summaries/cinderella.md",
            description="7 of 10 source chunks in 'Cinderella.pdf' aren't reflected "
                        "in the wiki page(s) citing it",
            suggestion="Expand or regenerate the page to cover the source",
        ),
    ])
    report = repair_wiki(lint, tmp_workspace.db_path, tmp_workspace.workspace)

    assert len(report.skipped) == 1
    message = report.skipped[0].message.lower()
    assert "unknown check type" not in message
    assert "advisory" in message


def test_every_lint_check_is_either_repairable_or_declared_advisory() -> None:
    """No check lint can emit may fall through to "Unknown check type".

    That label means "the repair layer has never heard of this", and it is the
    right message for a typo or a stale issue record. It is the wrong message for
    a check somebody added to lint and forgot to wire here — which is exactly how
    `thin_page` ended up printing a bug report into every ingestion log.

    Reads the check names out of the lint module's own source, so adding a new
    check without deciding what repair should do about it fails here.
    """
    import re
    from pathlib import Path

    from domain.repair.runner import _ADVISORY_CHECKS, _DISPATCH

    source = (Path(__file__).resolve().parents[2]
              / "base" / "domain" / "lint" / "checks.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'check="([a-z_]+)"', source))
    assert emitted, "no check names found — did lint/checks.py move?"

    unhandled = emitted - set(_DISPATCH) - _ADVISORY_CHECKS
    assert not unhandled, (
        f"lint emits {sorted(unhandled)}, which repair_wiki would report as "
        f"'Unknown check type'. Add a handler to _DISPATCH, or list it in "
        f"_ADVISORY_CHECKS if it is meant to be resolved by a human."
    )


def test_repair_genuinely_unknown_check_is_still_flagged(
    tmp_workspace: WorkspaceFixture,
) -> None:
    lint = LintReport(issues=[LintIssue(
        check="totally_made_up", severity="warning", page="x",
        description="d", suggestion="s")])
    report = repair_wiki(lint, tmp_workspace.db_path, tmp_workspace.workspace)
    assert len(report.skipped) == 1
    assert "unknown check type" in report.skipped[0].message.lower()


# ── the skip message speaks to the person reading it ────────────────────────

def test_needs_llm_skip_message_names_the_two_buttons_that_supply_a_model() -> None:
    """The message is read in the ingest app's Activity Log, not in a traceback.

    It used to say "pass llm_client" — accurate, and useless to the only audience
    that ever sees it: somebody looking at a log in an app, where there is no
    argument to pass and two buttons that do the job. A skip should name what is
    missing in the reader's own terms.
    """
    from domain.repair.runner import _NEEDS_LLM, _NEEDS_LLM_MESSAGE

    rendered = _NEEDS_LLM_MESSAGE.format(check="stale")
    assert "llm_client" not in rendered, "API-level wording leaked back into a user-facing message"
    assert "Also run full LLM lint & repair after ingest" in rendered
    assert "Run Wiki Lint & Repair" in rendered
    assert _NEEDS_LLM == {"stale", "missing_concept"}, (
        "the message promises these two are fixable by supplying a model"
    )


def test_skip_message_button_names_match_the_ingest_app() -> None:
    """Guard against the two drifting apart.

    The message tells the user to press specific controls. If either is renamed
    in the app and not here, the log sends them looking for something that does
    not exist — a failure nothing else would catch, since no code reads these
    strings.
    """
    from pathlib import Path

    from domain.repair.runner import _NEEDS_LLM_MESSAGE

    app = (Path(__file__).resolve().parents[2] / "marimo" / "ingest_app.py").read_text()
    for control in ("Also run full LLM lint & repair after ingest", "Run Wiki Lint & Repair"):
        assert control in _NEEDS_LLM_MESSAGE, f"message no longer names {control!r}"
        assert control in app, f"{control!r} is named in the skip message but gone from the app"
