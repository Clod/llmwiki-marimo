<!-- Generated: 2026-05-25 | Files scanned: ~45 | Token estimate: ~700 -->

# Architecture

**llmwiki-marimo** — an LLM-powered knowledge base. Raw sources (PDF/DOCX) are ingested
into a curated wiki (summaries + concept pages) backed by SQLite. A chat agent
answers questions wiki-first, falling back to raw source chunks. A lint→repair
cycle keeps the wiki internally consistent. See `docs/programmer_manual.md` for
the authoritative §-by-§ spec.

## Layers

```
marimo/        UI layer (marimo notebooks) — ingest_app, read_app
   │
api_new/domain/    Pure domain logic (no UI, no network except LLM)
   ├── ingestion/  sources → wiki pages + DB
   ├── lint/       detect wiki inconsistencies → LintReport
   ├── repair/     fix lint issues → RepairReport
   ├── chat/       PydanticAI agent + RAG tools
   └── tools/      DB, references graph, FTS search, fs, deletion, git
   │
shared/            sqlite_schema.sql (single source of DB truth)
workspace/         per-user data: sources/, wiki/, .llmwiki/index.db
```

## Core data flow

```
sources/*.pdf
   └─ ingest_file ─ extract ─ chunk ─ LLM(extract_structured) ─┐
                                                               ▼
                              wiki/summaries/*.md + wiki/concepts/*.md
                              + documents / document_chunks / document_references
                                                               │
                              lint_wiki ──► LintReport ──► repair_wiki
                                                               │
                                            internally consistent wiki
                                                               │
   chat: read_wiki_page → search_wiki_fts → search_source_chunks  (wiki-first RAG)
```

## Key principle — wiki-first

The agent re-reads **curated wiki pages** (cheap, high-signal) before dropping to
raw `document_chunks`. Reconciliation (lint→repair) is the mechanism that keeps
those curated pages trustworthy after every ingest and chat→wiki save.

## Status

All ten §6 workflows are ✅ (see programmer_manual §6 table). Web search (RAG
Phase 4) is a deliberate out-of-scope future enhancement (§12).
