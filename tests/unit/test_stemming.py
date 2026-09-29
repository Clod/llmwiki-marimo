"""Tests for language-aware stemming (design_stemming.md, section 7).

One Snowball stemmer per wiki language serves the FTS index, the queries, the
coverage gate and the Tier-2 verification; the blacklist and the dataset keys
are the two lists compared without stemming. No LLM.
"""

import asyncio
import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from domain.chat.config import load_config
from domain.chat.overlap import coverage
from domain.chat.scope import is_off_limits, mentions_known_data
from domain.chat.tools import search_source_chunks
from domain.text.stemming import stem_fts_query, stem_text
from domain.tools.db import SchemaMismatchError, open_db, seed_workspace_row
from domain.tools.search import search_chunks
from domain.tools.wiki_fs import create_page


# ── 1. stem_text ─────────────────────────────────────────────────────────────

def test_plural_and_singular_reach_the_same_string():
    assert stem_text("plazos fijos", "es") == stem_text("plazo fijo", "es") == "plaz fij"


def test_accent_and_plural_reach_the_same_string():
    assert stem_text("inversión", "es") == stem_text("inversiones", "es") == stem_text("inversion", "es")


def test_underscore_separates_words():
    assert stem_text("plazo_fijo", "es") == "plaz fij"


def test_english_stemmer_is_used_for_en():
    assert stem_text("running", "en") == stem_text("run", "en") == "run"
    assert stem_text("running", "es") == "running"      # the Spanish stemmer leaves it


# ── 2. stem_fts_query ────────────────────────────────────────────────────────

def test_fts_query_keeps_quotes_and_operators():
    assert stem_fts_query('"plazos" OR "fijos"', "es") == '"plaz" OR "fij"'
    assert stem_fts_query('"cauciones" AND NOT "acciones"', "es") == '"caucion" AND NOT "accion"'
    assert stem_fts_query("NEAR(plazos fijos)", "es") == "NEAR(plaz fij)"


# ── 3–4. Search, through search_chunks and the model's source tool ──────────

def _spanish_wiki(tmp_path: Path) -> tuple[Path, str]:
    workspace = tmp_path / "wiki_es"
    (workspace / "wiki" / "concepts").mkdir(parents=True)
    (workspace / "wiki_config.toml").write_text('[wiki]\nlanguage = "es"\n', encoding="utf-8")
    db_path = str(workspace / ".llmwiki" / "index.db")
    open_db(db_path).close()
    seed_workspace_row(db_path, "wiki_es")
    return workspace, db_path


def _source_chunk(db_path: str, content: str) -> None:
    conn = open_db(db_path)
    user_id = conn.execute("SELECT user_id FROM workspace").fetchone()[0]
    with conn:
        conn.execute(
            "INSERT INTO documents (id, user_id, filename, title, path, relative_path, "
            "source_kind, file_type, status) VALUES ('s1', ?, 'fuente.pdf', 'Fuente', "
            "'sources/', 'sources/fuente.pdf', 'source', 'pdf', 'ready')", (user_id,),
        )
        conn.execute(
            "INSERT INTO document_chunks (id, document_id, chunk_index, content, "
            "content_stemmed, token_count) VALUES ('c1', 's1', 0, ?, ?, 5)",
            (content, stem_text(content, "es")),
        )
    conn.close()


def test_search_matches_every_word_form_and_returns_the_original_text(tmp_path):
    workspace, db_path = _spanish_wiki(tmp_path)
    # Longer than MIN_CHUNK_TOKENS (chunker.py), which drops a shorter text.
    text = "Los plazos fijos rinden una tasa fija al vencimiento del depósito. " * 6
    create_page(db_path, workspace, "/wiki/concepts/", "plazo-fijo", "Plazo fijo", text, [])
    plural = search_chunks(db_path, '"plazos" OR "fijos"', scope="wiki")
    singular = search_chunks(db_path, '"plazo" OR "fijo"', scope="wiki")
    assert plural and [r["content"] for r in plural] == [r["content"] for r in singular]
    assert "Los plazos fijos rinden" in plural[0]["content"]      # original, not stemmed


def test_model_source_tool_stems_the_query(tmp_path):
    _, db_path = _spanish_wiki(tmp_path)
    _source_chunk(db_path, "Las cauciones bursátiles son de bajo riesgo.")
    out = asyncio.run(search_source_chunks(SimpleNamespace(deps=db_path), "caución"))
    assert "1 result(s)" in out and "cauciones bursátiles" in out


# ── 5. The coverage gate ─────────────────────────────────────────────────────

def test_plural_question_is_in_the_roster_through_the_category():
    assert mentions_known_data(
        "¿Cuánto rinden los plazos fijos?", {"plazo_fijo"}, language="es",
    )
    # Without stemming the plural does not match — the defect decision 15 fixes.
    assert not mentions_known_data("¿Cuánto rinden los plazos fijos?", {"plazo_fijo"})


