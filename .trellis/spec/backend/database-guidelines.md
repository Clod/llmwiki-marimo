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
applies the base schema, and runs any pending migrations.

Do not leave connections open across cell boundaries in marimo notebooks.

---

## Schema Management

The canonical schema lives in `database/sqlite_schema.sql`.
It uses `CREATE TABLE IF NOT EXISTS` throughout — safe to apply on every open.

**Do not modify `database/sqlite_schema.sql` directly.**
Add structural changes via migration functions (see below).

### Migrations

Additive schema changes (new columns, new indexes) are applied in `_ensure_migration()`:

```python
def _ensure_migration(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(
            "ALTER TABLE documents ADD COLUMN source_document_id TEXT "
            "REFERENCES documents(id) ON DELETE SET NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_source_doc "
            "ON documents(source_document_id)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists — safe to ignore
```

Migrations are idempotent: `ALTER TABLE` raises `OperationalError` if the column exists,
which is silently swallowed.

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
