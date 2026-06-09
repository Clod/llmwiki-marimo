# Acceptance & Regression Test Plan

This is the **user-acceptance test (UAT)** for the wiki, split into two tiers so
the parts that *can* be regressioned are separated from the parts that need a
human:

- **Part A — Automated deterministic regression gate.** One `pytest` command that
  asserts the structural invariants (DB integrity, FTS alignment, deletion
  cascade, derived-page provenance, hash idempotency, save mechanics, lint logic,
  git snapshots). No LLM, no servers, runs in about a minute. **Run this on every code
  change.**
- **Part B — Manual acceptance (UAT).** The human-judgment checks that can't be
  regressioned because their output is non-deterministic: chat grounding and
  citations, generated-content quality, GUI interactions, and lint *finding*
  quality. **Run this before a release, or after changing the model, prompts, or
  UI.**
- **Part C — Model check (optional).** Reuses the Part-B ideas to answer one
  practical question: **is the AI model you picked good enough?** One command for
  the chat model, plus an eyeball check for the page-writing model. **Run this
  when choosing or switching models.**

> Always run **Part A first**. If it's red, fix that before bothering with the
> manual pass — a structural break will surface as confusing manual symptoms.

It uses the four fairy-tale PDFs in `tests/fixtures/pdfs/`. Every Part-B
expectation is framed as an **invariant or range**, not an exact string — the
pipeline calls an LLM, so judge against the *shape* of the result.

---

# Part A — Automated deterministic regression gate

Run this after any change. It's fast (fake-LLM unit tests + a frozen real-ingest
golden corpus), needs no API keys and no running apps:

```bash
uv run pytest tests/unit tests/regression -q
```

`tests/regression/` restores a **frozen, human-verified ingest** of the four PDFs
(`tests/fixtures/golden_corpus/index.db` + `wiki/`) into a temp dir and asserts
LLM-variation-robust invariants over it — so the deterministic backbone is
checked against a *real* ingest without re-calling the model.

## What Part A asserts (and where)

| Invariant | Enforced by |
|-----------|-------------|
| Ingest: 4 sources all `ready`, one summary each | golden `test_four_sources_all_ready`; unit `test_pipeline_phase2`, `test_batch_ingest` |
| Derived-page provenance: each summary resolves to and **cites** its source | golden `test_each_summary_cites_its_source` |
| Every concept page has a `cites` edge (H1 citation-graph guard) | golden `test_every_concept_page_has_a_cites_edge` |
| FTS5 stays row-aligned with `document_chunks`, is searchable, integrity-checks | golden `test_fts_rowcount_matches_chunks` / `test_fts_search_returns_hits` / `test_fts_integrity_check_passes` |
| Save-to-wiki mechanics (create / update / index / FTS) | unit `test_wiki_tools` |
| Deletion cascade: source + pages + chunks + refs removed, FTS realigned, **1-to-1 summary deleted outright**, no dangling edges | golden `test_deleting_a_source_cascades_and_drops_its_summary`; unit `test_delete_source` |
| Multi-source concept page **kept and marked stale** on source delete (not deleted) | unit `test_delete_source_marks_multi_source_concept_stale` |
| Change detection / hash idempotency (unchanged skips, changed re-ingests) | unit `test_pipeline_phase2`, `test_batch_ingest` |
| Git snapshot: labelled, clean tree, local identity, `sources/`+`.llmwiki/` untracked | unit `test_git_ops` |
| Lint logic (each check) + repair adds real reference edges, non-destructively | unit `test_lint_*`, `test_repair*`; golden `test_lint_reports_no_errors` |
| DB rows and on-disk markdown tree agree | golden `test_db_and_markdown_tree_agree` |

