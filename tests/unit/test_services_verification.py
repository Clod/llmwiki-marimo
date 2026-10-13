"""services.verification and the verify option of the ingestion services. No model."""

import uuid
from types import SimpleNamespace

import pytest

from domain.tools.db import get_connection
from domain.tools.references import update_references
from domain.tools.wiki_fs import create_page
from domain.verify import FakeVerifier, store
from services import ingest
from services import verification as v
from services.wiki import open_wiki

SOURCE = "El plazo fijo UVA ajusta el capital por inflación. Tiene un plazo mínimo de 180 días."
BODY = ("# UVA\n\nEl plazo fijo UVA tiene un plazo mínimo de 180 días.\n\n"
        "Rinde un cuarenta por ciento anual garantizado.\n\n## Fuentes\n- a.pdf\n")


@pytest.fixture
def wiki(tmp_path):
    w = open_wiki(tmp_path / "w")
    doc_id = uuid.uuid4().hex
    with get_connection(w.db_path) as conn:
        user = conn.execute("SELECT user_id FROM workspace").fetchone()[0]
        conn.execute(
            "INSERT INTO documents (id, user_id, filename, title, path, relative_path, source_kind, "
            "file_type, status, content) VALUES (?,?,?,?,?,?,'source','pdf','ready',?)",
            (doc_id, user, "a.pdf", "a", "/sources/", "sources/a.pdf", SOURCE))
        conn.execute("INSERT INTO document_pages (document_id, page, content) VALUES (?,1,?)", (doc_id, SOURCE))
        conn.commit()
    object.__setattr__(w, "source_id", doc_id)
    return w


def page(wiki, folder, slug, body=BODY, **kw):
    info = create_page(wiki.db_path, wiki.path, f"/wiki/{folder}/", slug, slug, body, [],
                       sources=["a.pdf"], **kw)
    update_references(wiki.db_path, info["id"], (wiki.path / info["path"]).read_text(encoding="utf-8"),
                      f"/wiki/{folder}/")
    return info


def test_page_id_and_relative_path_are_inverse():
    assert v.page_id("/wiki/concepts/x.md") == "concepts/x" == v.page_id("wiki/concepts/x.md")
    assert v.relative_path("concepts/x") == "wiki/concepts/x.md"


def test_verify_pages_prints_one_line_per_page_and_stores_the_result(wiki):
    page(wiki, "concepts", "uva")
    lines = []
    outcomes = v.verify_pages(wiki, ["concepts/uva"], FakeVerifier(), progress=lines.append)
    assert lines == ["✔ concepts/uva: 2 sentences, 1 without support"]
    assert outcomes[0].report.unsupported == 1
    assert v.page_verification(wiki, "concepts/uva").state is store.State.VALID


def test_a_page_with_no_sources_and_a_failing_page_do_not_stop_the_rest(wiki):
    create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "sola", "sola", "Texto sin ninguna fuente.\n", [])
    page(wiki, "concepts", "uva")
    lines = []
    v.verify_pages(wiki, ["concepts/sola", "concepts/falta", "concepts/uva"], FakeVerifier(),
                   progress=lines.append)
    assert lines[0] == "– concepts/sola: no sources to verify"
    assert lines[1].startswith("✖ concepts/falta:")
    assert lines[2].startswith("✔ concepts/uva:")


def test_verify_all_counts_the_pages_that_have_sources(wiki):
    page(wiki, "concepts", "uva")
    page(wiki, "concepts", "otra", body="# Otra\n\nEl plazo fijo UVA ajusta el capital.\n\n## Fuentes\n- a.pdf\n")
    create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "sola", "sola", "Texto sin ninguna fuente.\n", [])
    assert v.verifiable_pages(wiki) == ["concepts/otra", "concepts/uva"]
    lines = []
    outcomes = v.verify_all(wiki, FakeVerifier(), progress=lines.append)
    assert lines[0] == "🔎 Verifying 2 pages…" and len(lines) == 3
    assert v.summary_line(outcomes) == "🏁 Verification: 2 pages, 3 sentences, 1 without support"


def test_page_verification_states_for_the_reading_screen(wiki):
    page(wiki, "concepts", "uva")
    assert v.page_verification(wiki, "overview") is None
    assert v.page_verification(wiki, "concepts/uva").state is store.State.NONE
    v.verify_one(wiki, "concepts/uva", FakeVerifier())
    shown = v.page_verification(wiki, "concepts/uva")
    assert shown.state is store.State.VALID and shown.has_context
    assert [s.text for s in shown.unsupported] == ["Rinde un cuarenta por ciento anual garantizado."]
    file = wiki.path / "wiki/concepts/uva.md"
    file.write_text(file.read_text(encoding="utf-8") + "\nOtra oración.\n", encoding="utf-8")
    assert v.page_verification(wiki, "concepts/uva").state is store.State.OUTDATED


def test_the_threshold_comes_from_wiki_config(wiki):
    page(wiki, "concepts", "uva")
    v.verify_one(wiki, "concepts/uva", FakeVerifier())
    (wiki.path / "wiki_config.toml").write_text("[verify]\nthreshold = 0.0\n", encoding="utf-8")
    assert v.page_verification(wiki, "concepts/uva").unsupported == []


