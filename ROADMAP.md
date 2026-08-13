# Roadmap

Where this project is going, and what is already known to be imperfect.

This is not a release schedule. It is a working list kept in the open, because a
roadmap that only lists features is a brochure. Items move, get dropped, or turn
out to be wrong; when that happens the entry says so rather than disappearing.
Shipped work is recorded in [`CHANGELOG.md`](CHANGELOG.md), not here.

Current version: **0.3.0**. See [`docs/`](docs/) for the two walkthroughs that
describe how the system actually behaves, measured rather than asserted.

---

## Next

**Audit the rest of the documentation against the walkthroughs.**
`docs/ingestion_walkthrough.md` and `docs/query_walkthrough.md` are the only
documents derived from *observing* the system — every figure in them is captured
from a real run. Everything else (`programmer_manual.md`,
`sqlite_data_dictionary.md`, both READMEs) was written from memory of the code
and has not been checked against that yardstick.

**Regenerate both shipped demos.** `examples/fairy-tales` and
`examples/finanzas-argentinas` were built by an older version of the pipeline.
They are correct, and they are not what today's code would produce. Deferred
deliberately: the walkthroughs quote their figures, so regenerating means
re-checking every number in two long documents, and nothing currently depends on
it.

**Run the full end-to-end lint sweep.** `E2E_FULL=1` against
`tests/e2e/test_ingest_app_v2.py` exercises the LLM lint checks in a browser and
has never been run. `E2E_DESTRUCTIVE=1` has.

**Promote the tabs read app.** `marimo/read_app_tabs.py` won over the
three-panel grid in `marimo/read_app.py`, but the old one is still shipped.
Promoting means a parity end-to-end test *first* — proving nothing was lost —
then replacing the README screenshots and their alt text in both languages.
Deleting `read_app.py` before that would be trading a known-good app for an
assumption.

