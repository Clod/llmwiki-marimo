# LLM Wiki SQLite Data Dictionary

This document provides a comprehensive data dictionary for the SQLite schema defined in [sqlite_schema.sql](../database/sqlite_schema.sql).

---

## 1. Architectural Overview & Design Patterns

### Derived State Principle
The LLM Wiki local index is designed around the concept of **derived state**. The database acts as a high-performance local cache and index for workspace files (such as Markdown files, PDFs, and assets).
* **Rebuildable, with caveats:** The database is a derived index, not the system of record — that role belongs to the files in `sources/` and `wiki/`. It can be repopulated by re-running the ingestion pipeline over the files still in `sources/`, but this is **not** a passive filesystem scan: it re-invokes the LLM, so reconstruction is **non-deterministic** — re-ingesting the same source will not reproduce byte-identical wiki pages, IDs, or chunk boundaries. Re-ingestion also **overwrites** the generated wiki markdown, assigns new internal document IDs, and re-derives the citation graph. Data that exists *only* in the database — extracted page text (`document_pages`), chunks (`document_chunks`), and the summary→source link (`source_document_id`, which is never written to disk) — is recoverable only from the original source files: by re-ingesting them, or by `reindex_from_disk`, described below. Consequences: wiki pages whose source file has been removed from `sources/`, and any manual edits to generated pages, **cannot** be reconstructed. The non-LLM complement is `reindex_from_disk` (`base/domain/tools/reindex.py`): it writes a fresh database from `sources/` and `wiki/` without a model call and without modifying a markdown or a source file, and keeps the old index as `index.db.bak`. It recovers the `documents` rows, `document_pages`, `document_chunks`, `chunks_fts`, the reference graph and each summary's `source_document_id`; `version`, `document_number`, the creation dates and `stale_since` are reset. A wiki page whose source file is gone is registered, and its `cites` edge has no target. See [`ROADMAP.md`](../ROADMAP.md).
* **Sync-Centric Design:** The `documents` table carries `content_hash`, `mtime_ns` and `stale_since`, which detect a difference between a file on disk and its database row. The other tables hang from `documents` and are deleted or rebuilt with it.

### Database Performance Tuning
The database uses two performance-critical SQLite configurations:
1. **Write-Ahead Logging (`PRAGMA journal_mode=WAL;`):** Enables concurrent reading and writing. The request threads of the web interface (`web/`) read the database while an ingestion thread writes to it.
2. **Foreign Key Enforcement (`PRAGMA foreign_keys=ON;`):** Ensures relational integrity. Cascade deletes (`ON DELETE CASCADE`) are heavily utilized to prevent "orphan" pages, chunks, and citations when a document is deleted.

### Schema Source
The runtime schema is exactly [sqlite_schema.sql](../database/sqlite_schema.sql).
`open_db()` (`base/domain/tools/db.py`) applies it verbatim on every connection. Every
statement is `CREATE … IF NOT EXISTS`, so on an existing database the script creates only the
tables and indexes that are missing, and changes nothing else. A table added to the schema
later, such as `page_verifications`, therefore appears empty in an older database.

There is **no migration layer**: no `ALTER TABLE` adds a column to an existing table. Before
applying the schema, `_verify_schema` compares the columns of every existing table with the
columns the schema declares:

1. A table that lacks only `content_stemmed` (in `document_chunks` and `chunks_fts`) is an index
   built before stemming. `_rebuild_stemmed_index` adds the column and rebuilds the full-text
   index, with no model call (§4.1).
2. Any other missing column raises `SchemaMismatchError`, which names the database and the
   missing columns. Without this check the mismatch would surface later as a raw
   `sqlite3.OperationalError`.

If a schema change ever needs to keep the data of a deployed database, the tool is a
`PRAGMA user_version`-gated migration step.

---

## 2. Entity-Relationship (ER) Diagram

