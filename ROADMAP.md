# Roadmap

Where this project is going, and what is already known to be imperfect.

This is not a release schedule. It is a working list kept in the open, because a
roadmap that only lists features is a brochure. Items move, get dropped, or turn
out to be wrong; when that happens the entry says so rather than disappearing.
Shipped work is recorded in [`CHANGELOG.md`](CHANGELOG.md), not here.

Current version: **0.6.5**. See [`docs/`](docs/) for the two walkthroughs that
describe how the system actually behaves, measured rather than asserted.

---

## Next

**Smaller items**, each a real gap rather than a nicety:

- **Document `scan_and_ingest` for end users** — what it touches, and when
  `batch_ingest` is the better call.
- **OCR for scanned PDFs — built** (commit `01497f0`; see `CHANGELOG.md`).
  `OCR_ENGINE` selects `none`, `tesseract` or `vision`. The engine interface is in
  `base/domain/ingestion/ocr.py`; another engine (RapidOCR, docTR, Surya) is one
  class and one entry there. Those three are not built.

---

## Known limits and open questions

Things measured and found wanting, kept here rather than quietly carried.

**A page kept after a source is deleted still holds what that source contributed.**
Deleting `La Cenicienta.pdf` from the fairy-tale demo deletes the three pages that
cite only that source: the summary, `cenicienta.md` and `hada-madrina.md`. The page
`moraleja.md` cites `Caperucita Roja.pdf` too, so it is kept, marked stale, and stops
citing the deleted file. Its sentences about the two morals of *La Cenicienta* stay.
A concept page is written by accumulation (`build_concept_page` rewrites the
existing text), so no record says which sentence came from which source; the
page's generated aliases have the same problem. Code cannot remove that text; the
answer checks are what flag it. The only remedy is rebuilding the whole wiki from
the remaining sources (README, Limitations). The deletion itself
(`base/domain/tools/deletion.py`) is complete: it runs the same way from the
Ingest screen and for a file removed from `sources/` by hand (when the wiki is
opened, before a scan, before a rebuild of the index), and leaves no link to
anything deleted.

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

**A number invented in the model's own prose is marked, not blocked.**
Built. The project's rule is that the model explains figures and never arrives at
them: `query_dataset` returns values read straight out of the data files, and the
advisory's whole comparison table is computed in Python and appended by
`postprocess.answer_with_table`. The figure check
(`base/domain/verify/figures.py`, called by `services.chat.check_turn_figures` at
the end of the three chat modes) covers the sentences around the table. It is
deterministic, calls no model and does not need the NLI verifier.

The check reads every figure of the prose, the part of the answer the model wrote,
and classifies it:

- **exact**: the figure equals an authorised value. The authorised values are the
  numbers of the passages the model read in the turn, of the advisory table, of
  every message of the user and of the passages read in earlier turns of the
  conversation. The row count and the columns `as_of` and `fuente` of a dataset
  table authorise nothing.
- **rounded**: the figure equals an authorised value rounded to the precision the
  model wrote ("cerca de 1.200" for `1187.5`), and differs from it by no more than
  `[verify] number_tolerance` (default `0.05`, relative).
- **unsupported**: any other figure ("1.150" and "1.000" for `1187.5`).

The web interface marks each unsupported figure in red, with a tooltip that names
the nearest authorised value and where it comes from. When the prose has rounded
figures, a fixed note in the language of the wiki is appended to the answer. The
text of the model is never edited and the answer is never blocked. The check runs
in the three chat modes, with or without the NLI verifier.

Limits, as measured and as designed:

1. A number written in words ("tres", "un tercio") is not checked.
2. A date and a year alone are not checked.
3. An integer from 0 to 12 with no unit (`%`, `$` or a currency word next to it)
   is a count and is not checked: "en 3 días", invented, passes.
4. The authorised set is permissive: a figure that equals any number of any passage
   read, or of any question of the user, passes, even when the passage speaks of
   another thing. The check proves that the figure comes from the material read,
   not that it is the right figure for the sentence. A date of a passage authorises
   nothing: `updated: 2026-10-01` in a wiki page does not authorise "1", "10" or
   "2026".
5. A computed figure (a sum, a difference, "un 20 % más") is unsupported unless
   the computation is in the advisory table.

