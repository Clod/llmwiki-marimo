
import pytest

from domain.tools.db import get_connection, open_db, seed_workspace_row
from domain.tools.wiki_fs import create_page, delete_page, write_page_content
from domain.verify import SentenceResult, VerificationReport
from domain.verify import store


@pytest.fixture
def wiki(tmp_path):
    db = str(tmp_path / ".llmwiki" / "index.db")
    open_db(db).close()
    seed_workspace_row(db, "w")
    (tmp_path / "wiki").mkdir()
    return db, tmp_path


def make_page(wiki, slug="uva", body="Un plazo fijo UVA dura 90 días. Rinde un 40 % anual.\n"):
    db, root = wiki
    return create_page(db, root, "/wiki/concepts/", slug, "UVA", body, [], language="es")["id"]


def report():
    return VerificationReport((
        SentenceResult(0, 30, "Un plazo fijo UVA dura 90 días.", True, 0.93, "sources/a.pdf", "passage a"),
        SentenceResult(31, 52, "Rinde un 40 % anual.", False, 0.04, "sources/a.pdf", "x" * 500),
    ), "fake", 0.5, pairs=4, pairs_possible=20)


def save(wiki, doc_id, **kw):
    db, _ = wiki
    with get_connection(db) as conn:
        store.save_verification(conn, doc_id, report(), body_hash="h", source_hashes={"sources/a.pdf": "s"},
                                model="m", **kw)


def load(wiki, doc_id):
    with get_connection(wiki[0]) as conn:
        return store.load_verification(conn, doc_id)


def test_a_new_index_has_the_two_tables_and_an_old_index_gets_them(tmp_path):
    db = str(tmp_path / "old.db")
    conn = open_db(db)
    conn.executescript("DROP TABLE verification_sentences; DROP TABLE page_verifications;")
    conn.close()
    with get_connection(db) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"page_verifications", "verification_sentences"} <= names


def test_save_and_load_round_trip(wiki):
    doc_id = make_page(wiki)
    save(wiki, doc_id)
    stored = load(wiki, doc_id)
    assert stored.checked == 2 and stored.verifier == "fake" and stored.model == "m"
    assert stored.pairs_compared == 4 and stored.pairs_possible == 20
    assert stored.source_hashes == {"sources/a.pdf": "s"}
    assert stored.sentences[1].text == "Rinde un 40 % anual."
    assert len(stored.sentences[1].excerpt) <= store.EXCERPT_CHARS + 1


def test_the_threshold_in_force_classifies_the_sentences(wiki):
    doc_id = make_page(wiki)
    save(wiki, doc_id)
    stored = load(wiki, doc_id)
    assert [s.text for s in stored.unsupported(0.5)] == ["Rinde un 40 % anual."]
    assert stored.unsupported(0.01) == []
    assert len(stored.unsupported(0.95)) == 2
    assert store.report_from_stored(stored, 0.95).unsupported == 2


def test_a_new_verification_replaces_the_previous_one_and_its_sentences(wiki):
    doc_id = make_page(wiki)
    save(wiki, doc_id)
    save(wiki, doc_id)
    with get_connection(wiki[0]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM page_verifications").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM verification_sentences").fetchone()[0] == 2


def test_create_page_overwrite_deletes_the_verification(wiki):
    doc_id = make_page(wiki)
    save(wiki, doc_id)
    create_page(
        wiki[0], wiki[1], "/wiki/concepts/", "uva", "UVA", "Otro texto.\n", [], overwrite=True, language="es")
    assert load(wiki, doc_id) is None
    with get_connection(wiki[0]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM verification_sentences").fetchone()[0] == 0


def test_write_page_content_deletes_the_verification(wiki):
    doc_id = make_page(wiki)
    save(wiki, doc_id)
    assert write_page_content(wiki[0], wiki[1], "/wiki/concepts/", "uva", "Nuevo.\n", language="es")
    assert load(wiki, doc_id) is None


def test_deleting_the_page_deletes_the_verification(wiki):
    doc_id = make_page(wiki)
    save(wiki, doc_id)
    delete_page(wiki[0], wiki[1], "/wiki/concepts/", "uva")
    with get_connection(wiki[0]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM page_verifications").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM verification_sentences").fetchone()[0] == 0


def test_writing_another_page_keeps_the_verification(wiki):
    doc_id = make_page(wiki)
    save(wiki, doc_id)
    make_page(wiki, slug="otra")
    assert load(wiki, doc_id) is not None


def test_state_compares_the_body_and_the_sources(wiki):
    doc_id = make_page(wiki)
    with get_connection(wiki[0]) as conn:
        store.save_verification(conn, doc_id, report(), body_hash=store.text_hash("cuerpo"),
                                source_hashes={"a": "1"})
    stored = load(wiki, doc_id)
    assert store.state(None, "cuerpo", {}) is store.State.NONE
    assert store.state(stored, "cuerpo", {"a": "1"}) is store.State.VALID
    assert store.state(stored, "cuerpo!", {"a": "1"}) is store.State.OUTDATED
    assert store.state(stored, "cuerpo", {"a": "2"}) is store.State.OUTDATED
    assert store.state(stored, "cuerpo", {}) is store.State.OUTDATED


def test_page_body_drops_the_front_matter():
    assert store.page_body("---\ntitle: x\n---\n\nHola.\n") == "\nHola."
    assert store.page_body("Sin front-matter.") == "Sin front-matter."


def test_a_rollback_restores_the_verification_of_the_snapshot(wiki):
    from domain.ingestion.pipeline import _rollback_wiki_pages, _snapshot_wiki_page

    db, root = wiki
    doc_id = make_page(wiki)
    save(wiki, doc_id)
    prior = _snapshot_wiki_page(db, "wiki/concepts/uva.md")
    assert prior["verification"] is not None
    create_page(db, root, "/wiki/concepts/", "uva", "UVA", "Texto de la corrida fallida.\n", [],
                overwrite=True, language="es")
    assert load(wiki, doc_id) is None
    comp = {"dir_path": "/wiki/concepts/", "slug": "uva", "category": "concepts", "prior": prior}
    assert _rollback_wiki_pages(db, root, [comp]) == 1
    restored = load(wiki, doc_id)
    assert restored is not None and restored.checked == 2