```mermaid
erDiagram
    workspace {
        TEXT id PK
        TEXT name
        TEXT description
        TEXT user_id UK
        TEXT created_at
    }

    documents {
        TEXT id PK
        TEXT user_id
        TEXT filename
        TEXT title
        TEXT path
        TEXT relative_path UK
        TEXT source_kind
        TEXT file_type
        INTEGER file_size
        INTEGER document_number
        TEXT status
        INTEGER page_count
        TEXT content
        TEXT tags
        TEXT date
        TEXT metadata
        TEXT error_message
        INTEGER version
        TEXT parser
        TEXT content_hash
        INTEGER mtime_ns
        TEXT last_indexed_at
        TEXT stale_since
        TEXT created_at
        TEXT updated_at
        TEXT source_document_id FK
    }

    document_pages {
        TEXT id PK
        TEXT document_id FK
        INTEGER page
        TEXT content
        TEXT elements
    }

    document_chunks {
        TEXT id PK
        TEXT document_id FK
        INTEGER chunk_index
        TEXT content
        TEXT content_stemmed
        INTEGER page
        INTEGER start_char
        INTEGER token_count
        TEXT header_breadcrumb
        TEXT created_at
    }

    document_references {
        TEXT id PK
        TEXT source_document_id FK
        TEXT target_document_id FK
        TEXT reference_type
        INTEGER page
    }

    chunks_fts {
        TEXT content_stemmed
    }

    page_verifications {
        TEXT id PK
        TEXT document_id FK
        TEXT body_hash
        TEXT source_hashes
        TEXT verifier
        TEXT model
        TEXT verified_at
        INTEGER sentences_checked
        INTEGER pairs_compared
        INTEGER pairs_possible
    }

    verification_sentences {
        TEXT id PK
        TEXT verification_id FK
        INTEGER position
        INTEGER start_char
        INTEGER end_char
        TEXT sentence
        REAL probability
        TEXT best_source
        TEXT best_excerpt
    }

    documents ||--o{ document_pages : "has"
    documents ||--o{ document_chunks : "contains"
    documents ||--o{ document_references : "cites / links (source)"
    documents ||--o{ document_references : "cites / links (target)"
    documents ||--o{ documents : "derived page → source (summary)"
    documents ||--o| page_verifications : "last verification"
    page_verifications ||--o{ verification_sentences : "checked sentences"
    document_chunks ||--|| chunks_fts : "automatically synced to"
```

---

## 3. Table Dictionary & Schema Details

### 3.1. `workspace`
Tracks the global state, configuration, and owner details for the active workspace project.

| Column | Data Type | Key / Constraints | Default Value | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `TEXT` | `PRIMARY KEY` | None | Unique identifier (typically a UUID string) representing this workspace instance. |
| `name` | `TEXT` | `NOT NULL` | None | A human-friendly title of the workspace (e.g., `"My Financial Research"`). |
| `description` | `TEXT` | None | `''` | Optional long-form description outlining the workspace's purpose. |
| `user_id` | `TEXT` | `NOT NULL`, `UNIQUE` | None | The owner's user identifier. The `UNIQUE` constraint guarantees that a user can have at most one active workspace mapping in this database. |
| `created_at` | `TEXT` | None | `(datetime('now'))` | UTC timestamp (`YYYY-MM-DD HH:MM:SS`, from `datetime('now')`) of when this workspace database record was initialized. |

---

### 3.2. `documents`
Stores key metadata, raw text content, and extraction/indexing status of files inside the wiki (e.g., Markdown files, PDFs, source articles).

