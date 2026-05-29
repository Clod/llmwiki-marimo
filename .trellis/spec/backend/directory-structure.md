# Directory Structure

> How backend/domain code is organized in this project.

---

## Overview

The backend is a single self-contained Python package: `base/`.
There is no web server, no HTTP routes, no Postgres, no Supabase.
Everything runs locally via marimo notebooks that import from `base/` directly.

---

## Directory Layout

```
base/                              # Ingestion pipeline + chat agent (self-contained)
├── __init__.py
├── config.py                         # pydantic-settings — resolves .env from project root
└── domain/
    ├── wiki_registry.py              # wiki discovery + recent list + path hygiene (the runtime picker; pure, no marimo)
    ├── tools/                        # CRUD layer — shared by pipeline, lint, and agent
    │   ├── db.py                     # open_db(), get_connection() — single entry point for SQLite
    │   ├── wiki_fs.py                # create_page, read_page, append_to_page, delete_page
    │   ├── search.py                 # search_chunks() — sync FTS5 with scope filter
    │   ├── references.py             # update_references, get_backlinks, find_orphan_pages, …
    │   └── git_ops.py                # init_wiki_repo(), auto_commit() — workspace git management
    ├── ingestion/
    │   ├── __init__.py               # re-exports: ingest_file, scan_and_ingest, etc.
    │   ├── pipeline.py               # orchestrator — 12-step ingest flow using domain/tools/
    │   ├── batch.py                  # batch_ingest() — deferred overview/log/git for bulk runs
    │   ├── index_manager.py          # deterministic wiki/index.md maintenance (no LLM)
    │   ├── wiki_generator.py         # extract_structured, build_summary_page, build_concept_page,
    │   │                             # update_overview, build_wiki_page (legacy)
    │   ├── extractor.py              # PDF/DOCX → list[tuple[int, str]]
    │   ├── pdf_extract.py            # opendataloader-pdf wrapper (no external deps)
    │   ├── chunker.py                # page content → list[Chunk]
    │   └── detector.py               # change detection (hash + mtime)
    ├── lint/
    │   ├── checks.py                 # six lint check functions (4 deterministic, 2 LLM)
    │   ├── report.py                 # LintIssue, LintReport dataclasses
    │   └── runner.py                 # lint_wiki() — runs all checks, skips LLM if client=None
    ├── repair/
    │   ├── actions.py                # one repair function per lint check type
    │   ├── report.py                 # RepairResult, RepairReport dataclasses
    │   └── runner.py                 # repair_wiki() — dispatches repairs from a LintReport
    └── chat/
        ├── __init__.py               # re-exports: create_agent
        ├── agent.py                  # PydanticAI agent factory
        ├── wiki_tools.py             # read_wiki_page, search_wiki_fts, file_to_wiki
        ├── tools.py                  # search_source_chunks (raw PDF fallback)
        └── config.py                 # load_config() — reads WIKI_PATH/wiki_config.toml

marimo/                           # Marimo applications
├── ingest_app.py                     # Upload → ingest → wiki generation
├── read_app.py                       # Read-only wiki viewer + FTS5 chat (3-column grid)
├── layouts/
│   └── read_app.grid.json            # Grid layout for read_app
├── widgets/                          # Reusable anywidget components (added to sys.path by apps)
│   ├── __init__.py
│   └── delete_confirm.py             # DeleteConfirmWidget — delete button + inline confirm panel
└── prototypes/                       # Exploratory notebooks (not imported by apps)

database/
└── sqlite_schema.sql                 # Canonical SQLite schema (CREATE IF NOT EXISTS)

tests/
├── conftest.py                       # sys.path for base/, pytest_plugins registration
├── helpers/
│   ├── fake_llm.py                   # FakeLLMClient — deterministic LLM stub for tests
│   └── workspace.py                  # tmp_workspace fixture — isolated DB + dir per test
├── unit/                             # 137 tests — fast, no network, FakeLLM
│   ├── test_db.py                    # open_db, get_connection
│   ├── test_fixtures.py              # FakeLLMClient, tmp_workspace fixture
│   ├── test_wiki_fs.py               # create_page, read_page, append_to_page, delete_page
│   ├── test_search.py                # search_chunks (all/wiki/sources scopes)
│   ├── test_references.py            # update_references, backlinks, orphans, uncited
│   ├── test_pipeline_phase2.py       # ingest_file end-to-end with FakeLLM
│   ├── test_batch_ingest.py          # batch_ingest deferred behaviour
│   ├── test_lint_*.py                # one file per lint check type
│   ├── test_repair.py                # repair_wiki dispatcher + all action types
│   └── test_wiki_tools.py            # PydanticAI tool functions (mock RunContext)
├── e2e/                              # 9 tests — live marimo + real LLM via OpenRouter
│   ├── test_ingest_app.py            # Playwright: upload PDF, wait for wiki page in DB
│   └── test_read_app.py              # Playwright: page list, nav, chat panel
└── fixtures/
    ├── pdfs/                         # Source PDFs for e2e ingest tests
    ├── wiki_config.toml              # Test wiki assistant config
    └── workspace/                    # Gitignored; populated by test_ingest_app
```

