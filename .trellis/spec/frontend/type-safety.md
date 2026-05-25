# Type Safety

> Type annotation conventions for this Python/marimo project.

---

## Overview

This project uses Python type hints throughout domain code (`api_new/`).
Marimo notebook cells are more loosely typed by necessity — annotations are added
where they add clarity but are not enforced by a type checker at CI time.

---

## Domain Code (api_new/)

All public functions in `api_new/` must have full type annotations on parameters
and return types.

```python
# api_new/domain/ingestion/pipeline.py
def ingest_file(
    file_path: Path,
    db_path: str,
    workspace: Path,
    llm_client,                          # OpenAI — no stub available
    model: str,
    progress_cb: Callable[[str], None] | None = None,
) -> IngestResult:
    ...
```

### Result types as dataclasses

Use `@dataclass` for typed result objects instead of returning raw dicts or tuples.

```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class IngestResult:
    file_path: Path
    status: Literal["ingested", "skipped", "failed"]
    message: str
    doc_id: str | None = None
```

### Literal for status fields

Use `Literal` for fields that have a fixed set of string values:

```python
status: Literal["ingested", "skipped", "failed"]
source_kind: Literal["source", "wiki"]
```

### Union types — use `X | Y` syntax (Python 3.10+)

```python
# CORRECT (project requires Python >=3.12)
doc_id: str | None = None
progress_cb: Callable[[str], None] | None = None

# WRONG — old style
from typing import Optional, Union
doc_id: Optional[str] = None
```

---

## Configuration (pydantic-settings)

All settings are typed via `pydantic_settings.BaseSettings`. This gives runtime
validation and IDE autocomplete for free.

```python
class Settings(BaseSettings):
    WIKI_PATH: str = "."
    LLM_MODEL: str = "anthropic/claude-haiku-4-5"
    LLM_API_KEY: str = ""
```

---

## Marimo Cell Code

Cell code does not carry strict annotations — marimo's reactive DAG passes values
implicitly through parameter names. Focus on:

- Private cell-local variables prefixed with `_`
- Domain functions (called from cells) being fully typed
- Avoiding `Any` or untyped dicts leaking from domain to cell boundary

---

## Forbidden Patterns

- `from __future__ import annotations` inside marimo cells — breaks marimo's
  introspection of function signatures
- `Any` in domain code signatures — use specific types or `object` + type narrowing
- Returning raw `sqlite3.Row` objects from domain functions — convert to `dict` first
  (use `conn.row_factory = sqlite3.Row` and index by name, then convert before returning)