| Column | Data Type | Key / Constraints | Default Value | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `TEXT` | `PRIMARY KEY` | `(lower(hex(randomblob(16))))` | A custom 32-character hexadecimal UUID string generated natively by SQLite. |
| `user_id` | `TEXT` | `NOT NULL` | None | The identifier of the user who owns or imported the document. |
| `filename` | `TEXT` | `NOT NULL` | None | The literal name of the file (e.g., `federal-reserve.md`). |
| `title` | `TEXT` | None | None | A clean, human-readable title. For a wiki page it is what `create_page` was given — the concept name, or a title derived from the filename — and the same value is written into the page's front-matter, so the row and the file agree by construction. For a source it comes from the file metadata. |
| `path` | `TEXT` | `NOT NULL` | `'/'` | Folder path inside the workspace directory (e.g., `/wiki/concepts`). |
| `relative_path` | `TEXT` | `NOT NULL`, `UNIQUE` | None | The unique relative path from the workspace root. Acts as the primary logical key to find the physical file on disk. |
| `source_kind` | `TEXT` | `NOT NULL`, `CHECK(source_kind IN ('wiki', 'source', 'asset'))` | None | Categorizes the file's function: <br>• `'wiki'`: Generated markdown pages under `wiki/` (summaries/, concepts/).<br>• `'source'`: Read-only source references (like PDFs).<br>• `'asset'`: Accompanying attachments or media files. |
| `file_type` | `TEXT` | `NOT NULL` | None | File extension or MIME category (e.g. `md`, `pdf`, `txt`). |
| `file_size` | `INTEGER` | None | `0` | Physical file size on disk in bytes. |
| `document_number`| `INTEGER` | None | None | Internal sequential number assigned during import or processing passes. |
| `status` | `TEXT` | `CHECK(status IN ('pending', 'processing', 'ready', 'failed'))` | `'pending'` | The parsing lifecycle status of the document:<br>• `'pending'`: Discovered but not yet parsed.<br>• `'processing'`: Text parsing/chunking in progress.<br>• `'ready'`: Fully extracted, chunked, and searchable.<br>• `'failed'`: Encountered processing errors. |
| `page_count` | `INTEGER` | None | None | Total count of extracted pages (meaningful for PDFs; typically `1` for Markdown). |
| `content` | `TEXT` | None | None | Full concatenated plain text content of the document. |
| `tags` | `TEXT` | None | `'[]'` | JSON array string storing tag associations (e.g., `'["finance", "policy"]'`). JSON by convention only — the schema does **not** enforce a `CHECK`/`json_valid` constraint. |
| `date` | `TEXT` | None | None | Arbitrary date metadata associated with the file (e.g., publication date). |
| `metadata` | `TEXT` | None | None | Unstructured JSON metadata containing author, publisher, or parsing-specific payloads. JSON by convention only — no `CHECK`/`json_valid` constraint in the schema. |
| `error_message` | `TEXT` | None | None | Stack trace or failure description if the document `status` is `'failed'`. |
| `version` | `INTEGER` | None | `0` | Incremental revision number used to track updates and invalidate cache layers. |
| `parser` | `TEXT` | None | None | Name of the parsing engine that processed the file. Actual values: `"opendataloader"` (PDF) or `"libreoffice+opendataloader"` (an office file — DOCX, DOC, ODT, RTF — converted to PDF first), or `"text:<encoding>"` (a `.md` or `.txt` file read directly; encoding `utf-8`, `utf-8-sig` or `cp1252`). `NULL` for generated wiki pages (`create_page` does not set it). |
| `content_hash` | `TEXT` | None | None | SHA-256 hash of the source file content (`detector.py` uses `hashlib.sha256`), used to detect modification. |
| `mtime_ns` | `INTEGER` | None | None | The source file's last modified timestamp on disk (in nanoseconds) used for incremental updates. |
| `last_indexed_at`| `TEXT` | None | None | UTC timestamp of when this document was successfully written into the database index. |
| `stale_since` | `TEXT` | None | None | Timestamp marking the moment this record was marked as out-of-sync or needing re-ingestion. |
| `created_at` | `TEXT` | None | `(datetime('now'))` | Timestamp of database record insertion. |
| `updated_at` | `TEXT` | None | `(datetime('now'))` | Timestamp of the last update to this database row. |
| `source_document_id` | `TEXT` | `FOREIGN KEY REFERENCES documents(id) ON DELETE SET NULL` | None | Self-reference linking a *derived* wiki page back to the `'source'` document it was generated from — e.g. each `/wiki/summaries/` page points to its origin PDF. Backs the citation graph. `ON DELETE SET NULL` orphans (rather than destroys) the derived page if the source is deleted. Indexed by `idx_documents_source_doc`. |

---

