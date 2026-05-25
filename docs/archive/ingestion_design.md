> **ARCHIVED — historical reference only.**
> Superseded by [`docs/programmer_manual.md`](../programmer_manual.md).
> Preserved for design rationale and traceability.

---

# Document Ingestion Workflow — Design

## 0. Prototype Isolation Strategy

**No existing files are modified.** All new code lives in `*_new` sibling directories.
When the prototype is validated, the migration to the final project is a controlled merge:

| Prototype directory | Final destination | Migration action |
|---|---|---|
| `api_new/` | `api/` | Copy new modules in; merge config |
| `shared_new/` | `shared/` | Apply migration SQL to schema |
| `marimo_new/` | `marimo/` | Merge new cells into `load_app.py` |

**Cleanup is a single step:** delete `api_new/`, `shared_new/`, `marimo_new/`.

---

## 1. Decisions Summary

| Topic | Decision |
|---|---|
| Source directory | `WIKI_PATH/sources/` (new, created on first run) |
| Wiki pages directory | `WIKI_PATH/wiki/` (existing) |
| DB schema | New migration file in `shared_new/` — applied on top of existing DB |
| Schema change | Add `source_document_id` FK to `documents` |
| Change detection | mtime_ns first, SHA-256 hash to confirm |
| Supported formats | PDF, DOCX (DOCX requires LibreOffice — documented, fails gracefully) |
| LLM for wiki generation | Separate configurable endpoint via `.env` |
| Re-ingestion | Full atomic overwrite: document + pages + chunks + wiki page |
| Failed ingestion | Whole pipeline fails, status=`failed`, nothing partial survives |
| Wiki page storage | Written to `WIKI_PATH/wiki/` filesystem **and** `documents` table (`source_kind='wiki'`) |
| Standalone wiki regen | Yes, separate callable operation |
| Triggers | Python functions (callable from Marimo and CLI) |
| Marimo UI | New notebook `marimo_new/ingest_app.py` |
| Progress reporting | Spinner while running → step log when done |
| File watcher | Kept as-is, **not used** in local-only design |

---

## 2. Project Directory Layout

```
llmwiki/
├── api/                    # ← UNTOUCHED (original)
├── shared/                 # ← UNTOUCHED (original)
├── marimo/                 # ← UNTOUCHED (original)
│
├── api_new/                # ← NEW: all new backend code
│   ├── config.py           #   standalone config (declares only what ingestion needs)
│   └── domain/
│       └── ingestion/
│           ├── __init__.py
│           ├── pipeline.py
│           ├── detector.py
│           ├── extractor.py
│           └── wiki_generator.py
│
├── shared_new/             # ← NEW: schema migration only
│   └── migration_001.sql   #   ALTER TABLE + new index
│
└── marimo_new/             # ← NEW: ingestion UI notebook
    └── ingest_app.py
```

### Workspace directory layout (unchanged)

```
WIKI_PATH/
├── sources/            # ← NEW at runtime: uploaded PDFs, DOCXs
├── wiki/               # Generated wiki pages (.md files)
└── .llmwiki/
    ├── index.db        # SQLite database
    └── cache/
        └── local/
            └── {doc_id}/
                └── converted.pdf   # LibreOffice output (DOCX only)
```

---

## 3. Schema Migration

**File:** `shared_new/migration_001.sql`

Applied once with: `sqlite3 $DB_PATH < shared_new/migration_001.sql`

```sql
-- Migration 001: add source_document_id for wiki page ↔ source document linkage
ALTER TABLE documents
    ADD COLUMN source_document_id TEXT
    REFERENCES documents(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_documents_source_doc
    ON documents(source_document_id);
```

All other columns needed by the ingestion pipeline (`content_hash`, `mtime_ns`,
`last_indexed_at`) already exist in `shared/sqlite_schema.sql`.

> **Migration note:** The pipeline applies this migration automatically on first run via
> `executescript` — `IF NOT EXISTS` / `IF column not exists` guards make it idempotent.

> **`UNIQUE(relative_path)`** makes upsert natural — ingesting the same file twice reuses the same DB row.

---

## 4. Configuration

