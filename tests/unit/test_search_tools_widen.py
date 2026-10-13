"""The search tools of the model (K2 `search_wiki_fts`, K3 `search_source_chunks`):
every word first; when nothing has every word, any word (the OR query of the
pre-retrieval search, `_fts_query`). A query the wiki matches as written keeps
its results; a query with one word the wiki lacks ("manzana envenenada
simbolismo", cue-15) no longer returns nothing. No model."""

import asyncio
import json
from types import SimpleNamespace

from domain import tracing
from domain.chat.tools import search_source_chunks
from domain.chat.wiki_tools import search_wiki_fts
from domain.text.stemming import stem_text
from domain.tools.db import open_db, seed_workspace_row
from domain.tools.wiki_fs import create_page

PAGE = ("La manzana envenenada es el instrumento con el que la reina malvada duerme a "
        "Blancanieves. La reina prepara la manzana con un veneno y se disfraza. ") * 6


def _wiki(tmp_path):
    workspace = tmp_path / "wiki_es"
    (workspace / "wiki" / "concepts").mkdir(parents=True)
    (workspace / "wiki_config.toml").write_text('[wiki]\nlanguage = "es"\n', encoding="utf-8")
    db_path = str(workspace / ".llmwiki" / "index.db")
    open_db(db_path).close()
    seed_workspace_row(db_path, "wiki_es")
    create_page(db_path, workspace, "/wiki/concepts/", "manzana-envenenada", "Manzana envenenada", PAGE, [])
    conn = open_db(db_path)
    user_id = conn.execute("SELECT user_id FROM workspace").fetchone()[0]
    with conn:
        conn.execute(
            "INSERT INTO documents (id, user_id, filename, title, path, relative_path, "
            "source_kind, file_type, status) VALUES ('s1', ?, 'Blancanieves.pdf', 'Blancanieves', "
            "'sources/', 'sources/Blancanieves.pdf', 'source', 'pdf', 'ready')", (user_id,),
        )
        conn.execute(
            "INSERT INTO document_chunks (id, document_id, chunk_index, content, "
            "content_stemmed, token_count) VALUES ('c1', 's1', 0, ?, ?, 20)",
            (PAGE, stem_text(PAGE, "es")),
        )
    conn.close()
    return workspace, SimpleNamespace(deps=db_path)


def _k_records(workspace):
    path = workspace / ".llmwiki" / "traces" / tracing.SPAN_FILE
    return {
        s["name"]: {k.removeprefix("llmwiki."): v for k, v in s["attributes"].items()
                    if k.startswith("llmwiki.")}
        for s in (json.loads(line) for line in path.read_text().splitlines() if line.strip())
        if s["name"].startswith("K")
    }


def test_a_query_with_every_word_in_the_wiki_is_unchanged(tmp_path):
    _, ctx = _wiki(tmp_path)
    out = search_wiki_fts(ctx, "manzana envenenada")
    assert "manzana-envenenada.md" in out and "some of the words" not in out


def test_a_query_with_a_word_the_wiki_lacks_is_widened_to_any_word(tmp_path):
    _, ctx = _wiki(tmp_path)
    out = search_wiki_fts(ctx, "manzana envenenada simbolismo")
    assert "manzana-envenenada.md" in out
    assert "some of the words" in out          # the model is told the match is partial


def test_a_query_with_no_word_in_the_wiki_still_finds_nothing(tmp_path):
    _, ctx = _wiki(tmp_path)
    assert search_wiki_fts(ctx, "lluvia mañana Rosario").startswith("No wiki pages found")


def test_the_source_tool_is_widened_the_same_way(tmp_path):
    _, ctx = _wiki(tmp_path)
    out = asyncio.run(search_source_chunks(ctx, "manzana envenenada simbolismo"))
    assert "Blancanieves.pdf" in out and "some of the words" in out
    assert asyncio.run(search_source_chunks(ctx, "lluvia mañana Rosario")).startswith("No results found")


def test_the_k_record_says_whether_the_query_was_widened(tmp_path, monkeypatch):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    workspace, ctx = _wiki(tmp_path)
    with tracing.root("turn", workspace):
        search_wiki_fts(ctx, "manzana envenenada simbolismo")
        asyncio.run(search_source_chunks(ctx, "manzana envenenada"))
    rec = _k_records(workspace)
    assert rec["K2"]["widened"] is True and rec["K2"]["substantive"] is True
    assert rec["K3"]["widened"] is False
