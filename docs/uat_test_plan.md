# Acceptance & Regression Test Plan

This plan checks that the wiki works. Run the plan before a release, and after a
change of the model, of a prompt or of the web interface. It has three parts:

- **Part A — automated regression.** Two `pytest` commands that the CI also runs.
  They check what does not depend on the model: the database, the index, the
  deletion of a source and the save of a page. They also check the lint and the
  web interface, and they call no model. **Run Part A after every change of the code.**
- **Part B — manual acceptance.** The checks that need a person, because the
  result depends on the model: the content of the generated pages, the grounding
  and the citations of the chat answers, and the use of the interface. **Run
  Part B before a release, and after a change of the model, of a prompt or of the
  interface.**
- **Part C — model check.** A test of the models you chose, with material that
  ships with the product. **Run Part C when you choose or change a model.**

Run Part A first. A failure of Part A shows in Part B as symptoms that are hard to
trace back to their cause.

Part B uses the four public-domain fairy tales of `tests/fixtures/pdfs/`. Each
expectation of Part B is a property of the result, not an exact text. The model
writes the pages and the answers, and two runs give different words.

---

# Part A — Automated regression

Part A calls no model and needs no API key. The first command takes about three
minutes (measured on 2026-11-23):

```bash
uv run pytest tests/unit tests/regression -q
uv run --group web pytest -q tests/web
```

The first command runs the unit tests, which replace the model with a fake one,
and the regression tests of `tests/regression/`. The regression tests restore a
frozen ingestion of the four PDFs (`tests/fixtures/golden_corpus/`) into a
temporary folder. The tests then check properties that hold whatever the model
wrote, so the ingestion is checked against a real run without calling the model
again.

The second command runs the tests of the web interface: the routes, the
rendering, the design checks and the browser flows of `tests/web/e2e/`. The
browser flows need Chromium once: `uv run playwright install chromium`.

## What Part A checks, and where

| Property | Test |
|---|---|
| The four sources are `ready`, with one summary page each | golden `test_four_sources_all_ready`; unit `test_pipeline_phase2`, `test_batch_ingest` |
| Each summary page cites its source | golden `test_each_summary_cites_its_source` |
| Every concept page has a `cites` edge | golden `test_every_concept_page_has_a_cites_edge` |
| The full-text index has one row per fragment, finds a known word and passes its integrity check | golden `test_fts_rowcount_matches_chunks`, `test_fts_search_returns_hits`, `test_fts_integrity_check_passes` |
| Saving a page from the chat creates or updates the page, its index row and its fragments | unit `test_wiki_tools` |
| Deleting a source deletes the source, its fragments, the pages only that source supported and their edges | golden `test_deleting_a_source_cascades_and_drops_its_summary`; unit `test_delete_source` |
| A concept page that also cites another source is kept and marked stale | unit `test_delete_source_marks_multi_source_concept_stale` |
| An unchanged file is skipped, and a changed file is ingested again | unit `test_pipeline_phase2`, `test_batch_ingest` |
| Each operation that changes the wiki makes a labelled git commit with the local identity | unit `test_git_ops` |
| Each lint check, and each repair | unit `test_lint_*`, `test_repair*`; golden `test_lint_reports_no_errors` |
| The rows of the database and the files of `wiki/` agree | golden `test_db_and_markdown_tree_agree` |