**The raw-source fallback is not reached on the shipped demos; a retry now reaches
the sources.** With pre-retrieval, the fallback before refusing answers from a
fragment of the original document. It runs only when the question names something
the wiki covers *and* the search over the generated pages returns nothing, and
those two conditions work against each other: naming a subject finds its page.
Measured on 2026-11-04 (v0.6.1) with the question "¿qué es …?": none of the 77
titles and aliases of the three demos reaches the fallback. An alias is searched by
the name it stands for, and finds the page ("Cinderwench" finds Cinderella); a
question that names only a dataset key goes to the data path, evaluated first.

A detail question about a covered subject, whose answer is in the source and not in
the generated page, used to end in a refusal: the model was handed pages that do not
contain the answer. Since the retry with the original documents (`Q_RETRY_SOURCES`,
`_retry_with_sources`), an answer from the pages that opens with the fixed refusal
sentence triggers a search of the source fragments and a second call to the model
with them. How often that second call answers is not measured yet.

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
They are recorded together because the entry above refers to them.

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

The mirror case belongs to the first kind. An answer that takes a detail from
one retrieved passage and attaches it to the subject of another is not caught
by reranking — every passage involved is relevant, and only the pairing is
wrong — and it is what a per-sentence entailment check detects. The NLI
verifier (`base/domain/verify/`) scores each sentence of the answer against each
passage the model read, and the web interface marks the sentences whose
entailment probability is below `[verify] threshold`.

**A third route, cheaper than either, when the source declares its own
structure.** A neural reranker judges relevance by reading question and passage
together. A document that carries headings has already stated which part it is:
introduction, method, results; installation, troubleshooting; beginning, middle,
ending. Comparing *the section a fragment came from* against *the section the
question asks about* is a string comparison, not a model.

Half of it is built and unused. `chunk_text_continuing`
(`base/domain/ingestion/chunker.py`) computes `header_breadcrumb` for every chunk,
step 6 of `_ingest_file` (`base/domain/ingestion/pipeline.py`) stores it on the
`document_chunks` row, `search_chunks` returns it (`base/domain/tools/search.py`),
and `_search_wiki_fts` (`base/domain/chat/wiki_tools.py`) renders it into what the
model sees. Every retrieved fragment already carries
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
  decision is left to a model. The NLI verifier does not take a decision: it
  marks sentences, and the answer is delivered. A reranker, or a verifier whose
  verdict rejected the answer, would put a model back into that chain. NLI
  models are small, run locally and are deterministic at temperature zero, so
  the claim would survive with qualification — but the wording would have to
  change.
- Embeddings were refused for retrieval. A cross-encoder is also a neural model,
  though a different one: it reads question and passage together instead of
  comparing vectors. Adopting it is an explicit exception to that decision, not
  a continuation of it.

**Where to put them first.** `base/domain/eval/` already holds a rubric and an
LLM judge, with `graders.py` as its deterministic pre-screen. RAGAS-style
metrics belong there as offline measurement. The NLI verifier entered the
answering path as a mark, not as a decision, and `scripts/verify_battery.py`
measures the NLI verifier offline: the script asks a list of questions N times
and verifies each answer. Making the verdict reject an answer requires a
measured threshold first.

**Where each one would be inserted, and why that is not the same as how large
the job is.** The call sites are few and already separated, so the seams are
not the obstacle:

| Route | Insertion point |
|---|---|
| breadcrumb comparison | between `retrieve_wiki` (`base/domain/chat/preretrieval.py`) and the plan, dropping or downranking rows whose `header_breadcrumb` does not match the section the question asks about |
| cross-encoder reranking | the same place, rescoring and reordering the rows instead |
| NLI verification | built: after the answer, `verify_answer` (called from `web/routes/chat.py`); the lexical check (`fragment_coverage`, node `Q_IS_GROUNDED`, `base/domain/chat/workflow/catalog.py`) still decides on Tier 2 |

One line each. What differs is what surrounds that line.

**Only the breadcrumb route is bounded end to end.** Read-side plumbing exists
(`search_chunks` already returns the field), so the whole job is: one module
shaped like `overlap.py`, one new section in `wiki_config.toml` parsed in
`config.py` beside the four it already parses, one call site, and tests. No
dependency, no model weights, no added latency, deterministic, and testable
without an LLM — which matters, because the suite runs without one. It would
also want a lint check for the mapping going stale, with `vocabulary_check` as
the precedent.