def test_written_pages_are_the_summaries_and_the_pages_that_cite_the_sources(wiki):
    page(wiki, "summaries", "a", body="# A\n\nEl plazo fijo UVA ajusta el capital.\n", source_document_id=wiki.source_id)
    page(wiki, "concepts", "uva")
    assert v.written_pages(wiki, [wiki.source_id]) == ["concepts/uva", "summaries/a"]
    assert v.written_pages(wiki, [wiki.source_id], summaries_only=True) == ["summaries/a"]
    assert v.written_pages(wiki, []) == []


def test_scan_sources_verifies_the_pages_it_wrote_only_with_a_verifier(wiki, monkeypatch):
    import domain.ingestion as ingestion
    page(wiki, "concepts", "uva")
    monkeypatch.setattr(ingestion, "scan_and_ingest", lambda *a, **k: [
        SimpleNamespace(status="ingested", doc_id=wiki.source_id)])
    lines = []
    ingest.scan_sources(wiki, None, "m", progress=lines.append)
    assert not any("Verifying" in line for line in lines)
    ingest.scan_sources(wiki, None, "m", verifier=FakeVerifier(), progress=lines.append)
    assert "🔎 Verifying 1 page(s) against their sources…" in lines
    assert "✔ concepts/uva: 2 sentences, 1 without support" in lines


def test_regenerate_summaries_verifies_only_the_summaries(wiki, monkeypatch):
    import domain.ingestion as ingestion
    page(wiki, "summaries", "a", body="# A\n\nEl plazo fijo UVA ajusta el capital por inflación.\n\n## Fuentes\n- a.pdf\n",
         source_document_id=wiki.source_id)
    page(wiki, "concepts", "uva")
    monkeypatch.setattr(ingestion, "regenerate_wiki_pages", lambda *a, **k: [
        SimpleNamespace(status="ingested", doc_id=wiki.source_id)])
    lines = []
    ingest.regenerate_summaries(wiki, None, "m", verifier=FakeVerifier(), progress=lines.append)
    assert any(line.startswith("✔ summaries/a") for line in lines)
    assert not any("concepts/uva" in line for line in lines)


def test_ingest_uploads_verifies_after_the_cross_links(wiki, monkeypatch):
    import domain.ingestion as ingestion
    page(wiki, "concepts", "uva")
    monkeypatch.setattr(ingestion, "ingest_file", lambda *a, **k: SimpleNamespace(status="ingested", doc_id=wiki.source_id))

    def crosslink(*a, **k):
        # A cross-link rewrites the page, which deletes its verification.
        from domain.tools.wiki_fs import write_page_content
        text = (wiki.path / "wiki/concepts/uva.md").read_text(encoding="utf-8")
        write_page_content(wiki.db_path, wiki.path, "/wiki/concepts/", "uva", text + "\n")
        return 1

    monkeypatch.setattr(ingestion, "crosslink_wiki_pages", crosslink)
    (wiki.sources_dir / "a.pdf").write_bytes(b"%PDF")
    ingest.ingest_uploads(wiki, [ingest.Upload("a.pdf", b"%PDF")], None, "m", verifier=FakeVerifier())
    assert v.page_verification(wiki, "concepts/uva").state is store.State.VALID


# ── Lint and repair: the pages a repair wrote with the model ────────────────

def _repairs(monkeypatch, results):
    from domain.lint import runner as lint_runner
    from domain.repair import runner as repair_runner
    from domain.repair.report import RepairReport

    monkeypatch.setattr(lint_runner, "lint_wiki", lambda *a, **k: SimpleNamespace(issues=[object()] * len(results)))
    monkeypatch.setattr(repair_runner, "repair_wiki", lambda *a, **k: RepairReport(results=results))


def test_lint_and_repair_verifies_only_the_pages_a_repair_wrote(wiki, monkeypatch):
    from domain.repair.report import RepairResult

    page(wiki, "concepts", "uva")
    page(wiki, "concepts", "otra")
    _repairs(monkeypatch, [
        RepairResult("missing_concept", "x", "concept_created", True, "ok", written="concepts/uva"),
        RepairResult("broken_link", "concepts/otra", "deleted", True, "ok"),
        RepairResult("stale", "summaries/a", "failed", False, "error", written="summaries/a")])
    lines: list[str] = []
    ingest.lint_and_repair(wiki, None, "m", verifier=FakeVerifier(), progress=lines.append)
    assert "🔎 Verifying 1 page(s) the repairs wrote…" in lines
    assert "✔ concepts/uva: 2 sentences, 1 without support" in lines
    assert not any("concepts/otra" in line for line in lines)


def test_lint_and_repair_without_a_verifier_verifies_nothing(wiki, monkeypatch):
    from domain.repair.report import RepairResult

    page(wiki, "concepts", "uva")
    _repairs(monkeypatch, [RepairResult("missing_concept", "x", "concept_created", True, "ok", written="concepts/uva")])
    lines: list[str] = []
    ingest.lint_and_repair(wiki, None, "m", progress=lines.append)
    assert not any(line.startswith("🔎") or line.startswith("✔") for line in lines)