A failed row has a SQL query in [Appendix A](#appendix-a--sql-queries-for-a-failed-part-a).
The query shows the state of a restored wiki.

## Freezing the golden corpus again

The golden corpus is part of the repository, and the regression tests read it
without a network. A deliberate change of the ingestion, for example a new
section of the summary page, changes the expected result. After such a change,
build the corpus again and freeze it. This is the one step of Part A that calls
the model:

```bash
uv run python scripts/build_golden_corpus.py build    # ingests the four PDFs again, with the model of .env
# read the pages of tests/fixtures/_golden_staging/wiki/
uv run python scripts/build_golden_corpus.py freeze   # copies the result to tests/fixtures/golden_corpus/
git add tests/fixtures/golden_corpus
```

Freeze the corpus only after a deliberate change. A corpus frozen after an
accidental change makes the regression tests accept that change.

---

# Part B — Manual acceptance

Part B needs a person, a model and the web interface. The tests of Part A
already check the rows and the files behind several steps of Part B. Part B
checks what a test cannot judge. Does a page read like an encyclopedia entry? Does
an answer come from the wiki? Does the interface do what its labels say?

## Start

1. Start the web interface from the root of the project:

   ```bash
   uv run --group web uvicorn web.app:app --port 8765
   ```

2. Open <http://localhost:8765>. The page lists the wikis.
3. Click **Open another folder…**, choose an empty folder, for example
   `/tmp/test-wiki`, and click **Create a wiki here…**. Choose **English** as the
   **Language of the wiki**, and click **Create the wiki**.
4. In the **Ingest** tab, choose the four PDFs of `tests/fixtures/pdfs/` and
   click **Ingest**. Wait until the progress log ends in a line that starts with
   🏁.

Ingesting a PDF needs a Java runtime (see the README, «Prerequisites»).

The four tales are *Cinderella*, *Little Red Riding Hood*, *The Sleeping Beauty
in the Wood* and *Snow White and the Seven Dwarfs*. The tales share motifs: a
prince, a royal family, a curse, a transformation. The shared motifs make the
ingestion link pages across tales, and Part B needs those links.

To start Part B again, delete the folder of step 3 and repeat the steps.

## B1. Ingestion: the content of the pages

**Do:** in the **Read** tab, open each summary page, then `overview` and
`index`.

**Accept:**

- Each summary page reads like an encyclopedia entry: a title and prose, not a
  copy of the source text and not JSON.
- `overview` is a synthesis of the four tales, not a copy of one summary.
- `index` lists every page.

The counts (four sources `ready`, one summary each, no `failed` source) are a
test of Part A. A summary page that is a copy of the source text points to the
model or to the prompt. The trace of B8 shows what the model received.

## B2. The Read tab: navigation and rendering

**Do:** click every page of the page index, and every link inside the pages.

**Accept:**

- The page index lists the pages of `wiki/` and nothing else.
- Each page shows its own Markdown: headings, prose and links.
- A link to another page opens that page. A link to a source document opens the
  document in a new tab. No link shows `[[…]]` or ends in an error page.

## B3. The chat: grounding and citations

The model has read these four tales during its training. A question about the
plot («what happens at the ball?») cannot tell the wiki from the training of the
model. A question about a detail of this edition
can: a name, a number or a phrase that only this PDF has.

**Do:** in the **Read** tab, ask these questions, each one in the three values of
the **Mode** selector:

1. A question about a detail of this edition. Find the detail in a source PDF
   first.
2. *What do these stories have in common?*
3. *What is the capital of France?*

**Accept, in the three modes:**

- Every factual sentence names a page, `(wiki/summaries/cinderella.md)`, or a
  source with its page, `(Cinderella.pdf, p. 3)`. Each answer ends with a
  reference line.
- The names and the events agree with the tales.

**Accept, per mode:**

- **Pre-retrieval:** question 3 is refused before the model is called. The
  answer says that the question names nothing the wiki covers. The button
  **Criterion** opens the audit, and the path of the question ends in
  `R_NOT_IN_ROSTER`.
- **Agent with evidence check:** question 3 is declined, or answered with a
  warning that no consultation of the wiki backs the answer. A plain «Paris»
  with no warning is a failure.
- **No verification:** the answer appears as the model writes it. Nothing checks
  the answer, so this mode can answer question 3. Record what the model did.

The buttons **Criterion**, **Sources** and **Model answer** open the audit of the
last answer. The audit shows the path of the question through the nodes of
[`query_nodes.md`](query_nodes.md) and the passages the model received. It also
shows the text of the model before the code replaced it.

When answers leave the wiki or come without citations, check the
`[assistant] system_prompt` of `wiki_config.toml` first: a looser prompt there
replaces the default. Then check the model with Part C. A prompt can ask the
model to cite every fact. Only the model can do it.

## B4. The chat: saving a conversation as a page

The model drafts the page. The person writes the page: the model has no tool
that writes to the wiki.

**Do:**

1. Ask: *Compare the villains of Cinderella and Sleeping Beauty.*
2. Click **Save** under the conversation, type a title and click **Save** in the
   panel. The model prepares a draft.
3. Read the draft in the dialog **Review the page**, edit it, and click **Save to
   wiki**.
4. Ask another question. Check the page index.

**Accept:**

- Before **Save to wiki**, no new page is in the page index.
- The draft compares the villains and cites a page for each point.
- After **Save to wiki**, the message «Created the page “…”.» appears, and the
  new page is in the page index under the concepts.
- After the question of step 4, the page index has no other new page.
- **Cancel** in the dialog writes nothing.

## B5. Lint and repair

**Do:** in the **Maintain** tab, click **Run Wiki Lint & Repair** and confirm.

**Accept:**

- The findings point to real pages. A `missing_xref` finding names two pages that
  share a source and do not link to each other.
- `contradiction` can be empty on the fairy tales. An empty result is not a
  failure.
- `thin_page` counts the fragments of a source that no page covers, not the
  length of a page. The finding is advisory: the repair reports it and changes
  nothing.
- The repair adds links and pages, and deletes no page. After a second run, a
  repaired `missing_xref` finding is gone.

### B5b. The vocabulary findings need another wiki

The three vocabulary findings (`vocab_stale`, `vocab_ambiguous`,
`vocab_covered`) find nothing on the fairy tales: `vocabulary_check` returned 0
findings on `examples/fairy-tales` on 2026-11-23. The finance example has
findings.

**Do:** copy `examples/finanzas-argentinas` to a temporary folder, open the
copy in the picker, and run lint and repair in the **Maintain** tab. Lint and
repair write to the wiki, so do not run them on the example itself.

**Accept:** the log lists `vocab_ambiguous` findings, and the repair skips each
one with the message «advisory finding — no automatic repair (resolve by hand)».
On 2026-11-23 the copy gave two findings:

```text
vocab_ambiguous (warning) — Alias 'galici' maps to several canonicals: Banco Galicia, GGAL
vocab_ambiguous (warning) — Alias 'caucion' maps to several canonicals: Cauciones bursátiles, caucion
```

The ingestion model generates the aliases, so a wiki ingested again can give
other findings. A repair that reports «fixed» or «Unknown check type» for a
vocabulary finding is a failure.

| Finding | Severity | Appears when | Repair |
|---|---|---|---|
| `vocab_stale` | warning | an alias points to a concept whose page no longer exists | none, advisory |
| `vocab_ambiguous` | warning | one alias points to two or more concepts | none, advisory |
| `vocab_covered` | info | a term of the blacklist (`[fuera_de_alcance]`) now has a page or a dataset | none, advisory: the blacklist entry is obsolete |

The **Vocabulary** tab edits the aliases and the blacklist. See
[`manual/workflows/6.1-lint.md`](manual/workflows/6.1-lint.md#maintaining-the-vocabulary-lists).

## B6. Two wikis

**Do:**

1. Create a second wiki in another empty folder, with the steps of «Start».
2. Ingest one PDF into the second wiki.
3. Switch between the two wikis with the wiki selector of the header.

**Accept:** the page index, the chat and the **History** tab follow the selected
wiki. Ingesting into one wiki changes nothing in the other: each wiki has its own
`.llmwiki/index.db`.

## B7. History

**Do:** in the **History** tab, click **Go back to this point** on the point
before the last ingestion, and confirm.

**Accept:**

- The pages of the last ingestion leave the page index.
- The history gains a new point: the history is not rewritten.
- Going back to the newest point brings the pages back.

## B8. The trace (optional, for developers)

**Do:** start the web interface with the trace on, ingest one PDF and ask one
question:

```bash
WIKI_TRACE=1 uv run --group web uvicorn web.app:app --port 8765
uv run python scripts/render_trace.py /tmp/test-wiki --doc <document id>
uv run python scripts/render_trace.py /tmp/test-wiki --conversation <conversation id>
```

**Accept:** the records go to `/tmp/test-wiki/.llmwiki/traces/spans.jsonl`, and
`render_trace.py` shows the steps of the ingestion and of the chat turn, with
the calls to the model.

---

## Sign-off checklist

**Automated (must pass first):**

- [ ] **Part A** — `uv run pytest tests/unit tests/regression -q` and
  `uv run --group web pytest -q tests/web` pass.

**Manual (before a release, or after a change of the model, a prompt or the
interface):**

- [ ] **B1** The pages read like encyclopedia entries; `overview` is a synthesis.
- [ ] **B2** Every page renders; every link opens its target.
- [ ] **B3** Every fact is cited and agrees with the tales; pre-retrieval refuses
  «capital of France» in `R_NOT_IN_ROSTER`; the agent mode declines it or warns.
- [ ] **B4** The model only drafts; **Save to wiki** writes the page; nothing is
  written without that click.
- [ ] **B5** The findings make sense; the repair adds links and deletes no page.
- [ ] **B5b** On a copy of the finance example, the `vocab_ambiguous` findings are
  skipped as advisory.
- [ ] **B6** The wiki selector switches wikis; the indexes stay apart.
- [ ] **B7** Going back to a point removes and restores pages; the history gains
  a point.
- [ ] **B8** (optional) The trace is written and `render_trace.py` shows it.

---

# Part C — Is your model good enough?

The wiki runs on models, and the model you choose matters. A weak model answers
from its training instead of from your documents, and leaves facts without a
citation. Part C checks a model before you rely on it.

The wiki uses a model for each of three jobs, and each job can use a different
model:

- **The reading model** (`LLM_MODEL` in `.env`) answers the questions of the chat.
- **The ingestion model** (`WIKI_LLM_MODEL`) writes the wiki pages when you add a
  document. Without a value of its own, it uses the reading model.
- **The vision model** (`VISION_MODEL`) describes the images of the sources, and
  reads scanned PDFs when the OCR engine is `vision`. Without a value of its own,
  it uses the ingestion model.

## Test the models: one button or one command

The tests need no document of yours. Each test builds a small temporary wiki with
its own material and deletes it at the end. Your wiki is not read or changed.

- **In the web interface.** Open **Settings**. In the block «Models», each model
  has a **Test** button. The test uses the values of the fields, without saving
  them. The **?** button next to it says what the test checks.
- **In a terminal.** Put the models in `.env`, then run:

  ```bash
  uv run python scripts/eval_chat_model.py
  ```

  The command tests the reading model and the ingestion model. The command tests
  the vision model too when `VISION_MODEL` is set or `OCR_ENGINE` is `vision`.
  `--model ID` tests the reading model with that model id, and `--language es`
  uses the Spanish material.

What each test checks:

1. **The reading model** gets three questions about a wiki of two pages. The
   model must answer a question the page answers, with the datum of the page and
   a citation of the page. The model must say that it found no answer to a
   question the page does not answer. In the mode with tools, the model must
   search the wiki before it answers.
2. **The ingestion model** ingests a text of one paragraph. The model must write
   the summary page and at least one concept page that names the source, and the
   lint must find no error.
3. **The vision model** reads a scanned page that ships with the product, with an
   invented text and table. The fourteen data of the page (a code, dates, amounts,
   the cells of the table) must be in what the model reads. The model must also
   describe the page as an image, in the format the product asks for.

Each check prints one line, then the command prints a verdict:

- **✓** — the check passed.
- **✗** — the check failed: the answer of the model does not meet the check.
  Choose a stronger model and run the test again.
- **?** — the service gave no answer: a wrong URL, key or model id, or a service
  that is not running. This result says nothing about the model. Correct the
  values and run the test again.

The command ends with exit code 0 when every check passed, 1 when a check failed,
and 2 when the tests could not run.

Passing is necessary, not sufficient. The tests find a model that cannot do the
work. The tests do not show how a model handles a long document or your own
questions. Two runs of the same model can give different results: run a doubtful
test once or twice more.

## Judge the ingestion model by its pages

The test above uses one short text. The ingestion model is also judged by the
pages it writes from your documents. Ingest the four sample PDFs («Start» in
Part B) and read a summary page: that is check **B1**. Summary pages that read
like encyclopedia entries show that the ingestion model does its job.

## Choosing between two models

The tests do not rank two models that both pass. Run the tests with each model
and discard the models that fail. Between two models that pass, ask both the same
questions about your own wiki (check **B3**) and compare the answers and their
citations. The README note «Chat grounding & citations scale with the chat model,
too» compares a weak and a strong model on the same questions.

---

## Appendix A — SQL queries for a failed Part A

When a regression test fails, restore a wiki and read its real state. Use the
wiki of Part B, or restore the golden corpus:

```bash
uv run python - <<'PY'
from pathlib import Path; import tempfile
from tests.helpers.golden import restore_golden
db, ws = restore_golden(Path(tempfile.mkdtemp()))
print("db:", db, "\nworkspace:", ws)
PY
# then: sqlite3 <db>   (.mode box  .headers on)
```

**The state of the documents**, one row per kind and status:

```sql
SELECT source_kind, status, COUNT(*) AS n
FROM documents GROUP BY source_kind, status ORDER BY source_kind, status;
```

**The counts of an ingestion** (golden `test_four_sources_all_ready`, unit
pipeline tests):

```sql
SELECT
  (SELECT COUNT(*) FROM documents WHERE source_kind='source') AS sources,
  (SELECT COUNT(*) FROM documents WHERE source_kind='wiki'
        AND relative_path LIKE 'wiki/summaries/%') AS summaries;
-- the pages and fragments of one file
SELECT
  (SELECT COUNT(*) FROM document_pages  p JOIN documents d ON d.id=p.document_id
     WHERE d.filename LIKE 'Cinderella%') AS pages,
  (SELECT COUNT(*) FROM document_chunks c JOIN documents d ON d.id=c.document_id
     WHERE d.filename LIKE 'Cinderella%') AS chunks;
```

**The source of each derived page** (golden `test_each_summary_cites_its_source`):

```sql
SELECT w.relative_path AS wiki_page, s.filename AS built_from_source
FROM documents w JOIN documents s ON s.id = w.source_document_id
WHERE w.source_kind='wiki' ORDER BY w.relative_path;
```

Each summary page has one source. The pages `index`, `overview` and `log`, and a
concept page with several sources, can have `source_document_id = NULL`.

**The full-text index** (golden `test_fts_*`):

```sql
SELECT (SELECT COUNT(*) FROM document_chunks) AS chunks,
       (SELECT COUNT(*) FROM chunks_fts)      AS fts_rows;   -- equal
SELECT c.document_id, substr(c.content,1,80) AS snippet
FROM chunks_fts f JOIN document_chunks c ON c.rowid=f.rowid
WHERE chunks_fts MATCH 'princ' ORDER BY rank LIMIT 5;        -- several rows
INSERT INTO chunks_fts(chunks_fts) VALUES('integrity-check'); -- no error
```

The index holds the stems of the words (`document_chunks.content_stemmed`), so
the query searches the stem `princ`, the stem of «prince» and «princes». On the
golden corpus, `princ` finds 13 rows and `prince` finds none (measured on
2026-11-23).

**Change detection** (unit pipeline tests):

```sql
SELECT filename, version, content_hash,
       datetime(last_indexed_at) AS indexed, datetime(stale_since) AS stale_since
FROM documents WHERE source_kind='source' ORDER BY filename;
```

A scan of an unchanged folder keeps `content_hash`, `version` and
`last_indexed_at`. A changed file gets a new `content_hash`, and its summary page
is written again. `stale_since` is `NULL` for a source with no problem.

**The deletion of a source** (golden
`test_deleting_a_source_cascades_and_drops_its_summary`, unit `test_delete_source`):

```sql
-- after deleting, for example, Little Red Riding Hood:
SELECT COUNT(*) FROM documents WHERE filename LIKE 'Little Red%' AND source_kind='source'; -- 0
SELECT
  (SELECT COUNT(*) FROM document_pages  p LEFT JOIN documents d ON d.id=p.document_id WHERE d.id IS NULL) AS orphan_pages,
  (SELECT COUNT(*) FROM document_chunks c LEFT JOIN documents d ON d.id=c.document_id WHERE d.id IS NULL) AS orphan_chunks; -- 0, 0
SELECT (SELECT COUNT(*) FROM document_chunks) AS chunks, (SELECT COUNT(*) FROM chunks_fts) AS fts; -- equal
SELECT COUNT(*) FROM document_references r
LEFT JOIN documents s ON s.id=r.source_document_id
LEFT JOIN documents t ON t.id=r.target_document_id
WHERE s.id IS NULL OR t.id IS NULL;  -- 0
```

The deletion deletes every page that only the deleted source supported, for
example its summary page. A page that also cites another source is kept and
marked with `stale_since`.

**The git history of the wiki** (unit `test_git_ops`), in the folder of the wiki:

```bash
cd /tmp/test-wiki && git log --oneline && git status && git ls-files | grep '^sources/'   # the last command prints nothing
```

Each operation that changes the wiki is a labelled commit, for example
`ingest: Cinderella.pdf`, with the identity `LLM Wiki <llmwiki@local>`. The tree
is clean after the operation. Git does not track `sources/` nor `.llmwiki/`, with
one exception: `.llmwiki/aliases.generated.toml`, the generated aliases, is
tracked. With `WIKI_AUTOCOMMIT=0` the wiki has no `.git` folder, and the
ingestion still works.

---

## Appendix B — Symptoms and causes

| Symptom | Probable cause | Where |
|---|---|---|
| The ingestion of a PDF fails with «has no text layer … it looks like a scanned PDF» | the PDF is scanned and `OCR_ENGINE` is `none` | README, «Document formats» |
| A source is `failed` | the model endpoint does not answer, or the extraction failed; read `error_message` | B1 |
| `chunks` differs from `fts_rows` | a trigger of the index did not run | Part A `test_fts_*`, Appendix A |
| A search for an obvious word finds nothing | the word was never indexed, or the query uses the word and not its stem | Part A `test_fts_search_returns_hits` |
| The chat answers a question outside the wiki, with no warning | a looser system prompt, or a model too weak | B3, Part C |
| A summary page is a copy of the source text | the ingestion model or its prompt; read the trace | B1, B8 |
| The git tree of the wiki has changes after an operation | the commit did not run to the end | Appendix A, git |
| A page that also cites another source is deleted with a source | a regression of `delete_source` | Part A `test_delete_source_marks_multi_source_concept_stale` |