**File:** `api_new/config.py` — standalone, does **not** import from `api/config.py`

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    WORKSPACE_PATH: str = "."

    # PDF extraction backend
    PDF_BACKEND: str = "opendataloader"   # or "mistral"
    MISTRAL_API_KEY: str = ""

    # Main LLM (chat, read_app) — re-declared here as fallback for WIKI_LLM_*
    LLM_BASE_URL: str = "https://openrouter.ai/api/v1"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "anthropic/claude-haiku-4-5"

    # Wiki page generation LLM — falls back to LLM_* if unset
    WIKI_LLM_BASE_URL: str = ""
    WIKI_LLM_API_KEY: str = ""
    WIKI_LLM_MODEL: str = ""

    @property
    def wiki_base_url(self) -> str:
        return self.WIKI_LLM_BASE_URL or self.LLM_BASE_URL

    @property
    def wiki_api_key(self) -> str:
        return self.WIKI_LLM_API_KEY or self.LLM_API_KEY

    @property
    def wiki_model(self) -> str:
        return self.WIKI_LLM_MODEL or self.LLM_MODEL

settings = Settings()
```

### `.env` additions

```ini
# Wiki page generation LLM — omit to reuse main LLM settings
WIKI_LLM_BASE_URL=    # https://openrouter.ai/api/v1
                      # http://localhost:11434/v1  (Ollama)
                      # http://localhost:1234/v1   (LM Studio)
WIKI_LLM_API_KEY=     # leave empty for Ollama / LM Studio
WIKI_LLM_MODEL=       # llama3.2, mistral-small, etc.
```

The `openai.OpenAI(base_url=..., api_key=...)` client works identically with
OpenRouter, Ollama, and LM Studio — no code changes needed to switch providers.

---

## 5. Imports from `api/`

`api_new/` code reuses utilities from `api/` **read-only** (no files modified).
Each module that needs them adds `api/` to `sys.path` at the top:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "api"))
```

| `api_new/` module | Imports from `api/` |
|---|---|
| `extractor.py` | `services.pdf_extract.extract_pdf` |
| `extractor.py` | `domain.local_processor._process_office` (DOCX conversion block) |
| `pipeline.py` | `services.chunker.chunk_pages` |
| `pipeline.py` | `infra.db.sqlite.SQLiteDocumentRepository` |

---

## 6. New Module: `api_new/domain/ingestion/`

### `detector.py`

**Responsibility:** Decide whether a file needs ingestion based on mtime + hash.

| Function | Description |
|---|---|
| `compute_file_hash(path) -> str` | SHA-256 of file bytes |
| `needs_ingestion(path, db) -> tuple[bool, str]` | Returns `(needs_processing, current_hash)` |

**Reference:** `api/domain/watcher.py` — `_index_file()` contains exactly this logic.
Extract and reuse.

---

### `extractor.py`

**Responsibility:** Given a file path, return `list[tuple[int, str]]` (page_num, markdown).

| Function | Description |
|---|---|
| `extract(file_path, cache_dir) -> list[tuple[int, str]]` | Routes by extension |
| `_extract_pdf(path) -> list[tuple[int, str]]` | Calls `extract_pdf()` directly |
| `_extract_docx(path, cache_dir) -> list[tuple[int, str]]` | LibreOffice → PDF → `extract_pdf()` |
| `check_libreoffice() -> bool` | `shutil.which("libreoffice") or shutil.which("soffice")` |

**LibreOffice check behaviour:**
- Called before any DOCX file is processed
- If not installed: raises `LibreOfficeNotInstalledError` with message:
  `"LibreOffice is required to process .docx files. Install: brew install --cask libreoffice"`
- Pipeline catches this, sets `status='failed'` with that message
- Marimo UI shows a startup warning callout if LibreOffice is absent

**Reference:** `api/domain/local_processor.py` — `_process_pdf()`, `_process_office()`.
DOCX→PDF block already written; lift directly.

**Reference:** `api/services/pdf_extract.py` — `extract_pdf()`. Used as-is via sys.path import.

---

### `wiki_generator.py`

**Responsibility:** Generate a structured wiki page markdown string via LLM.