**If a row goes red**, the hands-on SQL probe for that invariant is in
[Appendix A](#appendix-a--sql-probes-for-debugging-a-red-part-a) — restore a wiki
and run the query to see the actual state.

## Re-freezing the golden corpus (after an intentional behavior change)

The golden corpus is committed; regression runs against it offline. If you
*intentionally* change ingest/summary structure, rebuild and re-freeze it (this
is the one step that needs LLM keys):

```bash
python scripts/build_golden_corpus.py build     # re-ingest the 4 PDFs (needs LLM)
# inspect tests/fixtures/_golden_staging/wiki/ by eye
python scripts/build_golden_corpus.py freeze     # snapshot → tests/fixtures/golden_corpus/
git add tests/fixtures/golden_corpus               # commit the new baseline
```

Only re-freeze on a *deliberate* change — otherwise a drifting baseline hides
regressions.

---

# Part B — Manual acceptance (UAT)

These need a human, a live LLM, or the GUI, so they can't be regressioned. Run
before a release or after changing the model, prompts, or UI. The DB-state
*mechanics* under several of these are already covered by Part A; here you judge
the things assertions can't — readability, grounding, and interaction.

## Quick start — reset & run

Paste this from the **project root** to wipe any previous test wiki, stage the
four PDFs, and launch both apps + a SQL session. Each block is a separate
terminal.

```bash
# ── Terminal 1: fresh wiki + ingest app ──────────────────────────────────
rm -rf /tmp/test-wiki && mkdir -p /tmp/test-wiki/sources
cp tests/fixtures/pdfs/*.pdf /tmp/test-wiki/sources/
WIKI_PATH=/tmp/test-wiki uv run marimo run marimo/ingest_app.py --no-sandbox --port 2718
#   → http://localhost:2718  · Scan sources/ (or drag+Ingest), wait for 'ready'
```

```bash
# ── Terminal 2: read + chat app (after Terminal 1 finishes ingesting) ─────
WIKI_PATH=/tmp/test-wiki uv run marimo run marimo/read_app.py --no-sandbox --port 2720
#   → http://localhost:2720  · read pages (middle), chat (right)
```

```bash
# ── Terminal 3: live SQL session (optional, for eyeballing) ───────────────
sqlite3 /tmp/test-wiki/.llmwiki/index.db
#   then, once:  .mode box   .headers on
```

> Setting `WIKI_PATH` inline overrides `.env` for that process only, so your real
> default wiki is untouched. Reset at any point by re-running Terminal 1.

The corpus (`tests/fixtures/pdfs/`) is four public-domain fairy tales —
*Cinderella*, *Little Red Riding Hood*, *The Sleeping Beauty in the Wood*, *Snow
White and the Seven Dwarfs*. They deliberately share motifs (princes, royalty, a
curse, a transformation) so cross-linking and the concept machinery actually
fire.

## B1. Ingest happy path & content quality

**Do:** ingest `Cinderella.pdf` via the upload box (**⚙️ Ingest uploaded
file(s)**), then **🔄 Scan sources/** to bring in the other three.

**Accept (human judgment):**

- On disk, the generated summary reads like a *coherent encyclopedia entry* —
  title, prose, maybe key-points — **not** a raw text dump or JSON:
  ```bash
  cat /tmp/test-wiki/wiki/summaries/*cinderella*.md
  ```
  Would a person recognise it as a real wiki page?
- `overview.md` reads as a *synthesis across all four tales*, not a copy of one
  summary; `index.md` is a catalogue of every page.

> The *counts* (4 sources ready, one summary each, pages/chunks > 0, no `failed`
> rows) are Part A's job — you don't need to re-check them by hand. If a summary
> is a dump, inspect the trace (B7).

## B2. Read app — navigation & rendering (GUI)

**Do:** open <http://localhost:2720>; confirm the 3-column layout (nav · content ·
chat); click through `index`, `overview`, each summary, each concept.

**Accept:**

- The left nav lists exactly the pages on disk (`ls -R /tmp/test-wiki/wiki`).
- Selecting a page renders **its** markdown — headings, prose, working internal
  links. Clicking an internal cross-link navigates (no 404, no raw `[[...]]`).
- No Python tracebacks in the middle panel; no blank page for a file that exists.

## B3. Chat — grounding & citations (the core non-deterministic check)

The default agent is **strict by design**: it answers *only* from your wiki and
sources, never from world knowledge, and every fact must be cited
(`base/domain/chat/config.py:_DEFAULT_SYSTEM_PROMPT`). This is the section whose
pass/fail rides on the model, which is why it's manual.

> **Caveat on famous corpora.** These tales are in essentially every model's
> pretraining, so a *plot* question ("what happens at the ball?") can't tell
> retrieval from recall. To actually probe grounding, use a **PDF-specific
> detail** — a phrase, name, or number that exists only in *your* file — where a
> correct answer *proves* a tool fired.

**Do** (read app's right column), one at a time:

1. **Grounding probe — PDF-specific detail.** Open a summary/source on disk, pick
   a distinctive detail particular to this edition (an odd translation, a named
   minor character, a specific number), and ask about it. A correct **cited**
   answer proves retrieval; a vague/wrong one means it leaned on memory.
2. *"What do these stories have in common?"* (cross-document synthesis)
3. *"List the royal characters across all the stories."* (forces breadth)
4. **Off-corpus refusal.** *"What's the capital of France?"*

**Accept:**

- Answers **stream** token by token.
- **Every factual claim is cited** — page path `(wiki/summaries/cinderella.md)`
  or source + page `(Cinderella.pdf, p. 3)`. With the strict default an uncited
  factual answer is a **failure**, not a nitpick.
- Content is **grounded** — names and events match the tales, no invented plot
  points.
- The agent searches the wiki first (`read_wiki_page` / `search_wiki_fts`) and
  only falls back to `search_source_chunks` when wiki pages lack detail.
- **Question 4 must be declined** — it should say the question is outside your
  knowledge base, **not** answer "Paris". Wording varies; refusal-to-roam is the
  check.

> **If grounding/citations leak** (off-corpus answered, or facts uncited): first
> check `WIKI_PATH/wiki_config.toml` isn't overriding the strict default with a
> looser `system_prompt`; then suspect the **model**. A model too weak to follow
> instructions ignores the mandate no matter how strict the prompt — try a
> stronger `WIKI_LLM_*` / `LLM_*` (see the README "Don't use too small a model"
> note). The prompt sets the contract; the model must be able to honour it.

## B4. Chat — save-to-wiki (human-in-the-loop UX)

Saving a chat response is **the user's action, never the agent's**. The agent has
no write tool: it drafts and proposes a title + category, then *you* commit via
the **"Save last response to wiki"** form (which calls `save_to_wiki`). The
DB/disk *mechanics* are asserted in Part A — here you check the **UX and the
draft quality**.

**Do:**

1. Ask: *"Write a new concept page comparing the villains in Cinderella and
   Sleeping Beauty and save it to the wiki."*
2. Read the reply: it should produce a **cited** comparison draft, propose a
   **Title** + **Category** (*Concept*), and tell you to use the form — it must
   **not** claim to have saved anything.
3. In the form, type the proposed title, pick **Concept**, press **💾 Save to
   wiki**; wait for the green ✅ callout.
4. **Re-save guard:** send another chat message. Watch `wiki/concepts/` — it must
   **not** grow on its own.

**Accept:**

- Before pressing the button: no new page on disk; the agent only drafted and
  proposed (claiming it saved would be a regression of the no-autonomous-write
  contract).
- The draft is a real, **cited** comparison (every point carries its source
  page).
- After submitting: the new `concepts/*.md` appears in the nav and renders.
- **After a successful save the title box clears** and the ✅ callout **persists**
  until the next save. The cleared box is also the re-save guard — the follow-up
  chat in step 4 must create **no** new page. A page appearing without you
  pressing the button is a regression.
- An empty title is rejected ("Title cannot be empty."); submitting before any
  chat is rejected ("Chat with the assistant first.").

## B5. Lint & repair — finding quality

**Do:** run lint, then repair, from the ingest app's maintenance controls. Checks:
`orphan`, `stale`, `missing_xref`, `missing_concept`, `contradiction`,
`data_gap`, `gap_filled`.

**Accept (judgment — Part A already proves lint *runs* and repair adds edges):**

- Findings are **sensible**: `missing_xref` between two tales that mention the
  same motif but don't link; `orphan`/`missing_concept` point at real pages you
  can open.
- The **LLM-gated** checks (`contradiction`, `data_gap`, `gap_filled`) are
  judgment calls — `contradiction` may legitimately be **empty** on fairy tales
  (empty ≠ broken).
- Repair is visibly **non-destructive**: it adds links/pages, the page count from
  the health query doesn't shrink, and a previously-flagged `missing_xref` is
  gone (or reduced) on a re-run.

## B6. Multi-wiki picker (GUI)

**Do:** make a second scratch wiki (`mkdir -p /tmp/test-wiki-2/sources`, drop one
PDF), then switch to it via the top-left picker.

**Accept:** the picker lists discovered + recent wikis and accepts a typed path;
switching **repoints** nav, DB, and chat to wiki-2; the two `.llmwiki/index.db`
stay independent (ingesting one doesn't touch the other).

## B7. Ingestion trace (optional, developer-facing)

**Do:**

```bash
WIKI_TRACE=1 uv run marimo run marimo/ingest_app.py --no-sandbox --port 2718
# ingest one PDF, then:
uv run marimo run marimo/trace_report_app.py --no-sandbox --port 2722
```

**Accept:** a JSONL trace is written (opt-in via the env var) and the report app
shows the LLM/data-flow steps — prompts, model calls, chunk flow — in a readable
timeline. Useful for diagnosing a bad summary in B1.

---

## Acceptance sign-off checklist

**Gate (must be green before anything else):**

- [ ] **Part A** — `uv run pytest tests/unit tests/regression -q` passes (all
  deterministic invariants).

**Manual acceptance (before release / after model/prompt/UI changes):**

- [ ] **B1** Generated pages read like real entries, not dumps; overview is a
  synthesis.
- [ ] **B2** Read app renders all on-disk pages; internal links navigate; no
  tracebacks.
- [ ] **B3** Chat streams; **every fact is cited**; stays grounded; **refuses
  off-corpus** ("capital of France"); a PDF-specific detail is answered with a
  citation (proves retrieval, not recall).
- [ ] **B4** Agent drafts + proposes but does **not** self-save; **💾 Save** adds
  the page; title box **clears** + ✅ notice **persists**; a follow-up chat
  triggers **no** auto re-save.
- [ ] **B5** Lint findings are sensible; repair adds a real link non-destructively.
- [ ] **B6** Picker switches wikis cleanly; indexes stay independent.
- [ ] **B7** (optional) Trace captured and viewable.

---

# Part C — Is your model good enough? (model check)

The wiki runs on an AI model, and **the model you choose matters**. A weak model
does two bad things: it **makes up answers** that aren't in your documents, and it
**forgets to show where an answer came from**. This part lets you check a model
*before* you rely on it — no deep technical knowledge needed.

There are really **two jobs**, and you can use a different model for each:

- **The answering model** (`LLM_MODEL` in your `.env`) — chats with you and
  answers questions.
- **The page-writing model** (`WIKI_LLM_MODEL`) — writes the wiki pages when you
  add a document. If you don't set this one separately, it uses the answering
  model.

## Check the answering model — one command

You don't need to add any documents: this uses a small **sample wiki** (four fairy
tales) that ships with the project. Put the model you want to try in your `.env`
as `LLM_MODEL`, then run:

```bash
uv run python scripts/eval_chat_model.py
```

It asks the assistant three questions and checks the answers:

1. **Does it refuse a question that isn't in the wiki?** We ask for the capital of
   France — which is *not* in the fairy tales. A good model says it doesn't know;
   a weak one blurts "Paris".
2. **Does it show its source?** Every fact should come with a small reference like
   `(wiki/summaries/cinderella.md)`.
3. **Does it still show sources when comparing two things?** Weak models drop the
   references exactly here.

You get a ✓ or ✗ for each, then a verdict:

- **✓ good enough** — it refuses off-topic questions and shows its sources. Safe
  to use.
- **✗ likely too weak** — it answered something it shouldn't, or gave facts with
  no source. Pick a stronger model and run it again.

> This is a quick check, not a full exam. If a result looks borderline, run it
> once or twice more — the model words things a little differently each time.

## Check the page-writing model — by eye

The page-writing model is judged by the pages it produces. Add the four sample
PDFs (the [quick-start](#quick-start--reset--run) in Part B), open a generated
summary, and ask: **does it read like a real encyclopedia page, or like a messy
dump?** That's exactly **[B1](#b1-ingest-happy-path--content-quality)** above — if
the summaries look good, the page-writing model is good enough.

## Choosing between two models

Run the command once with each model set as `LLM_MODEL`. The one that passes —
and, on the comparison question, cites **both** stories — is the better pick for
chat. For a worked example of a weak vs. a strong model, see the model-guidance
note in the README.

---

## Appendix A — SQL probes (for debugging a red Part A)

When a regression test fails, restore a wiki and inspect the real state. Either
use your `/tmp/test-wiki` from Part B's quick-start, or restore the golden corpus:

```bash
python - <<'PY'
from pathlib import Path; import tempfile
from tests.helpers.golden import restore_golden
db, ws = restore_golden(Path(tempfile.mkdtemp()))
print("db:", db, "\nworkspace:", ws)
PY
# then: sqlite3 <db>   (.mode box  .headers on)
```

**Health dashboard** — the one-glance state used throughout:

```sql
SELECT source_kind, status, COUNT(*) AS n
FROM documents GROUP BY source_kind, status ORDER BY source_kind, status;
```

**Ingest counts (golden `four_sources_all_ready`, unit pipeline tests):**

```sql
SELECT
  (SELECT COUNT(*) FROM documents WHERE source_kind='source') AS sources,
  (SELECT COUNT(*) FROM documents WHERE source_kind='wiki'
        AND relative_path LIKE 'wiki/summaries/%') AS summaries;
-- pages & chunks for a given file
SELECT
  (SELECT COUNT(*) FROM document_pages  p JOIN documents d ON d.id=p.document_id
     WHERE d.filename LIKE 'Cinderella%') AS pages,
  (SELECT COUNT(*) FROM document_chunks c JOIN documents d ON d.id=c.document_id
     WHERE d.filename LIKE 'Cinderella%') AS chunks;
```

**Derived-page provenance (golden `each_summary_cites_its_source`):**

```sql
SELECT w.relative_path AS wiki_page, s.filename AS built_from_source
FROM documents w JOIN documents s ON s.id = w.source_document_id
WHERE w.source_kind='wiki' ORDER BY w.relative_path;
```

Each summary resolves to exactly one source; structural pages (`index`,
`overview`, `log`) and multi-source concepts may legitimately have
`source_document_id = NULL`.

**FTS5 integrity (golden `test_fts_*`):**

```sql
SELECT (SELECT COUNT(*) FROM document_chunks) AS chunks,
       (SELECT COUNT(*) FROM chunks_fts)      AS fts_rows;   -- must be equal
SELECT c.document_id, substr(c.content,1,80) AS snippet
FROM chunks_fts f JOIN document_chunks c ON c.rowid=f.rowid
WHERE chunks_fts MATCH 'prince' ORDER BY rank LIMIT 5;       -- several hits
INSERT INTO chunks_fts(chunks_fts) VALUES('integrity-check'); -- must not raise
```

**Change detection (unit pipeline tests):**

```sql
SELECT filename, version, content_hash,
       datetime(last_indexed_at) AS indexed, datetime(stale_since) AS stale_since
FROM documents WHERE source_kind='source' ORDER BY filename;
```

Unchanged re-scan: `content_hash` / `version` / `last_indexed_at` stable (hash
skip). Changed file: `content_hash` changes, summary regenerates. `stale_since`
is `NULL` for healthy rows.

**Deletion cascade (golden `deleting_a_source_cascades…`, unit `test_delete_source`):**

```sql
-- after delete_source on, e.g., Little Red Riding Hood:
SELECT COUNT(*) FROM documents WHERE filename LIKE 'Little Red%' AND source_kind='source'; -- 0
SELECT
  (SELECT COUNT(*) FROM document_pages  p LEFT JOIN documents d ON d.id=p.document_id WHERE d.id IS NULL) AS orphan_pages,
  (SELECT COUNT(*) FROM document_chunks c LEFT JOIN documents d ON d.id=c.document_id WHERE d.id IS NULL) AS orphan_chunks; -- 0, 0
SELECT (SELECT COUNT(*) FROM document_chunks) AS chunks, (SELECT COUNT(*) FROM chunks_fts) AS fts; -- equal
SELECT COUNT(*) FROM document_references r
LEFT JOIN documents s ON s.id=r.source_document_id
LEFT JOIN documents t ON t.id=r.target_document_id
WHERE s.id IS NULL OR t.id IS NULL;  -- 0 dangling
```

> **Correct deletion behavior:** a **1-to-1 summary page is DELETED outright**
> (there's no source left to regenerate it from). A **multi-source concept page
> is KEPT and marked `stale_since`** (it may draw on surviving sources). This is
> `delete_source`'s relationship-based handling — *not* `ON DELETE SET NULL` on
> the summary. (The FK's `SET NULL` only matters for rows that reference the
> source but aren't themselves removed.)

**Git snapshot (unit `test_git_ops`)** — in the wiki's own repo:

```bash
cd /tmp/test-wiki && git log --oneline && git status && git ls-files | grep -E 'sources/|\.llmwiki/'  # last grep: no output
```

Labelled `ingest:` commits, clean tree, identity `LLM Wiki <llmwiki@local>`,
`sources/` + `.llmwiki/` untracked. With `WIKI_AUTOCOMMIT=0`: no `.git`, ingestion
still succeeds.

---

## Appendix B — Troubleshooting quick map

| Symptom | Likely cause | Where |
|---------|--------------|-------|
| `chunks = 0` for a PDF | scanned/image-only PDF (no OCR), or empty extraction | B1, README "Document formats" |
| `status='failed'` | LLM endpoint unreachable or extraction error — read `error_message` | B1 |
| `chunks != fts_rows` | an FTS trigger didn't fire | Part A `test_fts_*`, Appendix A |
| Empty search on obvious term | tokenizer/trigger issue, or content never chunked | Part A `test_fts_search_returns_hits` |
| Chat answers off-corpus questions | grounding/system-prompt regression, or too-weak model | B3 |
| Summary is a raw text dump | wiki-generation prompt/model problem — inspect trace | B1, B7 |
| Wiki git tree dirty after ingest | `.gitignore` not staging correctly, or autocommit half-ran | Appendix A (git) |
| Multi-source concept page deleted on source delete | should be marked stale, not deleted — `delete_source` regression | Part A `test_delete_source_marks_multi_source_concept_stale` |

> Reset between full Part-B runs: `rm -rf /tmp/test-wiki && mkdir -p /tmp/test-wiki/sources && cp tests/fixtures/pdfs/*.pdf /tmp/test-wiki/sources/`