**Wiki rollback.** Designed, unbuilt: git-tracked markdown plus a gitignored
database snapshot ring and a deterministic reindex floor, so a bad ingest can be
undone. Design lives in [PR #7](https://github.com/Clod/llmwiki/pull/7).

**Italian.** The engine is already multilingual per wiki (`[wiki] language` in
`wiki_config.toml`); adding a language is one `Locale` entry in
`base/domain/i18n.py`. Scheduled last on purpose — the user-facing docs get
translated once, after the open branches merge, rather than twice.

**Rebuild the index from disk, without the model.** Today the only way to
repopulate a lost or corrupt `index.db` is to re-run ingestion — which re-invokes
the LLM, so pages, document IDs and chunk boundaries all differ run to run, and
it *overwrites* the on-disk markdown, destroying manual edits. It cannot rebuild
a page at all once its source file is gone. That contradicts the principle the
rest of the project rests on: the durable layer is the markdown plus the sources,
and the database should be rebuildable from them **mechanically**.

`reindex_from_disk(workspace, db_path)` would be that complement:

1. Apply the schema to a fresh DB and re-create the `workspace` row.
2. Walk `sources/*` → one `source_kind='source'` row per file (recompute
   `content_hash` / `mtime_ns` / `file_size`), re-extract pages with the existing
   deterministic extractor, re-chunk, fill `document_pages` + `document_chunks`.
   Re-extraction is the only step that reads the original file, and it uses no
   LLM.
3. Walk `wiki/**/*.md` → one `source_kind='wiki'` row per page; read title and
   tags from front-matter and re-chunk the markdown (the FTS triggers repopulate
   `chunks_fts`). This step used to be the weak one — front-matter was written by
   the model, so values could be missing or drifted. `create_page` now writes it
   from the values it is given, which makes reading it back sound rather than
   hopeful.
4. Run `update_references` per wiki page to rebuild `document_references` from
   the on-disk citations and wikilinks — already idempotent.
5. Re-derive each summary's `source_document_id` by matching its slug back to the
   source whose `make_wiki_slug(filename)` equals it.

**Recovered exactly, every run:** all `documents` rows, `document_chunks` +
`chunks_fts`, the reference graph, and `index.md` / `overview.md` / `log.md`
(read back verbatim — they are just files). **Cannot come from disk:** internal
counters (`version`, `document_number`) reset and `created_at` becomes "now".
**Caveats:** `document_pages` repopulates only while the source files are still
there to re-extract; a wiki page whose source was deleted re-registers fine but
its `cites` edge stays dangling, exactly as today.

**Smaller items**, each a real gap rather than a nicety:

- **Deepen `data_gap`.** The check only reads concept *titles*, and its repair
  inserts a generic TODO into the most-related page. Deepening it to read page
  bodies, and having the repair name specific sub-questions, is the open work.
- **Give scan and regenerate the same automatic tail as ingest.** Ingestion now
  closes with a lint+repair pass scoped to the pages it touched. `scan_and_ingest`
  and `regenerate_wiki_pages` still do not reconcile afterwards.
- **Warn on duplicate upload.** A file already ingested and unchanged is silently
  skipped; the interface should say so.
- **Document `scan_and_ingest` for end users** — what it touches, and when
  `batch_ingest` is the better call.
- **OCR for scanned PDFs.** `pdf_extract.py` extracts text only, so an image-only
  PDF yields nothing. Any fix should stay provider-agnostic: a local engine
  (Tesseract via ocrmypdf, docTR, Surya, RapidOCR, Docling) fits the local-first
  ethos with no extra key, or page images could go to the vision-capable model
  already configured through `LLM_*`.

---

## Known limits and open questions

Things measured and found wanting, kept here rather than quietly carried.

**Citation is guaranteed on one path and merely requested on the other.**
`postprocess.ensure_citation` appends attribution the answer is missing by
reading which tools the run actually used. With pre-retrieval enabled the model
is given no wiki tools at all — the passages arrive in the prompt — so for the
curated and raw-source tiers it finds no tool returns and does nothing. What
makes those answers cite is the injected prompt asking them to, plus whatever
attribution the passages happen to carry inline. Dataset answers are unaffected,
because `query_dataset` *is* a tool and its returns are visible.

The project states the principle it is departing from, in
`guardrail.enforce_grounding`'s own docstring: a system prompt "can ASK the
model to answer only from the wiki, but it cannot GUARANTEE it." The same
reasoning applies to asking it to cite. Deciding this means choosing what a
guaranteed citation would name — the passages injected, or only those the answer
demonstrably used — which is the same open question as the fit check below.

**A number invented in the model's own prose is asked against, not blocked.**
The project's rule is that the model explains figures and never arrives at them:
`query_dataset` returns values read straight out of the data files, and the
advisory's whole comparison table is computed in Python and appended by
`postprocess.answer_with_table` whether or not the model reproduced it. What
none of that covers is the model writing a *different* number into the sentences
around the table. The system prompt asks it not to. Nothing checks.

Unlike the fit check below, this one is exactly checkable. Numbers are a closed
class and compare exactly, where a paraphrase does not, and the figures the tools
returned are already held as typed values rather than as text to be parsed back
out — `DatasetRow.valor`, plus the figures in the computed advisory block. The shape is the one `postprocess.py`
already uses: a pure function over the run's message log, reading `ToolReturnPart`
contents.

```python
authorised = {norm(v) for v in dataset_values(messages)} | numbers_in(advisory_table)
invented   = {n for n in numbers_in(answer_prose) if norm(n) not in authorised}
```

Two decisions block it, and neither is technical:

**Rounding.** The data says `1187.5`, the model writes "cerca de 1.200". That is
good prose, not an invention, and an exact match rejects it. Admitting a
tolerance (±x%) reopens the hole the check exists to close; refusing one forbids
rounding outright. The project's own stance narrows this more than it would
narrow elsewhere — under "the model does not arrive at figures", a computed
number *is* the violation — but the rounding case still has to be decided rather
than assumed away. Numbers appearing inside a page the answer legitimately read
also have to be admitted into the authorised set.

**What happens on a hit.** Reject and regenerate, strike the sentence, or
annotate. The existing precedent is to append rather than rewrite
(`answer_with_table` never edits the model's text), but only rejection is
actually a guarantee.

Stated as a limit in
[`docs/query_walkthrough.md`](docs/query_walkthrough.md#the-question-this-document-answers).

**The raw-source fallback is reachable but structurally rare.** With
pre-retrieval enabled, the last resort before refusing is to answer from a
fragment of the original document. It runs only when the question names
something the wiki covers *and* the search over the generated pages returns
nothing — and those two conditions work against each other. What the wiki
"covers" is mostly the titles of its own pages, so naming one all but
guarantees the search finds it. Measured across both shipped demos: of 43
concept-page titles, **0** fail to find their own page; of 17 ingest-generated
aliases, **3** do.

Those three are the whole reachable domain, and they share a shape: a name the
wiki *recognises* but never *wrote*. Ingestion found "Cinderwench" in the tale
and recorded it as another name for Cinderella; the generated page says
Cinderella throughout. So the search finds nothing, the fallback fires, and the
original text — the one place that word appears — is where it looks.

The consequence is narrower reach than the design reads as offering. A detail
question about a covered subject, whose answer is in the source and not in the
generated page, still goes to the curated tier, because the subject's name finds
its page. The model is handed pages that do not contain the answer, and the
branch that would have gone looking is never evaluated.

The gate asks *did I find anything?* rather than *does what I found answer
this?* — the same distinction that made the stop-word defect possible one level
down. Deliberate as a safety property (a question about an uncovered subject
must not be able to pull a tangential fragment as cover) but the effect on reach
looks like a consequence rather than a decision.

`chat/overlap.py` does not answer this question, although it runs on that same
path. It measures whether the answer draws on the fragment, not whether the
fragment answers the question — a faithful narration of an irrelevant passage
scores high. Judging fit requires one of the techniques in the entry below. No
change proposed yet; the trade needs measuring first.

**Verifying an answer against its source: the established techniques.** Two
distinct problems are involved, and this project currently addresses neither.
They are recorded together because the entry above and
[`docs/query_walkthrough.md`](docs/query_walkthrough.md#the-question-this-document-answers)
both refer to them.

*Problem 1 — which passage supports which sentence.* `overlap.is_supported`
takes one answer and one source. Where it runs, the code injected exactly one
fragment, so there is nothing to attribute. In the agentic path the model chose
what to read and several tool returns may exist, with no record of which one a
sentence came from. Joining them and comparing against the whole does not work:
coverage rises with the length of the right-hand side. The established approach
is to score each sentence against each passage independently and keep the
highest score. The stronger variant is to record attribution during generation —
require a citation per claim naming the passage — which is what the **ALCE**
benchmark (Gao et al., 2023) evaluates, using entailment to compute citation
precision and recall.

*Problem 2 — whether the passage answers the question.* Two families, differing
in where they act:

| What is verified | Technique | Acts |
|---|---|---|
| the answer follows from the passage | **textual entailment (NLI)**: the pair (premise = passage, hypothesis = sentence) is classified as entailed / neutral / contradicted. DeBERTa-v3-MNLI, AlignScore, MiniCheck, Vectara HHEM | after the answer |
| the passage is relevant to the question | **cross-encoder reranking**: the pair (question, passage) is scored by a model that reads both together. monoT5, BGE-reranker, Cohere Rerank | at retrieval |

The Snow White failure written up in the query walkthrough is the second kind,
not the first: the fragment is described accurately and the answer narrates it
faithfully; what fails is that the fragment is not about the ending. Reranking
at retrieval addresses it. A post-hoc verifier does not.

**A third route, cheaper than either, when the source declares its own
structure.** A neural reranker judges relevance by reading question and passage
together. A document that carries headings has already stated which part it is:
introduction, method, results; installation, troubleshooting; beginning, middle,
ending. Comparing *the section a fragment came from* against *the section the
question asks about* is a string comparison, not a model.

Half of it is built and unused. `chunker.py:63` computes `header_breadcrumb` for
every chunk, `pipeline.py:242` stores it on the `document_chunks` row,
`search_chunks` returns it (`tools/search.py:27`), and `wiki_tools.py:118`
renders it into what the model sees. Every retrieved fragment already carries
the section it came from; no code compares it with anything.

The missing half is the mapping from a question to a section — deciding that
"how does it end" is about the ending. Options, in the project's existing idiom:
a per-corpus declared list, like the alias lists in `wiki_config.toml`; or a
model call, which reintroduces the cost the route was chosen to avoid. The
mapping is also the part that fails silently on a corpus whose headings are
idiosyncratic.

Scope, stated honestly: it applies only where sources are structured, it checks
*which part* rather than *what is true*, and it does not subsume the entailment
check above — an answer can quote the right section and still misstate it.
Nothing is built.

Costlier families, for completeness: atomic-claim decomposition and per-claim
verification (**FactScore**, **SAFE**, Chain-of-Verification), and
model-as-judge with a rubric (**RAGAS** faithfulness and context
precision/recall, the **TruLens** RAG triad, **DeepEval**, **Arize Phoenix**).

**What adopting any of them costs here.** All are models producing a score
against a threshold, not a branch in Python. Two consequences specific to this
project:

- The document argues that the *before* position gives guarantees because no
  decision is left to a model. An NLI verifier or a reranker puts a model back
  into that chain. NLI models are small, run locally and are deterministic at
  temperature zero, so the claim survives with qualification — but the wording
  has to change.
- Embeddings were refused for retrieval. A cross-encoder is also a neural model,
  though a different one: it reads question and passage together instead of
  comparing vectors. Adopting it is an explicit exception to that decision, not
  a continuation of it.

**Where to put them first.** `base/domain/eval/` already holds a rubric and an
LLM judge, with `graders.py` as its deterministic pre-screen. RAGAS-style
metrics or an NLI verifier belong there as offline measurement. Only with that
measurement in hand is there a basis for moving one into the answering path.

**The coverage gate matches page titles literally.** With pre-retrieval enabled,
whether a wiki answers a question depends on whether one of its concept-page
titles appears word-for-word *inside that question* — the test runs in that
direction, not the reverse. On the shipped finance demo:

```
pages:     Caución Bursátil · Riesgo Inflacionario en Cauciones
           Riesgo de Crédito en Cauciones
question:  "¿las cauciones son riesgosas?"          →  not covered
```

Three pages about exactly that, and the question is turned away, because no whole
title fits inside the sentence — and the bare `caución` from the dataset misses
too, since matching is by whole word and `caución` is not one inside `cauciones`.
Page titles are chosen by a language model at a temperature above zero, and it
named statements rather than subjects. Regenerating a wiki therefore also edits
the list of subjects it will answer about.
Measured and written up in
[`docs/query_walkthrough.md`](docs/query_walkthrough.md#where-the-roster-shows-its-limits).

**The sharpest way to state it: one system holds two different notions of "the
same word".**

| | how it compares | `caución` ≟ `cauciones` |
|---|---|---|
| the search index (`chunks_fts`) | `porter unicode61` — stems | **yes** — identical rows, verified |
| the coverage gate (`scope._normalize`) | lowercase, strip accents, whole word | **no** |

This is solvable, and there are three routes out. They are not equivalent, and
the obvious one is the dangerous one.

**A — Stem the gate the way the index already stems.** The narrowest change:
`caución` and `cauciones` unify and the two halves of the system stop disagreeing
about what a word is. It does *not* fix multi-word titles — `Riesgo Inflacionario
en Cauciones` still fits inside no question anyone would type. Low risk, because
it loosens nothing semantically. One caveat worth knowing before adopting it:
`porter` is an *English* stemmer applied to Spanish. It happens to unify this
pair; adopting it means adopting its arbitrariness too.

**B — Match on the title's content words with a threshold**, instead of requiring
the whole phrase. `Riesgo Inflacionario en Cauciones` → `{riesgo, inflacionario,
cauciones}`, and a question supplying `{cauciones, riesgosas}` scores 1 of 3.
This fixes the case above **and reopens the CEDEARs leak** — the exact failure the
gate exists to prevent, where a question about one instrument matches a page about
another because both say *riesgo*. Recorded here because it is what everyone
proposes first, and the reason to refuse it should be written down rather than
rediscovered.

**C — Have ingestion declare what each page is about.** Today the roster is
*inferred* from whatever title the model chose in passing. But ingestion already
extracts, per concept, a name **and its aliases** — that machinery exists and
ships (see `alias_generation.py`). Extending it to record *"this page covers:
cauciones, credit risk, short-dated placements"* turns the roster into **declared
data** instead of a by-product of page naming. This is the one that addresses the
cause: the problem is not that the match is strict, it is that the list it
matches against was assembled by accident.

**Recommendation: C as the direction, A as an interim, B refused with the reason
kept.** None of the three has been built; this entry exists so the next person
does not have to re-derive the trade.

**"Regenerate and diff" is a weaker check than the ingestion walkthrough claims.**
That document says a disagreement between its prose and a regenerated appendix
"is a signal the pipeline changed". But pages are model-written at temperature
0.2–0.4, so a difference can equally be model variance — a point the same
document makes elsewhere. The claim is stronger than the mechanism supports, and
it bears on how much of the docs can be pinned by tests.

**The documentation link checker cannot see a link cut in two.** When a markdown
link is split across lines, both halves can resolve as separate links and the
check passes while the page renders wrong. It verifies that targets exist, not
that a link is one link. No such link exists in the repo today.

**A guard exists for a step that was never built.**
`scope.drop_false_synonyms` filters a model's proposed synonyms against pairs
declared not to be synonyms (`cedear` ≠ `acción`). Nothing calls it: it was
written for a query-time "synonym rescue" step — widen a question's terms when
the first search returns nothing — that was never implemented. Kept rather than
deleted because it is precisely what would keep such a step safe. The decision
to build it or delete it is open; the function documents both directions.

**There is no interface for the vocabulary lists.** The blacklist, the
hand-written aliases and the false-synonym pairs live in `wiki_config.toml` and
are edited in a text editor. This is deliberate — the pipeline never rewrites a
file a human wrote, and the automatic repair for a colliding alias refuses to
touch your config and says so — but it does mean the maintenance loop is manual.
It is documented in
[`docs/manual/workflows.md`](docs/manual/workflows.md#maintaining-the-vocabulary-lists).

---

## Not planned

Recorded so the absence reads as a decision rather than an oversight.

- **Embeddings and vector search — as a *retrieval* mechanism.** Retrieval is
  SQLite FTS5 throughout. The argument for the curated-wiki approach is that a
  page written once beats a fragment retrieved every time; adding a vector store
  would not change that and would add an index to keep in sync.

  **Using an embedding model at *ingest* time is a separate question, and it is
  open.** Today the alias lists exist precisely because there are no embeddings:
  keyword search cannot connect *the central bank* to a corpus that only says
  *the Fed*, so ingestion asks an LLM to write down the alternate names it finds,
  and a human adds the ones the documents never use. An embedding model could
  propose those pairings instead, or check the ones already proposed — and
  crucially, its output would still be a plain TOML file a person can read and
  edit, not an index to keep in sync. That is compatible with everything above.

  What is not obvious is whether it would be *better*. The current pass is
  auditable end to end: you can open `aliases.generated.toml`, disagree with a
  line, and delete it. Similarity scores are not auditable in that way, and the
  failure this list guards against — bridging two instruments that are not the
  same thing, `cedear` ≟ `acción` — is exactly the kind of near-synonym an
  embedding model is most likely to get wrong. Anyone taking this on should start
  by measuring the current pass's misses rather than assuming they exist.
- **Multi-user or hosted operation.** A workspace is a folder on one machine.
  See "Limitations & non-goals" in the [README](README.md#limitations--non-goals).
- **Web search, at query time or as an ingest loop.** The chat agent's cascade is
  wiki index → wiki full-text → raw source chunks. A fourth step that reaches the
  web is deliberately absent: the project's claim is about answering from a
  *curated, local corpus*, and those three steps exercise it fully. Web search is
  also the only workflow with a recurring external cost and a network dependency
  that complicates testing. The same reasoning covers the richer version — lint
  finds a gap, a tool searches the web, and on approval the result is ingested as
  a new source. Today you do that by hand: run the search, drop the finding into
  `sources/`. The corpus still compounds; only the automation is missing.
- **Review the extraction before it is written.** Ingestion is one shot: a
  document goes in and pages come out, with no chance to edit the model's
  extraction in between. Splitting it into `extract_only` and
  `commit_to_wiki(edited)` would give that chance. Not planned, because the
  correction path already exists on the other side — discuss the document in
  chat, then save a corrected page through the **Save to wiki** form. Post-hoc
  rather than mid-ingest, but the human still shapes the wiki.
- **Output formats beyond markdown** — slide decks, Obsidian Canvas files, an
  interactive graph rendering of `document_references`. Each is a plausible thing
  to build on top of the wiki, and none of them tests the idea the project exists
  to test.
- **Image handling.** Ingestion is text-only; images embedded in a document are
  skipped rather than described. Storing them under `sources/assets/` and passing
  them to a vision-capable model is the obvious extension, and is not on the path.
