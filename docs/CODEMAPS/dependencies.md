<!-- Generated: 2026-06-09 | Files scanned: 2 (pyproject.toml, config.py) | Token estimate: ~470 -->

# Dependencies

## Runtime (pyproject.toml)

```
marimo>=0.23.4         Reactive notebook UI (ingest_app, read_app)
openai>=1.0.0          LLM client — any OpenAI-compatible endpoint
                       (configured via WIKI_LLM_* / LLM_* settings)
pydantic-ai>=1.97.0    Chat agent framework (create_agent, tool calling, streaming)
python-dotenv          .env loading (used alongside pydantic-settings)
pydantic-settings      Config loading (config.settings)
anywidget>=0.11.0      Custom JS widgets (DeleteConfirmWidget)
traitlets>=5.15.0      anywidget runtime dependency
aiosqlite              Async SQLite access (chat async tools)
opendataloader-pdf     PDF text extraction (ingestion/pdf_extract.py)
```

Standard library: `sqlite3` (primary sync DB access), `subprocess` (git, pdf
loader), `re`, `hashlib`, `pathlib`.

## External services

```
LLM API        OpenAI-compatible (base_url + api_key from settings).
               Used by: ingestion (extract_structured, build_*_page),
               lint (contradiction_check, data_gap_check),
               repair (stale, missing_concept), chat (agent + structure_chat_content).
Web search     NOT integrated — RAG Phase 4, deliberately not built (see ROADMAP.md).
               If added: async tool in chat/tools.py; candidates Tavily/Brave/DDG.
```

## Internal infra (not third-party)

```
SQLite + FTS5  All persistence + full-text search (no external DB/vector store).
git            OPTIONAL. Per-workspace auto-commit of wiki changes
               (tools/git_ops.py). A missing/failing git is warned once and
               skipped — never fails an ingest; WIKI_AUTOCOMMIT=0 disables it.
filesystem     workspace/ holds sources/, wiki/, .llmwiki/index.db.
```

## Config keys (config.settings)

No hardcoded provider/model — all LLM values from `.env`; defaults are blank.
`require_llm_config()` fails fast when an LLM is unconfigured.

```
LLM_BASE_URL / LLM_API_KEY / LLM_MODEL        chat + default for ingestion
WIKI_LLM_BASE_URL / _API_KEY / _MODEL         ingestion; fall back to LLM_* if blank
WIKI_PATH                                     default wiki on launch (picker switches it)
WIKI_HOME                                     folder the picker scans (default: parent of WIKI_PATH)
```
PDF extraction is opendataloader-pdf only (text PDFs; no OCR/`PDF_BACKEND` today).
Per-workspace overrides in `workspace/wiki_config.toml` (system_prompt,
suggested_prompts).
