# Database Guidelines

> SQLite patterns and conventions used in this project.

---

## Overview

The project uses SQLite for local, single-user deployments.
The ingestion pipeline (`base/`) uses `sqlite3` directly (synchronous).
The MCP server (`mcp/`) uses `aiosqlite` (async).

**Never use an ORM.** All queries are raw SQL. This is intentional — the schema is
simple and ORM abstractions would add complexity without benefit.

---

## Opening a Connection

Always use `open_db()` from `base/domain/tools/db.py`. It is the single source of
truth for connection setup. Never open a raw `sqlite3.connect()` without setting
`row_factory` and PRAGMAs.

```python
from domain.tools.db import open_db, get_connection

# Long-lived: manage close() yourself
conn = open_db(db_path)
try:
    ...
finally:
    conn.close()

# Short-lived: use the context manager (preferred)
with get_connection(db_path) as conn:
    rows = conn.execute("SELECT ...").fetchall()
```

`open_db()` sets `row_factory = sqlite3.Row`, enables WAL mode and foreign keys,
and applies the canonical schema (idempotent — every statement is
`CREATE … IF NOT EXISTS`, so re-opening an existing DB is a no-op).

Do not leave connections open across cell boundaries in marimo notebooks.

---

## Schema Management

The canonical schema lives in `database/sqlite_schema.sql` and is the **single
source of truth**. It uses `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT
EXISTS` throughout, so `open_db()` applies it safely on every open.

**Make structural changes directly in `database/sqlite_schema.sql`.**
Add a new column to its `CREATE TABLE` body, or a new index to the index block.
There is **no migration layer** — this is a greenfield project with no deployed
databases to upgrade, so the final schema lives in the DDL from the start.

### No migrations (and what to do if that ever changes)

`open_db()` does *not* run a migration step. A `_ensure_migration()` /
`ALTER TABLE` shim once existed for `source_document_id`; it has been removed and
the column folded into the base DDL.

The reason there is no migration framework: with no databases deployed anywhere,
editing the canonical DDL is enough — fresh DBs get the change, and there are no
old-shape DBs to reconcile. `CREATE … IF NOT EXISTS` does **not** alter an
existing table, so this only works while every existing DB is disposable
(dev DBs, the regenerable golden corpus).

If the project ever ships and a *post-release* schema change becomes necessary,
add a real `PRAGMA user_version`-gated migration step at that point — version the
DB, apply each step exactly once in order — and update this section. Do not
reintroduce the broad `try/except OperationalError` shim: it has no version
tracking and silently swallows unrelated errors.

---

## Deletion semantics (`delete_source`)

Deleting a source is **not** a pure FK cascade — `delete_source(db_path,
workspace, doc_id, *, also_delete_file=False)` in
`base/domain/tools/deletion.py` combines DB cascade with relationship-aware
handling of derived pages. Get this contract right; it has been mis-documented
before (the manual test plan once claimed summaries are orphan-kept).

| What | On `delete_source(<a source>)` | Mechanism |
|------|-------------------------------|-----------|
| `document_pages`, `document_chunks`, `chunks_fts`, `document_references` of the source | **deleted** | FK `ON DELETE CASCADE` (+ FTS triggers) |
| 1-to-1 summary page (`source_document_id == doc_id`) | **deleted outright** (row + markdown file) | application logic — no source left to regenerate it from |
| Multi-source concept page (merely *cites* the source) | **kept**, marked `stale_since` | application logic — it may draw on surviving sources |
| `source_document_id` FK | `ON DELETE SET NULL` is only a **backstop** | it never fires for 1-to-1 summaries because they're deleted first |

Invariants that must hold after a delete: no orphan `document_pages` /
`document_chunks` rows, `document_chunks` count == `chunks_fts` count, and zero
dangling `document_references` (both endpoints resolve).

