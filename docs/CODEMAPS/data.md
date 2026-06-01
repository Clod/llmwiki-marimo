<!-- Generated: 2026-06-01 | Files scanned: 1 | Token estimate: ~600 -->
<!-- 2026-06-01: source_document_id + idx folded into base DDL; runtime migration removed. -->

# Data Model

Single source of truth: `database/sqlite_schema.sql`. SQLite, WAL mode,
`PRAGMA foreign_keys=ON`. Applied by `tools/db.py:open_db`. One DB per workspace
at `workspace/.llmwiki/index.db`.

## Tables

```
workspace            id, name, user_id                       one row per workspace
documents            id, user_id, filename, title, path, relative_path (UNIQUE),
                     source_kind CHECK(wiki|source|asset), file_type, status
                     CHECK(pending|processing|ready|failed), content, tags(JSON),
                     page_count, version, source_document_id, content_hash,
                     stale_since, created_at, updated_at, ...
document_pages       id, document_id→documents, page, content   (raw per-page text)
document_chunks      id, document_id→documents, chunk_index, content, page,
                     token_count(NOT NULL), header_breadcrumb
                     UNIQUE(document_id, chunk_index)
document_references  id, source_document_id→documents, target_document_id→documents,
                     reference_type CHECK(cites|links_to), page
                     UNIQUE(source, target, reference_type)   ON DELETE CASCADE
chunks_fts           FTS5 virtual table (content) — mirrors document_chunks
```

## Relationships

```
workspace 1──* documents
documents 1──* document_pages       (raw extracted pages)
documents 1──* document_chunks ──1:1── chunks_fts (FTS index)
documents *──* documents via document_references
              cites    : wiki page → source doc   (citation)
              links_to : wiki page → wiki page     (cross-link)
```

## source_kind semantics

- `source` — immutable raw file under `sources/` (PDF/DOCX).
- `wiki`   — generated markdown under `wiki/` (summaries/, concepts/).
  Summary pages carry `source_document_id`; concept pages don't.
- `asset`  — reserved (images, future).

## Indexes

`relative_path`, `path`, `source_kind`, `status`, `source_document_id` on
documents; `document_id` on chunks; `source`/`target` on references.

## Migrations

None. `database/sqlite_schema.sql` is the single canonical schema, applied
verbatim by `db.py:open_db` (all statements are `CREATE … IF NOT EXISTS`, so
re-opening is a no-op). Greenfield project, no deployed DBs to upgrade.
