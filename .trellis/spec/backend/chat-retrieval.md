# Chat Retrieval & Grounding Contracts

> Cross-layer contracts for the chat path (retrieval → plan → answer → citation).
> Captured after the vocab/pre-retrieval UAT surfaced three defects that each
> came from violating one of these. Executable: names files, functions, fields.

---

## 1. FTS5 queries must be sanitized before `MATCH`

**Contract.** `domain.tools.search.search_chunks(db_path, query, ...)` passes
`query` **verbatim** to `chunks_fts MATCH ?`. FTS5 reads `,` `?` `¿` `"` and the
bare keywords `AND`/`OR`/`NOT`/`NEAR` as **query syntax**, so a natural-language
question raises `sqlite3.OperationalError` — which `search_chunks` **swallows**
(`except Exception: return []`). The failure is therefore **silent**: the caller
sees "no hits", not an error.

**Any caller that passes user/NL text MUST first turn it into a valid MATCH
expression.** Reference implementation:

```python
_fts_query(text: str, language: str | None = None) -> str
retrieve_wiki(db_path, query, *, limit=6, language: str | None = None) -> list[str]
retrieve_source_chunks(db_path, query, *, limit=4, language: str | None = None) -> list[str]
```

Tokenize `\w+`, drop `language`'s stop words and ≤2-char tokens, `OR`-join each
token quoted (`"plazo" OR "fijo"`). The two pre-retrieval wrappers are its only
callers; the model's own `search_chunks` tool is exempt because the model already
emits keyword queries.

**Stop words are per language, and a new wiki language MUST add an entry.**
`_STOPWORDS: dict[str, frozenset[str]]` is keyed by ISO code; `_stopwords(lang)`
falls back to `"en"`, matching `wiki_settings.load_wiki_language`. The sets stay
**separate, never merged**: a function word in one language is a content word in
another — Spanish `son` is English "son", present in six chunks of the fairy-tale
corpus, so one shared list would drop the key word of *"Who is the king's son?"*.

| Case | Input | Forwarded to MATCH |
|------|-------|--------------------|
| Good | `_fts_query("¿le gano a la inflación?", "es")` | `"gano" OR "inflación"` → hits |
| Good | `_fts_query("What is the capital of France?", "en")` | `"capital" OR "France"` → 0 wiki hits |
| Bad  | raw `"¿le gano a la inflación?"` | `OperationalError` → silently `[]` |
| Bad  | `_fts_query("What is the capital of France?", "es")` | `"What" OR "the" OR …` → matches nearly every chunk |
| Edge | `_fts_query("¿? , .")` | `""` → `search_chunks` short-circuits to `[]` |
| Edge | `_fts_query(q, "it")` — unknown language | English set applied (documented fallback) |

**Two regression bugs this prevents.** (1) Hybrid pre-retrieval was dead for
every real Spanish question — the gate refused covered topics because retrieval
always returned empty. (2) With a Spanish-only stop-word list, an English wiki
matched `the` in nearly every chunk, so `wiki_hits` was **never empty**; because
`doc_hits` is computed only when `wiki_hits` **is** empty (§3), the Tier-2
raw-source branch was unreachable. Measured on the shipped demo: *"What is the
capital of France?"* went 6 injected chunks → 0, and *"Tell me about
Cinderwench"* — an ingest-generated alias no curated page mentions — went
unreachable → Tier 2 with 4 fragments.

Tests: `tests/unit/test_chat_retrieve.py` —
`test_english_function_words_do_not_reach_fts`,
`test_stopword_sets_are_language_specific_not_merged`,
`test_unknown_language_falls_back_to_english`.

---

## 1b. Injected blocks are labelled by page, with front-matter stripped

**Contract.** `domain.chat.preretrieval._format_hits(rows) -> list[str]` turns
`search_chunks` rows into the text handed to the model. Each block is:

```text
[<path><filename>]
<content, front-matter removed>
```

Two rules, and they are **coupled — do not apply one without the other**:

1. **Label = `row["path"] + row["filename"]`.** `path` alone is
   `/wiki/concepts/` for *every* concept page, so labelling by it gives the model
   several blocks it cannot tell apart — in the one mode whose prompt asks it to
   cite. `filename` is present in the row.
2. **Strip front-matter** (`datasets.frontmatter.split_frontmatter`). It is
   metadata, not text to answer from — the same reason
   `retrieve_collection_pages` already strips it. A chunk that is *only*
   front-matter keeps it, so no block is ever emitted empty.

**Why coupled.** Nothing in code guarantees a citation on this path (§2 below:
`ensure_citation` reads tool returns, and the pre-retrieval agent holds no wiki
tools). In practice the model cited from the front-matter's `sources:` line, so
stripping it *before* the label identified the page would remove the attribution
and put nothing back.