| Function | Description |
|---|---|
| `build_wiki_page(doc_meta, page_contents, client, model) -> str` | LLM call → full markdown |
| `make_wiki_slug(filename) -> str` | `stem.lower().replace(" ", "-")` |

**Wiki page template (generic — to be specialized for finance later):**

```markdown
# {title}

**Source:** {filename} | **Type:** {file_type} | **Pages:** {page_count} | **Ingested:** {date}

## Summary
{2-4 paragraph narrative summary of the document}

## Key Topics
{bullet list of main themes and topics}

## Key Entities
{people, organizations, places, dates mentioned}

## Important Data & Figures
{notable numbers, statistics, conclusions}

## Source Information
- **File:** {filename}
- **Type:** {file_type}
- **Pages:** {page_count}
- **Ingested:** {datetime}
- **Parser:** {parser}
```

**LLM call strategy:**
- Input: full extracted text (all pages joined) up to a reasonable token limit
- Documents >20 pages: use first 5 pages + last 2 pages + any detected table of contents
- Single call, `temperature=0.3` for consistency

**Reference:** `marimo/read_app.py` — `chat_panel` cell for OpenAI client setup pattern.

---

### `pipeline.py`

**Responsibility:** Orchestrator for all three public operations. Handles atomicity,
error handling, and progress reporting.

**Public API:**

```python
@dataclass
class IngestResult:
    file_path: Path
    status: Literal["ingested", "skipped", "failed"]
    message: str
    doc_id: str | None = None

async def ingest_file(
    file_path: Path,
    db: aiosqlite.Connection,
    workspace: Path,
    llm_client: openai.OpenAI,
    model: str,
    progress_cb: Callable[[str], None] | None = None,
) -> IngestResult: ...

async def scan_and_ingest(
    workspace: Path,
    db: aiosqlite.Connection,
    llm_client: openai.OpenAI,
    model: str,
    progress_cb: Callable[[str], None] | None = None,
) -> list[IngestResult]: ...

async def regenerate_wiki_pages(
    workspace: Path,
    db: aiosqlite.Connection,
    llm_client: openai.OpenAI,
    model: str,
    progress_cb: Callable[[str], None] | None = None,
) -> list[IngestResult]: ...
```

---

## 7. Step-by-Step Pipelines

### 7a. `ingest_file` — Single File

