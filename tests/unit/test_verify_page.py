import uuid

import pytest

from domain.tools.db import get_connection, open_db, seed_workspace_row
from domain.tools.references import update_references
from domain.tools.wiki_fs import create_page, write_page_content
from domain.verify import FakeVerifier, store
from domain.verify.page import (
    CHAT, CONCEPT, SUMMARY, current_state, generated_texts, resolve_context, verify_page,
)

SOURCE_A = "El plazo fijo UVA ajusta el capital por inflación. Tiene un plazo mínimo de 180 días."
SOURCE_B = "Los CEDEARs representan acciones extranjeras que cotizan en el mercado local."


@pytest.fixture
def wiki(tmp_path):
    db = str(tmp_path / ".llmwiki" / "index.db")
    open_db(db).close()
    seed_workspace_row(db, "w")
    (tmp_path / "wiki").mkdir()
    return db, tmp_path


def add_source(db, name, pages):
    doc_id = uuid.uuid4().hex
    with get_connection(db) as conn:
        user = conn.execute("SELECT user_id FROM workspace").fetchone()[0]
        conn.execute(
            "INSERT INTO documents (id, user_id, filename, title, path, relative_path, source_kind, "
            "file_type, status, content) VALUES (?,?,?,?,?,?,'source','pdf','ready',?)",
            (doc_id, user, name, name, "/sources/", f"sources/{name}", "\n".join(pages)))
        for n, text in enumerate(pages, 1):
            conn.execute("INSERT INTO document_pages (document_id, page, content) VALUES (?,?,?)",
                         (doc_id, n, text))
        conn.commit()
    return doc_id


def add_page(wiki, folder, slug, body, *, sources=None, source_document_id=None):
    db, root = wiki
    info = create_page(db, root, f"/wiki/{folder}/", slug, slug, body, [], sources=sources,
                       source_document_id=source_document_id, language="es")
    update_references(db, info["id"], (root / info["path"]).read_text(encoding="utf-8"), f"/wiki/{folder}/")
    return info


def context(wiki, info):
    db, root = wiki
    with get_connection(db) as conn:
        return resolve_context(conn, info["id"], (root / info["path"]).read_text(encoding="utf-8"))


def run(wiki, info, **kw):
    db, root = wiki
    return verify_page(db, root, info["path"], FakeVerifier(), threshold=0.6, candidates=5,
                       language="es", **kw)


def test_a_summary_page_uses_the_pages_of_its_source(wiki):
    src = add_source(wiki[0], "a.pdf", [SOURCE_A, "Segunda página."])
    info = add_page(wiki, "summaries", "a", "## Resumen\n\nEl plazo fijo UVA tiene un plazo mínimo de 180 días.\n",
                    sources=["a.pdf"], source_document_id=src)
    ctx = context(wiki, info)
    assert ctx.kind == SUMMARY and [p.page for p in ctx.passages] == ["sources/a.pdf"]
    assert SOURCE_A in ctx.passages[0].text and "Segunda página." in ctx.passages[0].text
    assert list(ctx.source_hashes) == ["sources/a.pdf"]


def test_a_concept_page_uses_every_source_it_cites(wiki):
    add_source(wiki[0], "a.pdf", [SOURCE_A])
    add_source(wiki[0], "b.pdf", [SOURCE_B])
    add_source(wiki[0], "c.pdf", ["No citada."])
    info = add_page(wiki, "concepts", "uva", "# UVA\n\nTexto.\n\n## Fuentes\n- a.pdf\n- b.pdf\n", sources=["a.pdf", "b.pdf"])
    ctx = context(wiki, info)
    assert ctx.kind == CONCEPT
    assert [p.page for p in ctx.passages] == ["sources/a.pdf", "sources/b.pdf"]


def test_a_chat_page_uses_the_wiki_pages_and_the_sources_it_cites(wiki):
    add_source(wiki[0], "a.pdf", [SOURCE_A])
    add_page(wiki, "concepts", "otra", "# Otra\n\nUna página del wiki con texto propio.\n")
    info = add_page(wiki, "concepts", "consulta",
                    "# Consulta\n\nRespuesta.\n\n## Véase también\n\n- [Otra](otra.md)\n\n## Fuentes\n- a.pdf\n",
                    sources=["chat"])
    ctx = context(wiki, info)
    assert ctx.kind == CHAT
    assert sorted(p.page for p in ctx.passages) == ["sources/a.pdf", "wiki/concepts/otra.md"]


def test_a_page_with_no_source_and_no_citation_has_no_context_and_stores_nothing(wiki):
    info = add_page(wiki, "concepts", "sola", "# Sola\n\nUn texto sin fuentes de ninguna clase.\n")
    assert context(wiki, info).kind == CONCEPT and context(wiki, info).empty
    outcome = run(wiki, info)
    assert outcome.report is None and not outcome.verified
    assert current_state(wiki[0], wiki[1], info["path"])[0] is store.State.NONE