### 3.3. `document_pages`
Stores the raw extracted text of multi-page files (e.g., PDFs) segregated page-by-page.

| Column | Data Type | Key / Constraints | Default Value | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `TEXT` | `PRIMARY KEY` | `(lower(hex(randomblob(16))))` | A custom 32-character hexadecimal UUID string. |
| `document_id` | `TEXT` | `NOT NULL`, `FOREIGN KEY REFERENCES documents(id) ON DELETE CASCADE` | None | Links to the parent document. If the document is deleted, all pages are cascadingly deleted. |
| `page` | `INTEGER` | `NOT NULL`, `UNIQUE(document_id, page)` | None | The 1-indexed page number of the document. |
| `content` | `TEXT` | `NOT NULL` | None | Raw extracted plain text specific to this page. |
| `elements` | `TEXT` | None | None | Rich structural components (e.g., layout coordinates, tables, images, bounding boxes) stored as a JSON payload. Plain `TEXT` — no JSON constraint in the schema. |

---

### 3.4. `document_chunks`
Stores smaller, semantic fragments (chunks) of documents. They back the **FTS5 keyword search** (`chunks_fts`) and are fed to the chat agent as retrieval context. (There is no vector/embedding search — retrieval is lexical BM25 only.)

| Column | Data Type | Key / Constraints | Default Value | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `TEXT` | `PRIMARY KEY` | `(lower(hex(randomblob(16))))` | A custom 32-character hexadecimal UUID string. |
| `document_id` | `TEXT` | `NOT NULL`, `FOREIGN KEY REFERENCES documents(id) ON DELETE CASCADE` | None | Links to the parent document. Cascade deleted if the document is deleted. |
| `chunk_index` | `INTEGER` | `NOT NULL`, `UNIQUE(document_id, chunk_index)` | None | The order index of this chunk within the parent document (starts at `0`). |
| `content` | `TEXT` | `NOT NULL` | None | The textual payload of the chunk. The reader and the chat agent always receive this text. |
| `content_stemmed` | `TEXT` | `NOT NULL` | `''` | The same text, stemmed with the Snowball stemmer of the wiki's language (`base/domain/text/stemming.py:stem_text`). Only the full-text index `chunks_fts` reads this column (§4.1). |
| `page` | `INTEGER` | None | None | The page number from which this chunk originated (optional). |
| `start_char` | `INTEGER` | None | None | Character offset from the beginning of the original document where this chunk starts. |
| `token_count` | `INTEGER` | `NOT NULL` | None | Number of words or LLM tokens contained inside this chunk. |
| `header_breadcrumb` | `TEXT` | None | None | Structural navigation context (e.g. `"Section 1 > Subsection A > Introduction"`). |
| `created_at` | `TEXT` | None | `(datetime('now'))` | UTC timestamp of chunk record creation. |

---

### 3.5. `document_references`
Represents the directed graph of linkages and citations between workspace documents.

| Column | Data Type | Key / Constraints | Default Value | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `TEXT` | `PRIMARY KEY` | `(lower(hex(randomblob(16))))` | A custom 32-character hexadecimal UUID string. |
| `source_document_id`| `TEXT` | `NOT NULL`, `FOREIGN KEY REFERENCES documents(id) ON DELETE CASCADE` | None | The document containing the link/citation (the outgoing edge). |
| `target_document_id`| `TEXT` | `NOT NULL`, `FOREIGN KEY REFERENCES documents(id) ON DELETE CASCADE` | None | The document being referenced/cited (the incoming edge). |
| `reference_type` | `TEXT` | `NOT NULL`, `CHECK(reference_type IN ('cites', 'links_to'))`, `UNIQUE(source_document_id, target_document_id, reference_type)` | None | The nature of the citation:<br>• `'cites'`: Standard academic citation or structured source reference.<br>• `'links_to'`: A physical hyperlink inside a Markdown file. |
| `page` | `INTEGER` | None | None | Optional page number in the source document where the citation or hyperlink is located. |

---

