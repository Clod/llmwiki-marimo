"""The two report-only lint checks of the sentence verification."""

import uuid

import pytest

from domain.lint.checks import unsupported_sentences_check, unverified_pages_check
from domain.lint.report import LintReport
from domain.lint.runner import lint_wiki
from domain.repair.runner import _ADVISORY_CHECKS, _DISPATCH, repair_wiki
from domain.tools.db import get_connection, open_db, seed_workspace_row
from domain.tools.references import update_references
from domain.tools.wiki_fs import create_page, write_page_content
from domain.verify import FakeVerifier
from domain.verify.page import verify_page

SOURCE = "El plazo fijo UVA ajusta el capital por inflación. Tiene un plazo mínimo de 180 días."
BODY = ("# UVA\n\nEl plazo fijo UVA tiene un plazo mínimo de 180 días.\n\n"
        "Rinde un cuarenta por ciento anual garantizado.\n\n## Fuentes\n- a.pdf\n")


@pytest.fixture
def wiki(tmp_path):
    db = str(tmp_path / ".llmwiki" / "index.db")
    open_db(db).close()
    seed_workspace_row(db, "w")
    doc_id = uuid.uuid4().hex
    with get_connection(db) as conn:
        user = conn.execute("SELECT user_id FROM workspace").fetchone()[0]
        conn.execute(
            "INSERT INTO documents (id, user_id, filename, title, path, relative_path, source_kind, "
            "file_type, status, content) VALUES (?,?,?,?,?,?,'source','pdf','ready',?)",
            (doc_id, user, "a.pdf", "a", "/sources/", "sources/a.pdf", SOURCE))
        conn.execute("INSERT INTO document_pages (document_id, page, content) VALUES (?,1,?)", (doc_id, SOURCE))
        conn.commit()
    return db, tmp_path


def add_page(wiki, slug, body=BODY, sources=("a.pdf",)):
    db, root = wiki
    info = create_page(db, root, "/wiki/concepts/", slug, slug, body, [], sources=list(sources), language="es")
    update_references(db, info["id"], (root / info["path"]).read_text(encoding="utf-8"), "/wiki/concepts/")
    return info


def verify(wiki, info, threshold=0.6):
    return verify_page(wiki[0], wiki[1], info["path"], FakeVerifier(), threshold=threshold,
                       candidates=5, language="es")


def test_a_page_never_verified_is_listed_and_a_page_with_no_sources_is_not(wiki):
    add_page(wiki, "uva")
    add_page(wiki, "sola", body="# Sola\n\nTexto sin ninguna fuente citada.\n", sources=())
    issues = unverified_pages_check(wiki[0], wiki[1])
    assert [i.page for i in issues] == ["/wiki/concepts/uva.md"]
    assert issues[0].check == "unverified_pages" and issues[0].severity == "info"
    assert "Never verified" in issues[0].description


def test_a_verified_page_is_not_listed_until_it_changes(wiki):
    info = add_page(wiki, "uva")
    verify(wiki, info)
    assert unverified_pages_check(wiki[0], wiki[1]) == []
    file = wiki[1] / info["path"]
    file.write_text(file.read_text(encoding="utf-8") + "\nOtra oración agregada a mano.\n", encoding="utf-8")
    issues = unverified_pages_check(wiki[0], wiki[1])
    assert len(issues) == 1 and "out of date" in issues[0].description


def test_a_page_written_by_the_application_is_listed_again(wiki):
    info = add_page(wiki, "uva")
    verify(wiki, info)
    write_page_content(wiki[0], wiki[1], "/wiki/concepts/", "uva", BODY + "\nMás texto.\n", language="es")
    assert len(unverified_pages_check(wiki[0], wiki[1])) == 1


def test_unsupported_sentences_lists_the_pages_with_sentences_below_the_threshold(wiki):
    verify(wiki, add_page(wiki, "uva"))
    issues = unsupported_sentences_check(wiki[0], wiki[1])
    assert len(issues) == 1
    assert issues[0].check == "unsupported_sentences" and issues[0].severity == "warning"
    assert issues[0].page == "/wiki/concepts/uva.md"
    assert "Rinde un cuarenta por ciento anual garantizado." in issues[0].description
    assert "1 of 2 sentences" in issues[0].description


def test_the_threshold_in_wiki_config_reclassifies_without_verifying_again(wiki):
    verify(wiki, add_page(wiki, "uva"))
    (wiki[1] / "wiki_config.toml").write_text("[verify]\nthreshold = 0.0\n", encoding="utf-8")
    assert unsupported_sentences_check(wiki[0], wiki[1]) == []
    (wiki[1] / "wiki_config.toml").write_text("[verify]\nthreshold = 0.5\n", encoding="utf-8")
    assert "1 of 2" in unsupported_sentences_check(wiki[0], wiki[1])[0].description


def test_an_outdated_verification_is_not_reported_as_unsupported(wiki):
    info = add_page(wiki, "uva")
    verify(wiki, info)
    file = wiki[1] / info["path"]
    file.write_text(file.read_text(encoding="utf-8") + "\nOtra oración.\n", encoding="utf-8")
    assert unsupported_sentences_check(wiki[0], wiki[1]) == []


def test_lint_wiki_runs_both_checks_and_repair_skips_them(wiki):
    verify(wiki, add_page(wiki, "uva"))
    add_page(wiki, "otra", body="# Otra\n\nOtro texto sobre el plazo fijo UVA.\n\n## Fuentes\n- a.pdf\n")
    report = lint_wiki(wiki[0], wiki[1])
    checks = {i.check for i in report.issues}
    assert {"unsupported_sentences", "unverified_pages"} <= checks
    assert not {"unsupported_sentences", "unverified_pages"} & set(_DISPATCH)
    assert {"unsupported_sentences", "unverified_pages"} <= _ADVISORY_CHECKS
    before = (wiki[1] / "wiki/concepts/uva.md").read_text(encoding="utf-8")
    only_ours = LintReport(issues=[i for i in report.issues
                                   if i.check in {"unsupported_sentences", "unverified_pages"}])
    result = repair_wiki(only_ours, wiki[0], wiki[1])
    skipped = [r for r in result.results if r.check in {"unsupported_sentences", "unverified_pages"}]
    assert skipped and all(r.action == "skipped" and "advisory" in r.message for r in skipped)
    assert (wiki[1] / "wiki/concepts/uva.md").read_text(encoding="utf-8") == before
