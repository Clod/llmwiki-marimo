<!-- Generated: 2026-06-01 | Files scanned: 26 | Token estimate: ~1000 -->

# Backend (base/domain)

Pure domain layer. No web framework — entry points are called directly by the
marimo apps. Tools derive `workspace = Path(db_path).parent.parent`.

`base/config.py` (pydantic-settings) holds no hardcoded provider/model — LLM
values come from `.env`. `require_llm_config(base_url, api_key, model, *, purpose)`
fails fast with a "set LLM_* in .env" message; the apps call it before building a
client. `wiki_registry.py` (sibling of `domain/`-subdirs) backs the wiki picker:
`discover_wikis`, `merge_options`, `load/save/push_recent`, `clean_path_input`,
`resolve_wiki_home`, `short_label` (pure, unit-tested).

## ingestion/ — sources → wiki

```
ingest_file(db_path, workspace, file_path, client, model, ...)   pipeline.py:89
  detector.detect_file_type → extractor / pdf_extract (opendataloader-pdf)
  → chunker.chunk_pages → wiki_generator.extract_structured (LLM)
  → build_summary_page / build_concept_page → wiki_fs.create_page
  → references.update_references → index_manager.update_index → git_ops.auto_commit
scan_and_ingest(...)        pipeline.py:463   bulk ingest a sources/ dir
regenerate_wiki_pages(...)  pipeline.py:508   rebuild pages from stored sources
batch_ingest(...)           batch.py:44       many files, global updates once at end
```
Key: `wiki_generator.py` holds all LLM prompts (`structure_chat_content`,
`extract_structured`, `build_*_page`, `update_overview`, `make_wiki_slug`).

## lint/ — detect inconsistencies → list[LintIssue]

```
lint_wiki(db_path, workspace, client=None, model="")            runner.py:18
  orphan_check · staleness_check · missing_xref_check ·
  missing_concept_check · gap_filled_check          (deterministic, always run)
  contradiction_check · data_gap_check              (LLM-gated, need client)
LintIssue(check, severity, page, description, suggestion,
          related_page="", topic="")               report.py
markers.py  DATA_GAP_NOTE, DATA_GAP_BLOCK_RE, contradiction_marker, fts_safe
            (shared by lint.checks + repair.actions to avoid import cycle)
```

## repair/ — fix issues → RepairReport

```
repair_wiki(lint_report, db_path, workspace, llm_client=None, model="")  runner.py
  _DISPATCH: orphan·stale·missing_xref·missing_concept·contradiction·
             data_gap·gap_filled
  _NEEDS_LLM = {stale, missing_concept}   (called with client; rest 3-arg)
actions.py  one repair_* fn per check. All but stale/missing_concept are
            deterministic. _relative_link, _parse_page_path helpers.
```

## chat/ — PydanticAI agent (RAG)

```
create_agent(base_url, api_key, model, system_prompt=_DEFAULT)  agent.py
  deps_type=str — db_path is passed as deps at run_stream time, not to the factory
  tools = [read_wiki_page, search_wiki_fts, search_source_chunks]  (read-only; no write tool)
config.py   _DEFAULT_SYSTEM_PROMPT + load_config(wiki_path) ← wiki_config.toml
wiki_tools.py  read_wiki_page · search_wiki_fts · save_to_wiki (form-driven, not an agent tool)
               + _lint_and_repair_after_save (post-save reconciliation hook)
tools.py    search_source_chunks (async, raw source FTS fallback)
```

## tools/ — infrastructure

```
db.py          open_db / get_connection (WAL, FK on; applies sqlite_schema.sql,
               idempotent — no migration layer)
wiki_fs.py     create_page · read_page · append_to_page · delete_page (+chunking)
references.py  update_references (rebuild cites/links_to edges) · get_backlinks ·
               get_forward_refs · find_orphan_pages · find_stale_pages
search.py      search_chunks(db, query, limit, scope=all|wiki|sources)  FTS5
deletion.py    delete_source (FK cascade; marks dependent pages stale)
git_ops.py     init_wiki_repo · auto_commit · autocommit_enabled
               (git is OPTIONAL — a missing/failing git is warned once and skipped,
                never fails an ingest; WIKI_AUTOCOMMIT=0 disables it entirely)
```