**Tests (assertion points):** `tests/unit/test_delete_source.py`
(`test_delete_source_deletes_derived_wiki_page`,
`test_delete_source_marks_multi_source_concept_stale`) and the end-to-end
invariant over the frozen corpus in
`tests/regression/test_golden_corpus.py::test_deleting_a_source_cascades_and_drops_its_summary`.

> **Regression strategy note.** Deterministic structural invariants like these are
> asserted over the **frozen golden corpus** (`tests/fixtures/golden_corpus/`,
> restored via `tests/helpers/golden.py`) — a real, human-verified ingest checked
> with **no live LLM**. Non-deterministic, model-dependent behavior (chat
> grounding, summary quality) is *not* regressioned; it lives in the manual UAT
> (`docs/uat_test_plan.md` Part B). When you add a deterministic DB/file
> invariant, add it to `tests/regression/`, not to the manual plan.

---

## Query Patterns

### Parameterized queries only

Never concatenate values into SQL strings. Always use `?` placeholders.

```python
# CORRECT
conn.execute(
    "SELECT id FROM documents WHERE relative_path = ?", (relative,)
)

# WRONG — SQL injection risk
conn.execute(f"SELECT id FROM documents WHERE relative_path = '{relative}'")
```

### Row access by name

With `row_factory = sqlite3.Row`, rows support both index and name access.
Always use name access for clarity:

```python
row = conn.execute("SELECT id, status FROM documents WHERE id = ?", (doc_id,)).fetchone()
if row:
    print(row["status"])   # CORRECT
    print(row[1])          # WRONG — brittle
```

### Explicit column lists in SELECT

Never use `SELECT *` in production code. List columns explicitly so changes to the
schema don't silently break callers.

```python
# CORRECT
cursor = conn.execute(
    "SELECT filename, status, page_count, parser, error_message, updated_at "
    "FROM documents WHERE source_kind='source' ORDER BY filename"
)

# WRONG
cursor = conn.execute("SELECT * FROM documents")
```

---

## Transaction Handling

Use `with conn:` for atomic multi-step writes. It commits on success and rolls back
on exception — no need for manual `try/except/rollback`:

```python
with get_connection(db_path) as conn:
    with conn:   # transaction
        conn.execute("DELETE FROM document_chunks WHERE document_id=?", (doc_id,))
        conn.executemany("INSERT INTO document_chunks ...", rows)
```

For long-lived connections (e.g. `pipeline.py` where one connection spans many steps),
use explicit `conn.commit()` and `try/finally conn.close()`:

```python
conn = open_db(db_path)
try:
    conn.execute("UPDATE documents SET status='processing' WHERE id=?", (doc_id,))
    conn.commit()
    # ... more work ...
    with conn:   # atomic block within the same connection
        conn.execute("UPDATE documents SET status='ready' WHERE id=?", (doc_id,))
        conn.executemany("INSERT INTO document_chunks ...", rows)
except Exception:
    conn.execute("UPDATE documents SET status='failed' ... WHERE id=?", (doc_id,))
    conn.commit()
    raise
finally:
    conn.close()
```

---

## Naming Conventions

| Item | Convention | Example |
|------|-----------|---------|
| Tables | `snake_case` plural | `documents`, `workspace` |
| Columns | `snake_case` | `source_kind`, `page_count` |
| Indexes | `idx_<table>_<column>` | `idx_documents_source_doc` |
| UUIDs | `str(uuid.uuid4())` | all `id` columns |
| Timestamps | `datetime('now')` in SQL | `created_at`, `updated_at` |

---

## Common Mistakes

- **Forgetting to close connections** — leads to locked DB on subsequent runs
- **Not enabling WAL mode** — causes write contention when marimo re-runs cells
- **Reading rows without name access** — breaks silently when column order changes
- **Missing `PRAGMA foreign_keys=ON`** — FK constraints are disabled by default in SQLite
- **Using SELECT \*** — breaks if columns are added or reordered