---

## Module Organization

### base/domain/tools/ — the CRUD layer

Shared by the ingestion pipeline, the lint system, and the chat agent. Import directly
from the module (no `__init__.py` re-export needed):

```python
from domain.tools.db import open_db, get_connection
from domain.tools.wiki_fs import create_page, read_page, append_to_page
from domain.tools.search import search_chunks
from domain.tools.references import update_references, get_backlinks, find_orphan_pages
```

**Rule:** `domain/tools/` functions must not import from `domain/ingestion/`,
`domain/lint/`, `domain/repair/`, or `domain/chat/` at module level. The
dependency flows upward: tools ← pipeline/lint/repair/agent.

**Circular import exception:** `wiki_fs.py` needs `chunk_pages` from `domain/ingestion/chunker`.
Because `domain/ingestion/__init__.py` imports `pipeline`, which imports `wiki_fs`, a top-level
import in `wiki_fs` creates a load-time cycle. Fix: defer the import to inside the function:

```python
def _insert_chunks(conn, doc_id, content):
    from domain.ingestion.chunker import chunk_pages  # deferred — avoids circular import
    chunks = chunk_pages([(1, content)])
    ...
```

Never use a module-level import of `domain.ingestion.*` inside `domain/tools/`.

### base/domain/ingestion/

Each file has a single responsibility. The public API is assembled in `__init__.py`:

```python
from .pipeline import ingest_file, scan_and_ingest, regenerate_wiki_pages
from .extractor import check_libreoffice
```

Callers import from the package, not from individual modules:

```python
from domain.ingestion import ingest_file, check_libreoffice
```

### base/domain/chat/

```python
from domain.chat.agent import create_agent
from domain.chat.config import load_config
```

### sys.path convention in marimo notebooks

`base/` is the only directory added to `sys.path`. The setup block pattern:

```python
_project_root = Path(__file__).parent.parent
_base = str(_project_root / "base")
if _base not in sys.path:
    sys.path.insert(0, _base)
sys.modules.pop("config", None)   # force fresh import of base/config.py
from config import settings
```

---

## Naming Conventions

| Item | Convention | Example |
|------|-----------|---------|
| Module files | `snake_case.py` | `wiki_generator.py` |
| Domain packages | `snake_case/` | `domain/ingestion/` |
| Public functions | `snake_case` | `ingest_file()` |
| Private helpers | `_snake_case` | `_apply_base_schema()` |
| Result dataclasses | `PascalCase` | `IngestResult` |
| Settings class | `Settings` (singleton `settings`) | `settings.WIKI_PATH` |