### 3.6. `page_verifications`
The last sentence-level verification of a wiki page against the text of its sources (`base/domain/verify/store.py`). One row per page: a new verification replaces the previous one. Derived state: it is not rebuilt from disk, but verifying the page again gives the same rows with the same verifier. A database created before this table gets it, empty, when it opens (`CREATE TABLE IF NOT EXISTS`).

| Column | Data Type | Key / Constraints | Default Value | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `TEXT` | `PRIMARY KEY` | `(lower(hex(randomblob(16))))` | A custom 32-character hexadecimal UUID string. |
| `document_id` | `TEXT` | `NOT NULL`, `UNIQUE`, `FOREIGN KEY REFERENCES documents(id) ON DELETE CASCADE` | None | The verified wiki page. Deleting the page deletes its verification. |
| `body_hash` | `TEXT` | `NOT NULL` | None | SHA-256 of the page body without its front-matter, as it was verified. |
| `source_hashes` | `TEXT` | `NOT NULL` | `'{}'` | JSON object: relative path of each context source → SHA-256 of the text used as context. |
| `verifier` | `TEXT` | `NOT NULL` | None | Name of the verifier: `nli:<model>` for the local NLI verifier (`base/domain/verify/nli.py`), `fake` for the verifier of the tests. |
| `model` | `TEXT` | `NOT NULL` | `''` | Model of the verifier, when it has one. |
| `verified_at` | `TEXT` | `NOT NULL` | `(datetime('now'))` | UTC timestamp of the verification. |
| `sentences_checked` | `INTEGER` | `NOT NULL` | `0` | Number of sentences checked. |
| `pairs_compared` | `INTEGER` | `NOT NULL` | `0` | Pairs (passage, sentence) sent to the verifier, after the pre-selection of passages. |
| `pairs_possible` | `INTEGER` | `NOT NULL` | `0` | Pairs a comparison of every sentence with every passage would have sent. |

There is no column for the number of supported sentences: it depends on `[verify] threshold`, which is read when the page is shown.

A write of the page through `create_page` or `write_page_content` deletes its row in the same transaction. A page changed outside the application is detected when it is shown: the hash of the body, or of a context source, differs from the stored one.

---

### 3.7. `verification_sentences`
Every sentence checked in the verification of a page, supported or not.

| Column | Data Type | Key / Constraints | Default Value | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `TEXT` | `PRIMARY KEY` | `(lower(hex(randomblob(16))))` | A custom 32-character hexadecimal UUID string. |
| `verification_id` | `TEXT` | `NOT NULL`, `FOREIGN KEY REFERENCES page_verifications(id) ON DELETE CASCADE` | None | The verification the sentence belongs to. |
| `position` | `INTEGER` | `NOT NULL`, `UNIQUE(verification_id, position)` | None | Order of the sentence in the page body, from 0. |
| `start_char` | `INTEGER` | `NOT NULL` | None | Offset in the page body where the sentence starts. |
| `end_char` | `INTEGER` | `NOT NULL` | None | Offset in the page body where the sentence ends (exclusive). |
| `sentence` | `TEXT` | `NOT NULL` | None | The sentence, as in `body[start_char:end_char]`. |
| `probability` | `REAL` | `NOT NULL` | None | Entailment probability of the best passage, from 0 to 1. A sentence is supported when it reaches the threshold in force. |
| `best_source` | `TEXT` | None | None | Source of the best passage (relative path). `NULL` when no passage was compared. |
| `best_excerpt` | `TEXT` | None | None | An excerpt of the best passage. |

---

## 4. Derived & Virtual Tables (FTS5)

