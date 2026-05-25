# Logging Guidelines

> How logging is configured and used in this project.

---

## Overview

The project uses Python's stdlib `logging` module. There is no third-party logging library.
Two distinct configurations exist — one for `api_new/` domain code and one for marimo apps.

---

## Logger Hierarchy

All project loggers live under the `wiki` namespace:

```
wiki                  — top-level namespace, configured in setup cell
wiki.app              — ingest_app.py marimo app
wiki_app              — read_app.py marimo app (flat name, same intent)
wiki.domain.ingestion — domain module loggers
```

Individual modules get their logger with:

```python
logger = logging.getLogger(__name__)
# Results in: wiki.domain.ingestion.pipeline, wiki.domain.ingestion.extractor, etc.
```

---

## Configuration (Marimo Setup Cell)

The root logger is kept at `WARNING` to silence marimo internals. Only the `wiki`
hierarchy is elevated:

```python
logging.getLogger().setLevel(logging.WARNING)   # silence marimo / third-party
_wiki_log = logging.getLogger("wiki")
_wiki_log.setLevel(logging.DEBUG if debug_mode else logging.INFO)
if not _wiki_log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    _wiki_log.addHandler(_h)
_wiki_log.propagate = False                     # don't bubble to root
logger = logging.getLogger("wiki.app")
```

The `not _wiki_log.handlers` guard prevents duplicate handlers on marimo hot-reload.

---

## Debug Mode

Toggle verbose logging with `WIKI_DEBUG=1` in `.env` or the shell environment:

```python
debug_mode = bool(os.environ.get("WIKI_DEBUG"))
_wiki_log.setLevel(logging.DEBUG if debug_mode else logging.INFO)
```

`read_app.py` uses a simpler check:

```python
debug_enabled = os.environ.get("DEBUG") or os.environ.get("WIKI_DEBUG")
log_level = logging.DEBUG if debug_enabled else logging.WARNING
```

---

## Log Levels

| Level | When to use |
|-------|-------------|
| `logger.debug(...)` | Fine-grained tracing: "Workspace row exists", DB query results |
| `logger.info(...)` | Normal pipeline progress: "Extracted 42 pages", "Ingestion ready" |
| `logger.warning(...)` | Degraded but recoverable: LibreOffice not found, LLM fallback |
| `logger.error(...)` | Failures: import errors, DB errors, LLM API failures |

---

## What to Log

Always log these events at `INFO`:
- Config loaded and key settings (model, base_url, paths)
- Each pipeline step completion (extract, chunk, wiki generate)
- Operation results (`status`, `message`)

Log at `DEBUG` when `WIKI_DEBUG=1`:
- Existence checks ("workspace row exists: {id}")
- Button/widget values (debug panel)
- DB row counts

Log at `ERROR` with `exc_info=True`:
- Import failures in runner cells
- Unexpected exceptions that caused an operation to abort

---

## What NOT to Log

- API keys, access tokens, or any credential
- Full file contents or extracted text (can be very large)
- User PII
- Raw SQL query results with sensitive fields

---

## Progress Callbacks

Long-running domain functions accept an optional `progress_cb: Callable[[str], None]`
for user-visible progress messages. These are separate from structured log entries.

```python
def _cb(msg: str) -> None:
    logger.info(msg)       # structured log
    if progress_cb:
        progress_cb(msg)   # UI-facing message (shown in log panel or spinner)
```

The callback receives human-readable strings like `"✅ Extracted 12 pages"`.
The logger receives the same string but structured log metadata is added automatically.

---

## Common Mistakes

- **Using `print()` instead of `logger.*()`** — bypasses level control; always appears
- **Forgetting `_wiki_log.handlers` guard** — causes duplicate log output on marimo reload
- **Propagating to root** — marimo's root logger is noisy; always set `propagate = False`
- **Logging at DEBUG in production** — only enable with `WIKI_DEBUG=1`
