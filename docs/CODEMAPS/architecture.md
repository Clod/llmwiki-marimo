<!-- Written 2026-11-20 from the code of master. -->

# Architecture

**llmwiki** uses a model to compile a folder of documents into a wiki of markdown
pages, and answers questions from those pages. The documents are read once, at
ingestion. When the pages do not answer a question, the answer comes from the
fragments of the documents. Facts with a date come from tables (`datasets/`).
**Lint** finds inconsistencies in the pages after every ingestion, and **repair**
fixes the ones that need no judgement.

The step-by-step references are [`ingestion_nodes.md`](../ingestion_nodes.md)
(writing) and [`query_nodes.md`](../query_nodes.md) (reading). The narrative is
in [`ingestion_walkthrough.md`](../ingestion_walkthrough.md) and
[`query_walkthrough.md`](../query_walkthrough.md).

## Layers

```
web/              FastAPI + HTMX + Jinja, calls base/services/ only
base/services/    one module per use case: ingest, chat, wiki, vocabulary,
                  verification, settings, model_test
base/domain/      the logic: no web framework, no HTTP except the model calls
database/         sqlite_schema.sql, applied to every index.db when it opens
<workspace>/      one wiki: sources/, datasets/, wiki/, wiki_config.toml, .llmwiki/
```

`base/domain/` never imports `web/`. A test of a service or a domain module needs
no running server.

## The packages of `base/domain/`

| Package | What it does |
|---|---|
| `ingestion/` | a document becomes fragments, wiki pages and generated aliases, with images, OCR and tables |
| `chat/` | the three chat modes, the question checks, the answer checks, the prompts |
| `chat/workflow/` | the input, model and output workflows of a turn, as `pydantic-graph` graphs |
| `datasets/` | reads the markdown tables of `datasets/` at question time |
| `extensions/` | domain extensions activated by the datasets of a wiki (`finance_argentina/`) |
| `verify/` | the sentence verifier (NLI, optional) and the figure check |
| `lint/` | finds inconsistencies in the pages and never writes |
| `repair/` | fixes what lint found, when the fix needs no judgement |
| `tools/` | SQLite access, the reference graph, search, wiki files, deletion, git, reindex |
| `rollback/` | returns a wiki to an earlier commit, pages and index together |
| `text/` | stemming and stop words per language |
| `eval/` | builds evaluation packets for an external judge model |
| `modeltest/` | the model test of the Settings screen |

The modules at the top of `domain/`:

- `tracing.py`: the trace, written with `WIKI_TRACE=1`.
- `i18n.py`: the texts of each language.
- `prompts.py`: the prompt files.
- `wiki_registry.py`: the wiki picker.
- `wiki_settings.py`: the language of a wiki.

## Writing

```
sources/<file>
  └─ ingest_file (ingestion/pipeline.py)
       I_VALIDATE → I_IS_CHANGED → I_DOCUMENT_ROW → I_EXTRACT
       → fragments (W_CHUNK → W_STEM → W_INSERT_CHUNKS): the source is ready
       → I_CONCEPTS (model) → I_CONCEPT_PAGES (model) → I_ALIASES → I_SUMMARY_PAGE
       → I_OVERVIEW (model) → I_LOG → I_COMMIT → I_LINT
  an upload, a scan or a regeneration then runs I_FINAL_REVIEW and I_CROSSLINKS
```

A failure between `I_EXTRACT` and `I_LINT` ends in `E_ROLLED_BACK`. The pages are
undone, and the source is marked `failed`.

## Reading

| Mode | Who chooses the context | Checks |
|---|---|---|
| agent | the model, with tools | output workflow of the agent (`T_*`), figure check |
| pre-retrieval | the code: input workflow (`Q_*`, `C_*`), then the model, then the output workflow | blacklist, coverage roster, evidence of the context, figure check |
| streaming | the model, with tools, and the answer appears as the model writes it | none |

## Principles

- **The sources are the truth.** `wiki/` and `index.db` are derived.
  `reindex_from_disk` rebuilds the index with no model call.
- **The code checks, the model writes.** Code decides the coverage of a question
  before the model call. Code adds the citations and checks the figures after it.
- **Mark, do not block.** An unsupported sentence or figure is marked. A refusal
  happens in a few cases, and each refusal states its cause.
- **A workspace is data only.** No workspace carries code. The extensions live
  in the engine.

Web search is not built (see the [ROADMAP](../../ROADMAP.md)).