### 4.1. `chunks_fts` (FTS5 Virtual Table)
`chunks_fts` is an **FTS5 Virtual Table**. Unlike regular SQLite tables, a virtual table is managed by an extension module (FTS5 in this case) and is provides the BM25 keyword search over the stemmed text of the chunks.

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content_stemmed,
    content='document_chunks',
    content_rowid='rowid',
    tokenize='unicode61'
);
```

#### Key Virtual Options:
1. **External Content (`content='document_chunks'`):** FTS5 does not duplicate the text data in its own physical storage. Instead, it indexes the `content_stemmed` column of the `document_chunks` table. Search queries join back to `document_chunks` on `rowid` and return `content`, the original text.
2. **Row Mapping (`content_rowid='rowid'`):** Maps the FTS virtual row IDs directly to the standard auto-incrementing SQLite `rowid` of the `document_chunks` table.
3. **Tokenizer (`tokenize='unicode61'`):** splits words and folds accents. FTS5 does no stemming of its own: `content_stemmed` arrives already stemmed by Python with the Snowball stemmer of the wiki's language (`base/domain/text/stemming.py:stem_text`), and `search_chunks` stems every query the same way (`stem_fts_query`). `Los plazos fijos rinden` is stored as `los plaz fij rind`, so `plazo fijo` and `plazos fijos` find the same fragments.

> **An index built before stemming** has no `content_stemmed` column. `open_db`
> detects that and rebuilds the index by itself, with no model call, and logs
> one warning (`base/domain/tools/db.py:_rebuild_stemmed_index`).

---

## 5. Active Database Triggers

Because `chunks_fts` uses an external content configuration, SQLite does not automatically keep the FTS index in sync when rows in `document_chunks` are added, updated, or deleted. The schema implements three auto-synchronization database triggers to enforce this instantly:

### 5.1. `chunks_fts_insert`
Fires automatically whenever a new semantic chunk is inserted into `document_chunks`.
```sql
CREATE TRIGGER IF NOT EXISTS chunks_fts_insert AFTER INSERT ON document_chunks BEGIN
    INSERT INTO chunks_fts(rowid, content_stemmed) VALUES (new.rowid, new.content_stemmed);
END;
```

### 5.2. `chunks_fts_delete`
Fires automatically when a chunk is deleted (including cascade deletes triggered by deleting a parent document).
```sql
CREATE TRIGGER IF NOT EXISTS chunks_fts_delete AFTER DELETE ON document_chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content_stemmed) VALUES('delete', old.rowid, old.content_stemmed);
END;
```

### 5.3. `chunks_fts_update`
Fires automatically when a chunk row is updated. It handles this in two distinct steps within the transaction:
1. Deletes the old index token mapping.
2. Inserts the newly updated content token mapping.
```sql
CREATE TRIGGER IF NOT EXISTS chunks_fts_update AFTER UPDATE ON document_chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content_stemmed) VALUES('delete', old.rowid, old.content_stemmed);
    INSERT INTO chunks_fts(rowid, content_stemmed) VALUES (new.rowid, new.content_stemmed);
END;
```

---

## 6. Speed & Query Optimization Indexes

Speed indexes allow the SQLite engine to locate records in $O(\log N)$ logarithmic time rather than resorting to expensive $O(N)$ full table scans.

| Index Name | Target Table | Columns Indexed | Optimization Target |
| :--- | :--- | :--- | :--- |
| `idx_documents_relative_path` | `documents` | `relative_path` | Crucial for checking file presence and performing single-file metadata lookups during ingestion runs. |
| `idx_documents_path` | `documents` | `path` | Optimizes workspace folder traversal (e.g., listing all documents in `/wiki`). |
| `idx_documents_source_kind` | `documents` | `source_kind` | Speeds up filtering queries (e.g., fetching only user-authored wiki pages). |
| `idx_documents_status` | `documents` | `status` | Speeds up ingestion worker queues (e.g., locating all `pending` or `failed` documents to process). |
| `idx_chunks_doc` | `document_chunks` | `document_id` | Optimizes joining documents with their constituent chunks and mass deletions. |
| `idx_refs_source` | `document_references`| `source_document_id`| Optimizes graph traversal for finding outbound links (what documents does *this* document cite?). |
| `idx_refs_target` | `document_references`| `target_document_id`| Optimizes graph traversal for finding inbound links/backlinks (what documents cite *this* document?). |
| `idx_documents_source_doc` | `documents` | `source_document_id` | Optimizes backlinks from derived wiki pages to their origin document (e.g. summary → source). |
| `idx_verification_sentences_verification` | `verification_sentences` | `verification_id` | Optimizes reading the sentences of a verification. |
