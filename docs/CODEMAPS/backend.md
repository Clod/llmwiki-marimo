<!-- Written 2026-11-20 from the code of master. -->

# Backend (`base/`)

`base/config.py` (pydantic-settings) reads `.env`. It holds no provider and no
model: `require_llm_config(base_url, api_key, model, purpose=...)` fails with a
message that names the missing setting. Symbols are cited without line numbers,
because a line number changes when the code above it changes.

## services/ — the use cases the web calls

| Module | Main functions |
|---|---|
| `ingest.py` | `ingest_uploads`, `scan_sources`, `regenerate_summaries`, `lint_and_repair`, `describe_images`, `delete_source`, `delete_stale_pages`, `remove_missing_sources` |
| `chat.py` | `build_agents`, `chat_turn_with_context`, `chat_turn_stream_with_context`, `save_answer`, `save_conversation` |
| `wiki.py` | `open_wiki`, `read_page`, `save_page`, `delete_page`, `reindex`, `history`, `revert`, `graph` |
| `vocabulary.py` | `roster`, `read_vocabulary`, `add_alias`, `reject_alias`, `add_blacklist_term`, `add_intent_cue` |
| `verification.py` | `verify_pages`, `verify_all`, `page_verification` |
| `settings.py` | `read_settings`, `save_settings` |
| `model_test.py` | `run` |

## domain/ingestion/ — documents → wiki

```
pipeline.py      ingest_file (the nodes of ingestion_nodes.md), scan_and_ingest,
                 regenerate_wiki_pages, _rollback_wiki_pages
batch.py         batch_ingest: I_OVERVIEW, I_LINT, I_LOG, I_COMMIT once at the end
workflow.py      the closing passes of a run: I_DATASET_ALIASES, I_FINAL_REVIEW, I_CROSSLINKS
detector.py      needs_ingestion: mtime, then hash; a failed file is tried again
formats.py       the accepted extensions, defined once
extractor.py     extract: PDF (pdf_extract.py), office → PDF, text (text_extract.py), image
ocr.py           OCR_ENGINE: none, tesseract (ocrmypdf) or vision
images.py        kept images, JPEG compaction; describe.py, describe_pass.py: vision descriptions
tables.py        a table cut by a page break keeps its header
chunker.py       chunk_pages: about 512 tokens, paragraph boundaries, 128 tokens of overlap
wiki_generator.py  the ingestion prompts: extract_structured, build_concept_page,
                 build_summary_page, update_overview, make_wiki_slug
alias_generation.py  update_generated_aliases, regenerate_dataset_aliases
index_manager.py update_index (wiki/index.md)
```

## domain/chat/ — the chat

```
agent.py         create_agent: wiki tools, dataset tool, extension tools
workflow/        input_checks.py, model_calls.py, output_checks.py (graphs);
                 catalog.py (the steps), node_texts.py, diagrams.py, flowchart.py
preretrieval.py  pre_retrieval_answer: plans the context of a turn, Tier 1 and Tier 2
scope.py, vocabulary.py, intent.py   the coverage roster, the aliases, the intent lists
condense.py      rewrites a follow-up question with the model
relevance.py     the evidence-quote and judge checks of the injected pages
postprocess.py   the advisory table, the citation, the injected-page note
figure_check.py  the figure check of the prose (C_FIGURES)
refusal.py       the refusals, each with its cause and suggested pages
history.py       the history a turn sends: refused turns marked, tables dropped
config.py        load_config: wiki_config.toml; config_writer.py, settings_writer.py write it
```

The tools of the model and their nodes (`K_*`) are listed in
[`query_nodes.md`](../query_nodes.md).

## domain/lint/ and domain/repair/

```
lint/runner.py   lint_wiki: 16 checks; contradiction and data_gap run only with a model
  checks.py      orphan, unpaged_source, staleness, missing_xref, missing_concept,
                 contradiction, data_gap, gap_filled, vocabulary, thin_page,
                 unverified_pages, unsupported_sentences
  image_checks.py  missing_image, orphan_image, image_without_description,
                 stale_image_description
lint/structure.py  not part of lint_wiki: the defects of form the code produces in a
                 generated wiki (front matter, links, section order, index entries);
                 scripts/check_wiki.py runs it
repair/runner.py repair_wiki: 9 repairs (orphan, stale, missing_xref, missing_concept,
                 contradiction, data_gap, gap_filled, missing_image, orphan_image)
                 _NEEDS_LLM = {stale, missing_concept}: skipped with a message when no
                 model is supplied
```

## domain/tools/ — infrastructure

```
db.py          open_db, get_connection: WAL, foreign keys on; applies the schema;
               _verify_schema rebuilds the index of a wiki built before stemming
wiki_fs.py     create_page, read_page, append_to_page, delete_page, strip_links_to
references.py  update_references: the cites and links_to rows of a page
search.py      search over chunks_fts, scope all, wiki or sources
deletion.py    delete_source: deletes the pages only that source supported,
               marks stale the pages that also cite another source
reindex.py     reindex_from_disk: a fresh index from the files, no model call
git_ops.py     auto_commit; optional, WIKI_AUTOCOMMIT=0 turns it off
```

## domain/verify/ and domain/rollback/

```
verify/        figures.py: classify the figures of a text (exact, rounded, unsupported)
               nli.py, verifier.py, sentences.py: the sentence verifier (optional group)
rollback/      revert.py: revert_wiki, forward commit; snapshots.py: index copies per commit
```