def test_verify_page_reports_unsupported_sentences_and_does_not_touch_the_page(wiki):
    add_source(wiki[0], "a.pdf", [SOURCE_A])
    body = ("# UVA\n\nEl plazo fijo UVA tiene un plazo mínimo de 180 días.\n\n"
            "Rinde un cuarenta por ciento anual garantizado.\n\n## Fuentes\n- a.pdf\n")
    info = add_page(wiki, "concepts", "uva", body, sources=["a.pdf"])
    file = wiki[1] / info["path"]
    before = file.read_text(encoding="utf-8")
    outcome = run(wiki, info)
    assert file.read_text(encoding="utf-8") == before
    report = outcome.report
    assert report.checked == 2 and report.unsupported == 1
    assert report.unsupported_sentences()[0].text.startswith("Rinde un cuarenta")
    assert report.pairs <= report.pairs_possible
    state, stored = current_state(wiki[0], wiki[1], info["path"])
    assert state is store.State.VALID and stored.checked == 2
    page_body = store.page_body(before)
    s = stored.sentences[1]
    assert page_body[s.start:s.end] == s.text and s.source == "sources/a.pdf"


def test_the_generated_sections_are_not_checked(wiki):
    body = ("# UVA\n\nEl plazo fijo UVA tiene un plazo mínimo de 180 días.\n\n"
            "## Véase también\n\n- [Una página relacionada cualquiera](../concepts/x.md)\n\n"
            "## Fuentes\n- a.pdf\n\n[^1]: nota de origen sin más detalle\n")
    assert len(generated_texts(body)) == 3
    add_source(wiki[0], "a.pdf", [SOURCE_A])
    info = add_page(wiki, "concepts", "uva", body, sources=["a.pdf"])
    assert run(wiki, info).report.checked == 1


def test_an_edit_through_the_application_deletes_the_verification(wiki):
    add_source(wiki[0], "a.pdf", [SOURCE_A])
    info = add_page(wiki, "concepts", "uva", "# UVA\n\nEl plazo fijo UVA ajusta el capital.\n\n## Fuentes\n- a.pdf\n",
                    sources=["a.pdf"])
    run(wiki, info)
    write_page_content(wiki[0], wiki[1], "/wiki/concepts/", "uva", "# UVA\n\nOtro texto.\n", language="es")
    assert current_state(wiki[0], wiki[1], info["path"])[0] is store.State.NONE


def test_an_edit_outside_the_application_is_detected_by_the_page_hash(wiki):
    add_source(wiki[0], "a.pdf", [SOURCE_A])
    info = add_page(wiki, "concepts", "uva", "# UVA\n\nEl plazo fijo UVA ajusta el capital.\n\n## Fuentes\n- a.pdf\n",
                    sources=["a.pdf"])
    run(wiki, info)
    file = wiki[1] / info["path"]
    file.write_text(file.read_text(encoding="utf-8") + "\nUna oración agregada a mano.\n", encoding="utf-8")
    assert current_state(wiki[0], wiki[1], info["path"])[0] is store.State.OUTDATED


def test_a_changed_source_is_detected_by_the_source_hash(wiki):
    src = add_source(wiki[0], "a.pdf", [SOURCE_A])
    info = add_page(wiki, "concepts", "uva", "# UVA\n\nEl plazo fijo UVA ajusta el capital.\n\n## Fuentes\n- a.pdf\n",
                    sources=["a.pdf"])
    run(wiki, info)
    with get_connection(wiki[0]) as conn:
        conn.execute("UPDATE document_pages SET content = ? WHERE document_id = ?", ("Texto nuevo.", src))
        conn.commit()
    assert current_state(wiki[0], wiki[1], info["path"])[0] is store.State.OUTDATED


def test_the_threshold_reclassifies_without_verifying_again(wiki):
    add_source(wiki[0], "a.pdf", [SOURCE_A])
    info = add_page(wiki, "concepts", "uva", "# UVA\n\nEl plazo fijo UVA tiene un plazo mínimo de 180 días.\n\nRinde un cuarenta anual garantizado.\n\n## Fuentes\n- a.pdf\n",
                    sources=["a.pdf"])
    run(wiki, info)
    _, stored = current_state(wiki[0], wiki[1], info["path"])
    assert len(stored.unsupported(0.6)) == 1
    assert len(stored.unsupported(0.0)) == 0


def test_a_missing_file_raises(wiki):
    with pytest.raises(FileNotFoundError):
        verify_page(wiki[0], wiki[1], "wiki/concepts/no.md", FakeVerifier(), threshold=0.5, candidates=5, language="es")


def test_a_concept_page_whose_edges_were_not_rebuilt_uses_the_sources_of_its_front_matter(wiki):
    add_source(wiki[0], "a.pdf", [SOURCE_A])
    info = add_page(wiki, "concepts", "uva", "# UVA\n\nEl plazo fijo UVA ajusta el capital.\n", sources=["a.pdf"])
    # No `## Fuentes` section, so update_references recorded no citation.
    with get_connection(wiki[0]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM document_references WHERE reference_type='cites'").fetchone()[0] == 0
    ctx = context(wiki, info)
    assert ctx.kind == CONCEPT and [p.page for p in ctx.passages] == ["sources/a.pdf"]