**The other two are bounded in the code and not outside it.** A cross-encoder or
an NLI model brings a dependency with weights in the hundreds of megabytes, into
a project whose installer is a stdlib-only `quickstart.py` over a hash-pinned
`requirements.txt` — the "one command, Python 3.12+" promise is the first thing
that breaks. Then: latency on every question, on top of the model call; a
configuration surface for model, threshold and per-wiki on/off; and a threshold
that has to be measured rather than chosen, which is the `base/domain/eval/`
work described above. Content language is a per-wiki property, so the model has
to be multilingual or there has to be one per language.

The NLI verifier pays those costs as follows. The dependency is the optional
group `verify` (`requirements-verify.txt`): `quickstart.py` asks whether to
install it, and the base installation does not need it. The model is
multilingual (`MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7`,
`base/domain/verify/nli.py`). The latency applies only to the turns where the
user ticks "Verify the sentences". `[verify] threshold` defaults to 0.5, and the
owner sets it per wiki. A cross-encoder would face the same costs.

**The coverage gate once matched page titles literally; routes A and C are built.**
With pre-retrieval, a question passes the coverage gate only if it names something
the wiki covers. The gate compared the question with the concept-page titles, whole
and word for word, so on the finance demo "¿las cauciones son riesgosas?" was turned
away although three pages treat exactly that. There were three routes out:

- **A — Stem the gate the way the index stems.** Built. The index and the gate both
  use the Snowball stemmer of the wiki's language (`stem_text`, the column
  `content_stemmed` of `document_chunks`), so `caución` and `cauciones` unify.
- **B — Match on the title's content words with a threshold.** Refused, and the
  reason is kept: it reopens the CEDEARs leak, where a question about one instrument
  matches a page about another because both say *riesgo*. It is what everyone
  proposes first.
- **C — Have each page declare what it is about.** Built. A concept or summary page
  declares `topics` in its front-matter; the ingestion proposes them in the same call
  that returns the aliases, a curator edits them in the page editor, and the gate
  counts each topic like a title (`page_topics`, `concept_page_names`).

Measured on the finance demo: "¿las cauciones son riesgosas?" now matches the topic
`cauciones` and the dataset category `caucion`. What C does not cover: the ingestion
can propose a topic that fits any work of the same kind (`cautionary tale` for a
fairy tale), which lets a question about another work pass; the ingestion names
every topic it adds, and the curator removes that kind.

**A malformed extraction is retried, then rejected (built).** `extract_structured`
asks for JSON in the prompt. The ingestion of a file is a graph
(`base/domain/ingestion/workflow.py`), and after the extraction (I_CONCEPTS) a decision
checks that the reply parsed. A reply that is not valid JSON is sent back to the model
with the instruction to return only the JSON (I_CONCEPTS_RETRY). A second invalid reply
raises `ExtractionError`: the run fails, no page is written, the document row gets
`status='failed'` with the reason, and the ingestion console reports it. Before, the raw
reply was written as the summary page and only a log warning recorded the failure.

What is still open: the provider's structured-output parameter would constrain
generation to the declared schema, so most invalid replies would not happen. It would be
one more branch before the retry; nothing is built.

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

**Staleness does not travel along page-to-page links, and the graph to make it
travel already exists.** Today `staleness_check` (`base/domain/lint/checks.py`) follows
`cites` only — wiki page to source document. It compares a page's timestamp
against the timestamps of the sources it cites, never against another page. So
rewriting page A leaves every page that links to A untouched, unflagged and
unreviewed. `update_references` rewrites only the *outgoing* edges of the page
being written, and `get_backlinks` (`tools/references.py`) has no production
caller at all — two tests use it and nothing else does.

What looks like propagation today is not. If B cites the same source as A and
that source changes, B is flagged on its own account, not because A changed.

*Why this matters.* Page B may reference A because it touches, in passing, a
subject A treats in full. If what A says about that subject changed, B's
sentence about it may now be wrong. Nothing surfaces that.

*The distinction that decides whether the check is useful.* Two link kinds hide
behind one edge type:

| | What B does | Does a change in A merit review |
|---|---|---|
| pointer | "for the detail, see A", restating nothing | no — the pointer still points correctly |
| restatement | "X is a kind of Y, covered in A" | yes — B holds a copy of a claim whose authoritative version is in A |

