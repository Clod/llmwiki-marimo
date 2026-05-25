<!-- Generated: 2026-05-25 | Files scanned: 1 (pyproject.toml) | Token estimate: ~450 -->

# Dependencies

## Runtime (pyproject.toml)

```
marimo>=0.23.4         Reactive notebook UI (ingest_app, read_app)
openai>=1.0.0          LLM client — any OpenAI-compatible endpoint
                       (configured via WIKI_LLM_* / LLM_* settings)
pydantic-ai>=1.97.0    Chat agent framework (create_agent, tool calling, streaming)
pydantic-settings      Config loading (config.settings)
anywidget>=0.11.0      Custom JS widgets (DeleteConfirmWidget)
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
Web search     NOT integrated — RAG Phase 4, deferred future enhancement (§12).
               If added: async tool in chat/tools.py; candidates Tavily/Brave/DDG.
```

## Internal infra (not third-party)

```
SQLite + FTS5  All persistence + full-text search (no external DB/vector store).
git            Per-workspace auto-commit of wiki changes (tools/git_ops.py).
filesystem     workspace/ holds sources/, wiki/, .llmwiki/index.db.
```

## Config keys (config.settings)

`WIKI_LLM_BASE_URL`/`API_KEY`/`MODEL` (fall back to `LLM_BASE_URL`/`API_KEY`/`MODEL`).
Per-workspace overrides in `workspace/wiki_config.toml` (system_prompt,
suggested_prompts).
