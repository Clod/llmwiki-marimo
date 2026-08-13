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

### Deleting a wiki *page* is not the same operation

`delete_page(db_path, workspace, dir_path: str, slug: str) -> bool` in
`base/domain/tools/wiki_fs.py:543` removes one generated page. It returns whether
the page existed (file **or** DB row). Unlike `delete_source` it touches no
source document, but it has two cleanups the FK cascade cannot do, because the
things it must clean are *not* rows pointing at the page.

| Step | What | Why not a cascade |
|---|---|---|
| A | `_strip_dead_links` (`wiki_fs.py:102`) rewrites every wiki page that links to this one, replacing `[Title](href)` with `Title` | the link is markdown *inside another page's content*, not a row |
| B | `document_chunks`, `document_references` (both directions), then the `documents` row | done explicitly so ordering is controlled |
| C | `remove_index_entry` (`index_manager.py:49`) drops the line from `wiki/index.md` | `index.md` has **no row in `documents`**, so nothing cascades to it |
| D | the markdown file, deleted **last** | if any DB step raises, the file survives and the page stays consistent rather than leaving an orphan row |

**Href resolution is the part that broke before.** Pages link by *page-relative*
href — `[Title](other.md)` between neighbours, `[Title](../summaries/other.md)`
across folders — which is what `inject_see_also` and `repair_missing_xref` emit.
A pattern built from the full `wiki/concepts/x.md` form matches none of them. The
current code matches **every** markdown link and resolves each href against the
directory of the page containing it, accepting both the page-relative form and
the workspace-root-relative form (with or without a leading `/`) that older pages
carry. Skips anything with `://`, and strips `#fragment` / `?query` first.

Step C is **best-effort**: wrapped in `try/except`, logging a warning, because a
missing or hand-edited `index.md` must not block the delete. It reads the wiki
language (`load_wiki_language`) since the section header is localized — a Spanish
wiki files entries under `## Conceptos`, and looking for the English header
removes nothing.

**Tests:** `tests/unit/test_wiki_fs.py` —
`test_delete_page_strips_dead_links_from_referencing_page`,
`test_delete_page_strips_links_written_the_way_pages_actually_write_them`,
`test_delete_page_strips_absolute_wiki_links`,
`test_delete_page_removes_the_entry_from_index_md`,
`test_deleting_every_stale_page_leaves_the_wiki_consistent`.

### `stale_since` is cleared only by a regeneration

`create_page(..., clear_stale: bool = False)` (`wiki_fs.py:299`, effect at 432).
The flag means *a source this page cited was deleted; the prose may now be
under-supported*. Clearing is **opt-in and must stay opt-in**: only four of the
eight `overwrite=True` call sites revisit the prose the mark is asking about.

| `clear_stale=True` | Leave the mark |
|---|---|
| ingest concept page (`pipeline.py:281`) | rollback after a failed ingest — restores a prior state, mark included |
| ingest summary page (`pipeline.py:323`) | `crosslink_wiki_pages` — appends a See-also link |
| `regenerate_wiki_pages` (`pipeline.py:688`) | `repair_gap_filled` — swaps a TODO marker for a link |
| `repair_stale` (`repair/actions.py:156`) | `save_to_wiki` — chat merge |

Tying it to `overwrite=True` instead would erase the signal on a See-also append,
making the flag meaningless by the opposite route from never clearing it.

**Tests:** `tests/unit/test_wiki_fs.py::test_regenerating_a_page_clears_its_stale_mark`
and `::test_an_edit_that_is_not_a_regeneration_leaves_the_stale_mark`.

### A `cites` edge may never point at a wiki page

`cites` means *this page took its content from that source document*.
`delete_source` decides what to destroy by following these edges, and lint and
provenance both assume the target is a source — so a page-to-page edge stored as
`cites` is a correctness bug, not untidiness.

`update_references` (`tools/references.py`) matches a citation candidate against
every document **by filename and by title**, and wiki pages carry both. A "See
also" bullet that drifted under `## Sources` therefore resolved to a wiki page
and was stored as a citation. Guarded at `references.py:126`: a candidate whose
`path` starts with `/wiki/` is skipped, whatever the markdown says.

Fixed at the other end too — `repair_missing_xref` used to append with
`append_to_page`, which writes to the *end* of the file, landing the link under
`## Sources` on a generated page (where See also precedes Sources). It now writes
into the See also section, opening one above Sources when absent, matching how
`inject_see_also` positions it at generation time.

**Tests:** `tests/unit/test_repair.py::test_a_cites_record_never_points_at_a_wiki_page`
and `::test_repair_missing_xref_writes_under_see_also_not_under_sources`.

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