| Case | Row | Emitted |
|------|-----|---------|
| Good | `path=/wiki/concepts/`, `filename=glass-slipper.md`, content with front-matter | `[/wiki/concepts/glass-slipper.md]` + body only |
| Base | source chunk, `path=/sources/`, no front-matter | passes through unchanged |
| Bad  | label from `path` alone | six blocks all reading `[/wiki/concepts/]` |
| Edge | chunk that is *only* front-matter | kept verbatim rather than emptied |
| Edge | `content` blank/whitespace | row dropped |

**Note on granularity.** Blocks are *chunks*, not whole pages — Tier 1 injects
the 6 best-ranked, Tier 2 the 4 best. In both shipped demos every wiki page fits
in one chunk (longest ≈ 400 of the 512-token budget), so chunk == page there **by
margin, not by design**. A longer page arrives in pieces of which only the first
carries the heading, which is the case the label rule exists for.

Tests: `tests/unit/test_chat_retrieve.py` —
`test_injected_block_is_labelled_by_page_not_directory`,
`test_injected_block_drops_front_matter`,
`test_chunk_that_is_only_front_matter_keeps_it`,
`test_block_without_front_matter_is_untouched`.

---

## 2. The citation format is `Referencia:` / `Fuente:` lines, not parentheses

**Contract.** The canonical citation an answer carries is a **trailing line**:
- `Referencia: <wiki page or source file>` — from a curated page or a document
- `Fuente: <external origin>` — for a datum's external origin (e.g. `ambito.com`)

This is specified in every wiki's `wiki_config.toml` system prompt and emitted
deterministically by `domain.chat.postprocess.ensure_citation`. An inline
parenthesized `(wiki/….md)` may *also* appear, but is **not** the guaranteed
form.

**Any code that detects, counts, or extracts citations MUST recognize the
line form** (and source-doc extensions `.docx`/`.pdf`, not only `.md`).
Reference: `domain.eval.graders._CITATION_LINE` / `_line_refs`, and
`domain.chat.trace._CITATION_MARKERS`.

| Case | Answer ends with | `has_citation` / `cited` |
|------|------------------|--------------------------|
| Good | `Referencia: wiki/concepts/caucion-bursatil.md` | True |
| Good | `Fuente: ambito.com` | True |
| Good | `... (wiki/summaries/x.md).` | True |
| Bad  | no reference line, no `(…​.md)` | False |

**Regression bugs this prevents (bit twice):** `trace._looks_cited` matched only
`.md` → a curated answer citing a `.docx` read as uncited/ungrounded;
`graders.has_citation` matched only parenthesized refs → the `uat_finanzas`
concept-citation check false-failed a correctly cited answer. Tests:
`test_chat_trace.py`, `test_eval_graders.py`.

---

## 3. Pre-retrieval plan order — both tiers gated on the roster

**Contract.** `domain.chat.preretrieval.plan_retrieval(question, *, off_limits,
wiki_hits, doc_hits, has_data, in_roster, collection_hits=[])` decides in this
order:

1. `is_off_limits` → **refuse** (blacklist wins over any hit).
2. `wiki_hits and in_roster` → **Tier-1 curated** (inject, no verify).
3. `collection_hits` → **Tier-1 curated** (inject the collection pages, no verify).
4. `has_data` → **tools only** (no injected context; `query_dataset` /
   `estimar_alternativas`).
5. `doc_hits and in_roster` → **Tier-2 raw doc** (inject, verify vs source + warn).
6. else → **refuse** without invoking the model.

**Both** tiers are gated on `in_roster` (the coverage *padrón*), because lexical
FTS can match a curated or raw chunk on a shared word for an uncovered topic — so
the padrón, not the search hit, is the coverage authority. `has_data` is checked
**before** Tier-2 so a data question (e.g. "¿a cuánto el billete verde?") reaches
`query_dataset` instead of being answered from raw prose.

**The collection branch is deliberately outside the padrón**, because the padrón
cannot answer for it: a question about the collection as a whole ("what is in
this wiki?", "compare all of them") names no item, so a roster of item names is
the wrong instrument — widening it with summary and document titles was measured
and leaves every such question uncovered. It sits **after** Tier 1 so a question
that is both collection-shaped and names covered subjects gets those pages rather
than the overview, and **before** `has_data` so "what data do you have?" reaches
the overview rather than the tools. It cannot fire without something to inject:
`retrieve_collection_pages` returns `[]` when the wiki has neither
`wiki/overview.md` nor `wiki/index.md`, and the question then refuses as before.

**Caller (`pre_retrieval_answer`) computes:**
`has_data = mentions_known_data(q, vocab, aliases) or advisory_intent(q)` — the
second disjunct routes a generic advisory question (an amount + horizon, no named
instrument) to the tools instead of refusing. `in_roster = mentions_known_data(q,
coverage, aliases)` where `coverage = dataset vocab ∪ concept page names`, and
`collection_hits = retrieve_collection_pages(workspace) if collection_intent(q)
else []` — read off disk, since `overview.md` and `index.md` have no `documents`
row and are invisible to FTS.

Tests: `test_chat_retrieval_plan.py`, `test_chat_pre_retrieval_answer.py`,
`test_chat_scope.py`.