`links_to` records that a markdown link exists and nothing more. And in this
project most `links_to` edges are pointers by construction: the `missing_xref`
repair inserts "See also" links between concept pages citing a shared source,
and the ingestion walkthrough measures that pass as the origin of most new
edges. Propagating over `links_to` unfiltered would flag mostly the kind that
never needs review.

*The discriminator is positional, and free.* A link inside the "See also"
section is a pointer; a link inside the body prose is the one that may
accompany a restated claim. The section heading is written by code from the
locale (`inject_see_also`), not chosen by the model, so the split is a string
operation with no model and no ambiguity. Two values — body and see-also — not
a scale: a scale invites calibration with nothing to calibrate against.

The weight belongs to the edge, not to the pair: A may link to B in its body
while B links back only under "See also". Heavy in one direction, light in the
other.

*This also settles how far to propagate.* One hop over body links only is a
small set by construction, so no arbitrary depth limit is needed.

*Derive it on read; do not store it.* There is no migration path in this
project — `_apply_base_schema` (`base/domain/tools/db.py`) runs the schema file on
open, and there is no schema version. The one `ALTER TABLE` adds the column
`content_stemmed` (`_rebuild_stemmed_index`); any other missing column raises
`SchemaMismatchError`. A new column on
`document_references` would never appear in the databases that already exist,
including both shipped demos, so storing the weight means first building a
migration mechanism, which is a larger change than the feature. Deriving it
when the check runs avoids that, keeps the classification in one place (the
check itself; `update_references` does not change), and reads page text the
lint pass already reads. It also gives the better semantics: a stored weight
would say what the link was when it was recorded, a derived one says what it is
now, and "does B currently restate something from A" is the question being
asked.

*Shape.* A new lint check, sibling to `stale`, advisory like `vocab_stale` —
deciding that a neighbour's prose has gone out of date is a judgement, not a
mechanical repair.

*Not settled.* What counts as a change to A. Pages are regenerated at
temperature 0.2–0.4, so a rewrite produces different prose from identical
content; if any rewrite flags its neighbours, every ingest floods the report and
the signal dies. The deterministic candidate is to compare the extraction rather
than the prose — the set of concept names and their insights — so a rewording
does not count and a changed concept does. Nothing is built.

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
- **Logfire as a shipped feature.** It is already installed — `pydantic-ai-slim`
  pulls it in, and `pydantic-evals` and `pydantic-graph` pull `logfire-api` —
  and instrumenting pydantic-ai with it is one call, which would trace every
  model call and tool call with its latency and token count. It stays out of the
  product for two reasons. `logfire.configure()` defaults `send_to_logfire` to
  `None`, meaning *send to Pydantic's cloud*, and what would travel is the
  prompts and the answers, which carry the content of the user's own documents;
  that contradicts the entry above. Turning it off with `send_to_logfire=False`
  works, but then the spans need an OTLP collector running somewhere, and the
  installer's promise is one command and Python 3.12+.

  It would also duplicate the existing tracers without replacing them.
  `chat/trace.py` records decisions rather than calls — `grounded`,
  `refusal_substituted`, `cited`, which tools returned something — and exists to
  diagnose failures like a prior refusal in the history priming the model to drop
  its citation on the next answer. Logfire would show the calls; those flags
  would still have to be computed and logged by hand.

  **Where it would pay, and is not refused:** as an opt-in development aid, with
  `send_to_logfire=False` and no collector — debugging a failing ingest, reading
  token counts per call, comparing models. That is the ground
  `scripts/eval_chat_model.py` and `scripts/build_eval_packet.py` cover by hand
  today. Evaluated 2026-09-14; nothing built.

- **Measurement that needs production traffic.** Sampling a percentage of live
  answers for offline scoring, shadow testing a candidate version against the
  same real traffic, and a metrics dashboard read week over week are the
  standard way to keep a RAG system under control once it is deployed. None of
  the three applies here, for the reason above: a workspace is a folder on one
  machine with one user, so there is no stream to sample, nothing running in
  parallel to compare against, and no series to plot. Measurement in this
  project is offline and deliberate — the frozen golden corpus, and the eval
  packet in `base/domain/eval/`.
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