def test_dataset_key_matches_only_as_written():
    keys = {"GGAL", "Banco Galicia"}
    assert mentions_known_data("¿Cómo cotiza GGAL?", set(), language="es", exact=keys)
    assert mentions_known_data("tasa del banco galicia", set(), language="es", exact=keys)
    assert not mentions_known_data("¿Cómo cotizan las GGALs?", set(), language="es", exact=keys)


def test_blacklist_does_not_refuse_the_verb_ceder():
    blacklist = ["cedear", "cedears"]
    assert not is_off_limits("¿Me conviene ceder mis acciones a un fondo?", blacklist)
    assert is_off_limits("¿Qué son los CEDEARs?", blacklist)


# ── 6. The Tier-2 verification ───────────────────────────────────────────────

def test_inflected_answer_counts_against_the_fragment():
    fragment = "La caución rinde una tasa fija a siete días."
    answer = "Las cauciones rinden tasas fijas."
    assert coverage(answer, fragment, "es") == 1.0


def test_stop_words_are_removed_before_stemming():
    # "estos" is a stop word; stemmed first it would become "estos" → "est"
    # and could collide with a content word such as "está"/"estable".
    assert coverage("estos", "estable", "es") == 1.0      # empty answer → nothing to support


# ── 7. min_coverage ──────────────────────────────────────────────────────────

def test_min_coverage_is_read_from_pre_retrieval(tmp_path):
    (tmp_path / "wiki_config.toml").write_text(
        "[pre_retrieval]\nenabled = true\nmin_coverage = 0.35\n", encoding="utf-8",
    )
    assert load_config(tmp_path).min_coverage == 0.35


def test_min_coverage_defaults_to_0_2(tmp_path):
    (tmp_path / "wiki_config.toml").write_text("[pre_retrieval]\nenabled = true\n", encoding="utf-8")
    assert load_config(tmp_path).min_coverage == 0.2


# ── 8. The schema guard rebuilds an index built before stemming ─────────────

_OLD_FTS = """
CREATE VIRTUAL TABLE chunks_fts USING fts5(content, content='document_chunks',
    content_rowid='rowid', tokenize='porter unicode61');
CREATE TRIGGER chunks_fts_insert AFTER INSERT ON document_chunks BEGIN
    INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
END;
"""


def _pre_stemming_db(db_path: str) -> None:
    """A database with today's tables but the index of the previous version."""
    open_db(db_path).close()
    seed_workspace_row(db_path, "wiki_es")      # before the downgrade: it opens the db
    old = sqlite3.connect(db_path)
    for trigger in ("chunks_fts_insert", "chunks_fts_delete", "chunks_fts_update"):
        old.execute(f"DROP TRIGGER {trigger}")
    old.execute("DROP TABLE chunks_fts")
    old.execute("ALTER TABLE document_chunks DROP COLUMN content_stemmed")
    old.executescript(_OLD_FTS)
    user_id = old.execute("SELECT user_id FROM workspace").fetchone()[0]
    old.execute(
        "INSERT INTO documents (id, user_id, filename, title, path, relative_path, "
        "source_kind, file_type, status) VALUES ('p1', ?, 'plazo-fijo.md', 'Plazo fijo', "
        "'/wiki/concepts/', 'wiki/concepts/plazo-fijo.md', 'wiki', 'md', 'ready')", (user_id,),
    )
    old.execute(
        "INSERT INTO document_chunks (id, document_id, chunk_index, content, token_count) "
        "VALUES ('c1', 'p1', 0, 'Los plazos fijos rinden una tasa fija.', 7)"
    )
    old.commit()
    old.close()


def test_old_index_is_rebuilt_logged_and_searchable(tmp_path, caplog):
    workspace = tmp_path / "wiki_es"
    (workspace / ".llmwiki").mkdir(parents=True)
    (workspace / "wiki_config.toml").write_text('[wiki]\nlanguage = "es"\n', encoding="utf-8")
    db_path = str(workspace / ".llmwiki" / "index.db")
    _pre_stemming_db(db_path)

    with caplog.at_level(logging.WARNING, logger="domain.tools.db"):
        conn = open_db(db_path)
    stemmed = conn.execute("SELECT content_stemmed FROM document_chunks").fetchone()[0]
    conn.close()
    assert stemmed == "los plaz fij rind una tas fij"
    assert len([r for r in caplog.records if "Rebuilt the search index" in r.message]) == 1
    assert search_chunks(db_path, '"plazo"', scope="wiki")

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="domain.tools.db"):
        open_db(db_path).close()
    assert not caplog.records          # a rebuilt index is current: no second rebuild


def test_any_other_missing_column_still_raises(tmp_path):
    db_path = str(tmp_path / "index.db")
    _pre_stemming_db(db_path)
    old = sqlite3.connect(db_path)
    old.execute("ALTER TABLE document_chunks DROP COLUMN header_breadcrumb")
    old.commit()
    old.close()
    with pytest.raises(SchemaMismatchError):
        open_db(db_path)
