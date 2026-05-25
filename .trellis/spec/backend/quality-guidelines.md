# Quality Guidelines

> Code quality standards for backend/domain development.

---

## Overview

These standards apply to `api_new/` and `mcp/`.

---

## Forbidden Patterns

### 1. String concatenation in SQL

Never build SQL with f-strings or `.format()`. Always use `?` placeholders.

### 2. `SELECT *` in production queries

Enumerate column names explicitly. Schema changes should not silently alter query results.

### 3. Bare `except:` clauses

Always catch specific exception types. Use `except Exception` as the widest acceptable catch.

```python
# WRONG
try:
    conn.execute(...)
except:
    pass

# CORRECT
try:
    conn.execute(...)
except sqlite3.OperationalError:
    pass  # column already exists
```

### 4. Leaving DB connections open

Always close connections in `finally` blocks. Never return a connection from a function.

### 5. Hardcoded model names or paths in domain code

Domain functions receive `model: str` and `workspace: Path` as parameters.
They never import `settings` directly — that is the notebook's job.

### 6. Mutable default arguments

```python
# WRONG
def ingest_file(..., errors: list = []):

# CORRECT
def ingest_file(..., errors: list | None = None):
    if errors is None:
        errors = []
```

---

## Required Patterns

### Single-responsibility modules

Each file in `api_new/domain/ingestion/` has one job:
- `pipeline.py` — orchestration + DB helpers
- `extractor.py` — text extraction only
- `chunker.py` — chunking only
- `detector.py` — change detection only
- `wiki_generator.py` — LLM call only

### Public API assembled in `__init__.py`

Callers import from the package, not from individual modules.

```python
# CORRECT
from domain.ingestion import ingest_file

# WRONG
from domain.ingestion.pipeline import ingest_file
```

### Module-level logger

Every module declares its logger at module level:

```python
logger = logging.getLogger(__name__)
```

### Type annotations on all public functions

See `type-safety.md` for details.

---

## Testing Requirements

| Layer | Coverage target | Tool |
|-------|----------------|------|
| Domain logic | 80%+ | pytest + pytest-cov |
| E2E (marimo) | Critical paths | Playwright |
| Integration | DB lifecycle | pytest |

Run unit and integration tests before committing domain changes:

```bash
uv run pytest tests/unit/ tests/integration/ -v
```

E2E tests are slower — run explicitly:

```bash
HEADLESS=1 uv run pytest tests/e2e/ -v -s
```

---

## Code Review Checklist

- [ ] All public functions have type annotations
- [ ] No `SELECT *` in SQL queries
- [ ] No bare `except:` clauses
- [ ] DB connections closed in `finally`
- [ ] Domain functions receive config as parameters (no internal `settings` import)
- [ ] Module-level `logger = logging.getLogger(__name__)`
- [ ] Result objects returned for expected failures, not exceptions
- [ ] Unit tests added for new domain logic
