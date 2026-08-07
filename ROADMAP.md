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
looks like a consequence rather than a decision. The machinery to judge fit
already exists in `chat/overlap.py`, used today to verify fallback answers
*after* they are produced. No change proposed yet; the trade needs measuring
first.

**The coverage gate matches page titles literally.** With pre-retrieval enabled,
whether a wiki answers a question depends on whether one of its concept-page
titles appears word-for-word in that question. Page titles are chosen by a
language model at a temperature above zero, so a wiki with three pages about the
risks of an instrument can still refuse a plainly-worded question about those
risks, and regenerating a wiki edits the list of subjects it will answer about.
Measured and written up in
[`docs/query_walkthrough.md`](docs/query_walkthrough.md#where-the-roster-shows-its-limits);
no fix proposed yet, because widening the match risks reopening the leak the gate
exists to close.

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