```
Step 1  VALIDATE
        - File exists on disk
        - Extension is .pdf or .docx (case-insensitive)
        - If .docx: check_libreoffice() — raise LibreOfficeNotInstalledError if missing
        → progress_cb("🔍 Validating {filename}")
        Reference: api_new/domain/ingestion/extractor.py::check_libreoffice()

Step 2  DETECT CHANGES
        - Compute mtime_ns from stat()
        - Query DB: SELECT mtime_ns, content_hash FROM documents WHERE relative_path = ?
        - If mtime_ns matches → compute SHA-256 hash → if hash matches → return SKIPPED
        → progress_cb("⏭ {filename} — already up to date") if skipped
        Reference: api_new/domain/ingestion/detector.py
                   (logic from api/domain/watcher.py::_index_file)

Step 3  UPSERT DOCUMENT RECORD (status='processing')
        - INSERT OR REPLACE into documents:
            id             — new uuid4 if not exists, keep existing id if updating
            filename, title, path='sources/', relative_path='sources/{filename}'
            source_kind    — 'source'
            file_type      — 'pdf' or 'docx'
            file_size      — from stat()
            status         — 'processing'
            content_hash   — SHA-256 computed in step 2
            mtime_ns       — from stat()
            last_indexed_at — now()
        → progress_cb("⚙️ Extracting text from {filename}")
        Reference: api/infra/db/sqlite.py::SQLiteDocumentRepository (imported via sys.path)

Step 4  EXTRACT TEXT
        - Call extractor.extract(file_path, cache_dir)
        - Returns list[tuple[int, str]] — (page_num, markdown)
        → progress_cb("✅ Extracted {n} pages")
        Reference: api_new/domain/ingestion/extractor.py
                   (wraps api/services/pdf_extract.py and api/domain/local_processor.py)

Step 5  CHUNK TEXT
        - Call chunk_pages(page_contents)
        - Returns list[Chunk]
        → progress_cb("✂️ Chunked into {n} chunks")
        Reference: api/services/chunker.py::chunk_pages() (imported via sys.path)

Step 6  GENERATE WIKI PAGE
        - Call wiki_generator.build_wiki_page(doc_meta, page_contents, client, model)
        - Returns full markdown string
        → progress_cb("🤖 Generating wiki page...")
        Reference: api_new/domain/ingestion/wiki_generator.py

Step 7  ATOMIC DB WRITE (single transaction)
        a. UPDATE documents SET
               status='ready', content={full_text}, page_count={n},
               parser={parser}, updated_at=now()
           WHERE id = {doc_id}
        b. DELETE FROM document_pages WHERE document_id = {doc_id}
           INSERT INTO document_pages (doc_id, page, content) × n pages
        c. DELETE FROM document_chunks WHERE document_id = {doc_id}
           INSERT INTO document_chunks × n chunks
           (FTS5 triggers fire automatically — no manual FTS update needed)
        d. UPSERT wiki page document:
           INSERT OR REPLACE INTO documents (
               source_kind='wiki', file_type='md',
               relative_path='wiki/{slug}.md',
               status='ready', content={wiki_markdown},
               source_document_id={doc_id}
           )
        e. DELETE + INSERT chunks for the wiki page document
        → progress_cb("💾 Saved to database")
        Reference: api/infra/db/sqlite.py (imported via sys.path)

Step 8  WRITE WIKI PAGE TO FILESYSTEM
        - Write wiki_markdown to WIKI_PATH/wiki/{slug}.md
        → progress_cb("📄 Wiki page written: wiki/{slug}.md")
        Reference: marimo/load_app.py::write_page() (pattern only — reimplemented inline)

        ── ERROR HANDLING ──────────────────────────────────────────────────
        On ANY exception in steps 4–8:
        → UPDATE documents SET status='failed', error_message={str(e)} WHERE id={doc_id}
        → Delete wiki page file if partially written
        → Return IngestResult(status='failed', message=str(e))
        Nothing partial survives — either fully committed or fully rolled back.
```

---

### 7b. `scan_and_ingest` — Bulk Scan

```
Step 1  ENSURE sources/ EXISTS
        - WIKI_PATH/sources/.mkdir(parents=True, exist_ok=True)
        → progress_cb("🔎 Scanning sources/...")

Step 2  SCAN DIRECTORY
        - Walk WIKI_PATH/sources/ recursively
        - Collect files with extensions: .pdf, .docx (case-insensitive)
        - Skip hidden files and directories (names starting with .)
        → progress_cb(f"🔎 Found {n} candidate files")
        Reference: api/domain/watcher.py::_should_ignore() (pattern only)

Step 3  FOR EACH FILE — CHECK CHANGES
        - Call detector.needs_ingestion(path, db)
        - If unchanged → record as SKIPPED with "⏭ already up to date"
        - If new or modified → add to processing queue

Step 4  FOR EACH FILE IN QUEUE — INGEST
        - Call ingest_file(path, db, workspace, llm_client, model, progress_cb)
        - Collect IngestResult per file
        - Continue on failure (one bad file does not abort the scan)

Step 5  RETURN
        - Return list[IngestResult] (all files: ingested + skipped + failed)
        - Caller (Marimo) renders per-file summary
```

---

### 7c. `regenerate_wiki_pages` — Standalone Wiki Regeneration

Use case: LLM model changed, wiki template updated, or wiki pages accidentally deleted.

```
Step 1  QUERY ALL READY SOURCE DOCUMENTS
        - SELECT id, filename, file_type, page_count, parser
          FROM documents
          WHERE source_kind = 'source' AND status = 'ready'
        → progress_cb(f"Found {n} source documents")

Step 2  FOR EACH DOCUMENT
        a. Load page_contents:
           SELECT page, content FROM document_pages
           WHERE document_id = ? ORDER BY page
        b. Generate wiki page:
           wiki_generator.build_wiki_page(doc_meta, page_contents, client, model)
        c. Upsert wiki document record in DB (same as step 7d of ingest_file)
        d. Write WIKI_PATH/wiki/{slug}.md to filesystem
        → progress_cb per document (success or failure)

Step 3  RETURN
        - Return list[IngestResult]
```

