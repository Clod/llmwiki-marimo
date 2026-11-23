<!-- Written 2026-11-20 from database/sqlite_schema.sql. -->

# Data Model

The schema is `database/sqlite_schema.sql`, applied by `open_db`
(`base/domain/tools/db.py`) every time a database opens. Every statement is
`CREATE … IF NOT EXISTS`. One database per workspace, at
`<workspace>/.llmwiki/index.db`, in WAL mode with foreign keys on. Every column is
described in [`sqlite_data_dictionary.md`](../sqlite_data_dictionary.md).

## Tables

```
workspace              one row per workspace
documents              one row per file: source_kind source | wiki | asset;
                       status pending | processing | ready | failed;
                       content_hash, mtime_ns (sources); source_document_id
                       (the source of a summary page); stale_since
document_pages         the text of each page of a source
document_chunks        one row per fragment: content (original text),
                       content_stemmed (roots), page, token_count, header_breadcrumb
document_references    cites (wiki page → source) and links_to (wiki page → wiki page)
chunks_fts             FTS5 index over content_stemmed, tokenizer unicode61,
                       external content; triggers on insert, update and delete
page_verifications     one row per verified page
verification_sentences one row per sentence of a verified page, with its verdict
```

Every child table declares `ON DELETE CASCADE` against its parent. Deleting a
`documents` row deletes its pages, fragments, links and verification, and the
triggers remove its index entries.

## Relationships

```
workspace 1──* documents
documents 1──* document_pages
documents 1──* document_chunks ──── chunks_fts (by rowid, through the triggers)
documents *──* documents through document_references
documents 1──1 page_verifications 1──* verification_sentences
```

## `source_kind`

- `source`: a document under `sources/`.
- `wiki`: a page under `wiki/`. A summary page carries `source_document_id`.
- `asset`: reserved.

`index.md`, `overview.md` and `log.md` have no row.

## Changes to the schema

There is no migration layer, with one exception. A wiki built before stemming
lacks `document_chunks.content_stemmed`. When such a wiki opens, `_verify_schema`
adds the column, stems every row and rebuilds `chunks_fts`, with no model call.
The nodes are `M_LACKS_STEMS` and `M_REBUILD_INDEX` in
[`ingestion_nodes.md`](../ingestion_nodes.md).

## Files next to the database

```
.llmwiki/aliases.generated.toml          aliases written at ingestion
.llmwiki/dataset_aliases.fingerprint     gate of the dataset-alias pass
.llmwiki/snapshots/                      a copy of the index per commit, for rollback
.llmwiki/traces/                         the trace, with WIKI_TRACE=1
```
