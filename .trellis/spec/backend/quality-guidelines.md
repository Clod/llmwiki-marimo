# Quality Guidelines

> Code quality standards for backend/domain development.

---

## Overview

These standards apply to `base/` and `mcp/`.

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

Each file in `base/domain/ingestion/` has one job:
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

Annotate parameters and return types on every public function and method. Prefer
modern syntax (`str | None`, `list[dict]`) over `Optional`/`List`. Keep `Any` out
of public signatures; reserve it for genuinely dynamic boundaries (e.g. parsed
JSON) and narrow it as soon as possible.

### Localization — thread the wiki language

Each wiki carries a content language (`wiki_config.toml` `[wiki].language`; en/es,
extensible). Any function that **generates wiki content or emits structural
headers/labels** must:

- take `language: str = "en"` as the **last, keyword-defaulted** parameter
  (append-only — never reorder existing params);
- resolve the language once at the entry point (config/app) via
  `domain.wiki_settings.load_wiki_language`, then thread it down — never read the
  TOML ad hoc;
- fill headers/labels from `domain.i18n.get_locale(language)`; append the content
  directive with `with_content_directive(...)` (generated prose) or
  `apply_chat_directive(...)` (chat); forward `language` to every downstream
  generator / index / repair / save call;
- keep the **English path byte-identical** — `get_locale("en")` returns the prior
  English literals and the directives are empty, so `language="en"` reproduces the
  old output (the golden corpus guards this).

Intentionally English in v1 (do not "fix"): the marimo app UI, the `log.md` ingest
log, and the lint/repair *diagnostic* notes (contradiction / data-gap / gap-filled).
See `docs/design_multilingual_content.md`.

---

## Testing Requirements

| Layer | Coverage target | Tool |
|-------|----------------|------|
| Domain logic | 80%+ | pytest + pytest-cov |
| E2E (marimo) | Critical paths | Playwright |
| Integration | DB lifecycle | pytest |

Run unit and regression tests before committing domain changes:

```bash
uv run pytest tests/unit/ tests/regression/ -v
```

E2E tests are slower — run explicitly:

```bash
HEADLESS=1 uv run pytest tests/e2e/ -v -s
```

### Running E2E locally (live LLM)

Unit and regression tests use the deterministic `FakeLLMClient` (no key, no
network). The E2E tests drive the real marimo apps against a **live** LLM, so
they need an API key, outbound network, and a Playwright browser. They cannot
run in a sandboxed/offline environment — pull the branch to a machine with a key.

1. **Configure the LLM** in `.env` at the repo root (`cp .env.example .env`):

   ```bash
   LLM_API_KEY=sk-or-...                       # your OpenRouter key
   LLM_BASE_URL=https://openrouter.ai/api/v1
   LLM_MODEL=anthropic/claude-sonnet-4.5       # ingest + chat model
   ```

   `WIKI_LLM_*` fall back to these `LLM_*` values, so the three above cover both
   apps. `.env` is gitignored — never commit it.

2. **Install the browser** (first run only):

   ```bash
   uv sync
   uv run playwright install chromium
   ```

3. **Run ingest before read** — the read app E2E skips unless a prior ingest has
   populated `tests/fixtures/workspace_e2e/`:

   ```bash
   HEADLESS=1 uv run pytest tests/e2e/test_ingest_app_v2.py -v -s
   HEADLESS=1 uv run pytest tests/e2e/test_read_app.py -v -s
   ```

   Or run the whole suite (ingest collected first): `HEADLESS=1 uv run pytest tests/e2e/ -v -s`.

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
- [ ] Content/header-generating functions thread `language` (en byte-identical) — see Localization