---

## 8. Marimo UI (`marimo_new/ingest_app.py`)

New standalone notebook — does **not** modify `marimo/load_app.py`.

### Cells

| Cell | Responsibility |
|---|---|
| `setup` | Load `.env`, build paths, open SQLite connection, ensure `sources/` exists |
| `llm_setup` | Build `openai.OpenAI` from `settings.wiki_base_url` / `wiki_api_key` |
| `libreoffice_check` | Startup: if LibreOffice absent, show `mo.callout(..., kind="warn")` |
| `source_uploader` | `mo.ui.file(filetypes=[".pdf",".docx"])` → saves to `sources/` |
| `ingest_btn` | "⚙️ Ingest uploaded file" → calls `ingest_file()` |
| `scan_btn` | "🔄 Scan sources/ for changes" → calls `scan_and_ingest()` |
| `regen_btn` | "🤖 Regenerate all wiki pages" → calls `regenerate_wiki_pages()` |
| `progress_display` | Accumulates `progress_cb` messages; spinner while running, step log when done |
| `sources_list` | Shows all files currently in `sources/` with their DB status |

### Progress display pattern

```python
log_lines, set_log_lines = mo.state([])

def progress_cb(message: str):
    set_log_lines(log_lines() + [message])

# While running:  mo.status.spinner("Processing...")
# When complete:  mo.md("\n".join(f"- {line}" for line in log_lines()))
```

---

## 9. LibreOffice — Documentation

> ⚠️ **Must be documented in the project README.**

DOCX files require LibreOffice for conversion to PDF before text extraction.

**Install on macOS:**
```bash
brew install --cask libreoffice
```

**Install on Linux:**
```bash
sudo apt-get install libreoffice
```

**Install on Windows:**
```powershell
winget install TheDocumentFoundation.LibreOffice
```
Or download the installer from https://www.libreoffice.org/download/libreoffice/

**Behaviour when not installed:**
- Uploading a `.docx` → immediate `failed` status with install instructions in the error
- Bulk scan with `.docx` files → those files fail individually, PDFs continue unaffected
- `ingest_app.py` shows a persistent warning callout at startup if LibreOffice is absent

---

## 10. Files to Ignore

| File | Reason |
|---|---|
| `api/domain/watcher.py` | Not used — keep for reference; `detector.py` lifts logic from it |
| `api/services/ocr.py` | Hosted mode only (S3 + Postgres) |
| `api/services/hosted.py` | Hosted mode only |
| `api/infra/tus.py` | Resumable upload protocol — not needed |
| `api/routes/local_upload.py` | Upload goes through Marimo, not FastAPI |
| `api/services/s3.py` | No S3 in local mode |
| `web/` | Replaced by Marimo |
| `e2e/` | Tests for the original web frontend |

---

## 11. Implementation Order

1. `shared_new/migration_001.sql` — schema migration file
2. `api_new/config.py` — standalone config with `WIKI_LLM_*` settings
3. `api_new/domain/ingestion/detector.py`
4. `api_new/domain/ingestion/extractor.py` + `LibreOfficeNotInstalledError`
5. `api_new/domain/ingestion/wiki_generator.py`
6. `api_new/domain/ingestion/pipeline.py`
7. `api_new/domain/ingestion/__init__.py`
8. `marimo_new/ingest_app.py`

Each module is independently testable before the next is built.

---

## 12. Migration to Final Project (post-prototype)

When the prototype is validated:

1. `cp -r api_new/domain/ingestion api/domain/`
2. Merge `api_new/config.py` fields into `api/config.py`
3. `sqlite3 $DB_PATH < shared_new/migration_001.sql`
4. Merge ingestion cells from `marimo_new/ingest_app.py` into `marimo/load_app.py`
5. `rm -rf api_new/ shared_new/ marimo_new/`
