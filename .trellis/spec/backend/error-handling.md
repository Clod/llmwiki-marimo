# Error Handling

> How errors are handled in the ingestion pipeline and marimo apps.

---

## Overview

The project distinguishes between two contexts:

1. **Domain functions** (`base/`) — return typed result objects; raise only for
   truly unrecoverable situations
2. **Marimo cells** — catch domain exceptions, surface them as UI messages and log entries

---

## Result Types (Domain Layer)

Domain functions that can partially succeed or produce different outcomes return
a typed `@dataclass` result rather than raising.

```python
@dataclass
class IngestResult:
    file_path: Path
    status: Literal["ingested", "skipped", "failed"]
    message: str
    doc_id: str | None = None
```

Callers check `result.status` — they don't need to catch exceptions for expected
failure modes (file not found, unsupported type, already up to date).

```python
result = ingest_file(fp, db_path, workspace, llm_client, model, _cb)
if result.status == "failed":
    logger.error("Ingestion failed: %s", result.message)
```

---

## Custom Exceptions

Define custom exceptions for errors that callers need to catch by type:

```python
class LibreOfficeNotInstalledError(RuntimeError):
    def __init__(self, filename: str = ""):
        msg = (
            f"LibreOffice is required to process '{filename}'. "
            "Install it and restart: brew install --cask libreoffice"
        )
        super().__init__(msg)
```

Only raise when the caller truly cannot continue without handling the specific type.
Use `RuntimeError` as the base class for infrastructure/environment failures.

---

## Error Handling in Runner Cells

Runner cells in marimo notebooks follow this pattern:

```python
@app.cell
def ingest_runner(mo, ingest_trigger, ...):
    mo.stop(ingest_trigger() is None)
    _, _files = ingest_trigger()

    try:
        from domain.ingestion import ingest_file as _if
    except Exception as _e:
        logger.error("Import error: %s", _e, exc_info=True)
        set_log_lines([f"❌ Import error: {_e}"])
        mo.stop(True)

    _msgs = []
    def _cb(msg):
        _msgs.append(msg)
        logger.info("[ingest] %s", msg)

    with mo.status.spinner(title="Ingesting…"):
        for _f in _files:
            _result = _if(...)
            logger.info("Result: %s — %s", _result.status, _result.message)

    set_log_lines(_msgs)
```

Key rules:
- Wrap domain imports in `try/except` so import errors surface in the UI
- Use `mo.stop(True)` after setting error messages to halt the cell cleanly
- Always log the error with `exc_info=True` for import/unexpected failures
- Update `set_log_lines` before stopping so the user sees feedback

---

## DB Error Handling

Wrap SQLite operations in try/except when the table might not exist yet (e.g., on
first run or in display cells that read before the pipeline has run):

```python
try:
    rows = conn.execute("SELECT filename, status FROM documents ...").fetchall()
except sqlite3.OperationalError:
    rows = []   # table doesn't exist yet — show empty state gracefully
```

Always close the connection in a `finally` block when the operation might fail:

```python
conn = open_db(db_path)
try:
    # ... operations ...
    conn.commit()
except Exception:
    conn.rollback()
    raise
finally:
    conn.close()
```

---

## LLM Response Fallback Pattern

LLM calls that expect structured output (JSON) must fall back gracefully when the
model returns invalid JSON rather than propagating a parse error:

```python
def _parse_extraction(raw: str, filename: str) -> ExtractionResult:
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
        ...
        return ExtractionResult(document_summary=summary, concepts=concepts)
    except (json.JSONDecodeError, TypeError):
        logger.warning("JSON parse failed for %s, using fallback", filename)
        return ExtractionResult(document_summary=raw, concepts=[])
```

Rules:
- Always strip markdown fences before parsing (```` ```json ``` ````).
- On `JSONDecodeError`, log a warning (not an error) and continue with a degraded result.
- Never let an LLM parse failure abort the ingest — the source doc is already committed.
- `FakeLLMClient` must return valid JSON to test the happy path; tests for the fallback
  path pass `"not valid json at all"` as the response content.

---

## What to Raise vs. Return

| Situation | Action |
|-----------|--------|
| File not found, wrong extension, already ingested | Return `IngestResult(status="failed"/"skipped")` |
| LibreOffice missing | Return `IngestResult(status="failed")` with clear message |
| DB schema not found | `raise RuntimeError(...)` — unrecoverable |
| LLM API failure | Log + return `IngestResult(status="failed")` |
| LLM returns invalid JSON | Log warning + `ExtractionResult(concepts=[])` fallback |
| Import failure in cell | Log + `set_log_lines([f"❌ ..."])` + `mo.stop(True)` |

---

## Common Mistakes

- **Silently swallowing errors** — always at minimum `logger.warning` or `logger.error`
- **Raising inside `on_click` lambdas** — marimo silently drops these; set state instead
- **Not closing DB on exception** — leaves the file locked; always use try/finally
- **Showing raw exception messages to users** — wrap technical details in user-friendly strings
