# Pre-Release Manual Test Plan

A hands-on smoke/acceptance plan to exercise the **main user-facing
functionalities** before opening the project to the public. It uses the four
fairy-tale PDFs already in the repo and gives you, for each step:

- **Do** — the concrete action.
- **SQL** — a query to inspect the resulting database state.
- **Expect (conceptually)** — what a *correct* result looks like. Because the
  pipeline calls an LLM, exact rows, wording, and counts are
  **non-deterministic** — so the expectations are framed as **invariants and
  ranges**, not exact strings. Judge against the *shape* of the result, not a
  golden value.

> This is a manual plan, complementary to the automated suites
> (`tests/unit/`, `tests/e2e/`). It checks the things a human notices —
> readability of pages, citation quality, chat behaviour — that assertions
> don't cover well.

---

## Quick start — reset & run

Paste this from the **project root** to wipe any previous test wiki, stage the
four fairy-tale PDFs, and launch both apps + a SQL session. Each block is a
separate terminal.

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
# ── Terminal 3: live SQL session against the index ───────────────────────
sqlite3 /tmp/test-wiki/.llmwiki/index.db
#   then, once:  .mode box   .headers on
#   first look:  SELECT source_kind, status, COUNT(*) n FROM documents
#                GROUP BY source_kind, status ORDER BY 1,2;
```

> Setting `WIKI_PATH` inline (as above) overrides `.env` for that process only,
> so your real default wiki is untouched. You can also leave `.env` alone and
> just switch to `/tmp/test-wiki` with the in-app picker (top-left).

To reset and start over at any point: re-run the first three lines of
Terminal 1.

The sections below walk each functionality in depth, with the SQL and the
"what to look for" for every step.

---

## 0. Conventions & setup

### Test corpus

Four public-domain fairy tales live in `tests/fixtures/pdfs/`:

| File | Notes |
|------|-------|
| `Cinderella.pdf` | small, text-based |
| `Little Red Riding Hood.pdf` | small, text-based |
| `The Sleeping Beauty in the Wood.pdf` | small, text-based |
| `Snow White and the Seven Dwarfs.pdf` | larger (~1.2 MB), multi-page |

They share characters/motifs (princes, royalty, a curse/spell, a transformation),
which is **deliberate** — it gives the wiki something to cross-link and lets the
`missing_xref` / concept machinery actually fire.

### A throwaway wiki for testing

Don't pollute a real wiki. Make a scratch one:

```bash
mkdir -p /tmp/test-wiki/sources
cp tests/fixtures/pdfs/*.pdf /tmp/test-wiki/sources/
```

Point the apps at it either via `.env` (`WIKI_PATH=/tmp/test-wiki`) or via the
in-app wiki picker (top-left in both apps).

### The database

The runtime index lives at:

```
$WIKI_PATH/.llmwiki/index.db        # e.g. /tmp/test-wiki/.llmwiki/index.db
```

Open a read-only SQL session against it in a **second terminal** while the apps
run (WAL mode allows concurrent reads):

```bash
sqlite3 /tmp/test-wiki/.llmwiki/index.db
```

Inside `sqlite3`, turn on readable output once:

```sql
.mode box
.headers on
```

> **Run queries against a quiet DB.** If an ingest is mid-flight you'll see
> partial state (`status='processing'`). Let the app finish before asserting.

### Reusable "health" query

Keep this handy — it's the one-glance dashboard used throughout:

```sql
SELECT source_kind, status, COUNT(*) AS n
FROM documents
GROUP BY source_kind, status
ORDER BY source_kind, status;
```

---

## 1. Environment sanity

**Do**

```bash
uv sync
uv run python -c "import marimo, pydantic_ai, opendataloader_pdf; print('deps ok')"
```

Confirm `.env` has a reachable `LLM_*` endpoint (and `WIKI_LLM_*` if you split
models). For a fully local run, start Ollama / LM Studio first.

**Expect (conceptually)**

- `deps ok` prints with no import error.
- The LLM endpoint answers a trivial request (the ingest will fail loudly
  otherwise). If you only want to test the *non-LLM* plumbing, note that
  ingestion **requires** the LLM — there's no offline ingest path.

---

## 2. Ingest via upload (the primary happy path)

**Do**

1. `uv run marimo run marimo/ingest_app.py --no-sandbox --port 2718`
2. Open <http://localhost:2718>, confirm the wiki picker shows `/tmp/test-wiki`.
3. Drag **`Cinderella.pdf`** into the upload box → click **⚙️ Ingest uploaded
   file(s)**.
4. Wait for completion (watch the status/log area).

**SQL**

```sql
-- The source document and its derived wiki summary page
SELECT filename, source_kind, status, page_count, parser, file_type,
       length(content) AS content_len
FROM documents
WHERE filename LIKE 'Cinderella%' OR filename LIKE 'cinderella%'
ORDER BY source_kind;

-- Pages and chunks were extracted
SELECT
  (SELECT COUNT(*) FROM document_pages  p JOIN documents d ON d.id=p.document_id
     WHERE d.filename LIKE 'Cinderella%') AS pages,
  (SELECT COUNT(*) FROM document_chunks c JOIN documents d ON d.id=c.document_id
     WHERE d.filename LIKE 'Cinderella%') AS chunks;
```

**Expect (conceptually)**

- Exactly **one** `source` row for the PDF with `status='ready'`,
  `parser='opendataloader'`, `file_type='pdf'`, `page_count >= 1`,
  `content_len > 0`.
- At least **one** `wiki` row appears — the generated summary
  (`wiki/summaries/cinderella.md` or similar). It may take a moment after the
  source row flips to `ready`.
- `pages >= 1` and `chunks >= 1`. Chunk count scales with length; a short tale
  may be a handful of chunks. **Zero chunks = red flag** (extraction produced
  empty text — see §13 troubleshooting).
- No row stuck in `processing`, none `failed`. A `failed` row should carry a
  human-readable `error_message`.

**On disk** (independent of the DB):

```bash
ls /tmp/test-wiki/wiki/ /tmp/test-wiki/wiki/summaries/
cat /tmp/test-wiki/wiki/summaries/*cinderella*.md
```

The summary should read like a *coherent human summary of Cinderella* — title,
prose, maybe key-points — not a raw text dump or JSON. This is a qualitative
judgement: would a person recognise it as a real encyclopedia entry?

---

## 3. Ingest the rest via "Scan sources/"

**Do**

1. The other three PDFs are already in `/tmp/test-wiki/sources/` (from §0).
2. In the ingest app, click **🔄 Scan sources/ for changes**.
3. Wait for all three to process.

**SQL**

```sql
SELECT source_kind, status, COUNT(*) AS n
FROM documents GROUP BY source_kind, status;

-- One summary per source?
SELECT
  (SELECT COUNT(*) FROM documents WHERE source_kind='source') AS sources,
  (SELECT COUNT(*) FROM documents WHERE source_kind='wiki'
        AND relative_path LIKE 'wiki/summaries/%') AS summaries;
```

**Expect (conceptually)**

- `source` count == **4**, all `status='ready'`.
- `summaries` should be **4** (one per source) — or close; if one is missing,
  open that summary path on disk to see whether generation silently skipped it.
- `wiki` rows also include the structural pages: `index.md`, `overview.md`,
  `log.md`, and possibly several `concepts/*.md`. So total `wiki` > 4.
- **Idempotency check:** click **Scan** again immediately. Nothing should
  re-ingest (content unchanged → `content_hash` matches). Confirm with §11.

---

## 4. Derived-page provenance (the self-FK)

Every generated summary should point back to the source it was built from via
`documents.source_document_id`.

**SQL**

```sql
SELECT w.relative_path AS wiki_page,
       s.filename       AS built_from_source
FROM documents w
JOIN documents s ON s.id = w.source_document_id
WHERE w.source_kind='wiki'
ORDER BY w.relative_path;
```

**Expect (conceptually)**

- Each summary page resolves to exactly one `source` document, and the pairing
  is sensible (the *Cinderella* summary points at `Cinderella.pdf`, not at
  *Snow White*).
- Structural pages (`index.md`, `overview.md`, `log.md`) and multi-source
  concept pages may legitimately have `source_document_id = NULL` — they aren't
  derived from a single source. That's expected, not a bug.

---

## 5. FTS5 index integrity

The full-text index is maintained by triggers; it must stay row-aligned with
`document_chunks`.

**SQL**

```sql
-- Row counts must match exactly (external-content FTS)
SELECT (SELECT COUNT(*) FROM document_chunks) AS chunks,
       (SELECT COUNT(*) FROM chunks_fts)      AS fts_rows;

-- A real keyword search returns ranked hits
SELECT c.document_id, substr(c.content,1,80) AS snippet
FROM chunks_fts f
JOIN document_chunks c ON c.rowid = f.rowid
WHERE chunks_fts MATCH 'prince'
ORDER BY rank
LIMIT 5;

-- Integrity self-check (should return no error rows)
INSERT INTO chunks_fts(chunks_fts) VALUES('integrity-check');
```

**Expect (conceptually)**

- `chunks == fts_rows`. A mismatch means a trigger didn't fire (serious — search
  will silently miss content).
- The `MATCH 'prince'` query returns several snippets actually containing the
  word/stem (`prince`, `princes`, `princess` — porter stemming). Empty result
  on a corpus full of princes ⇒ tokenizer/trigger problem.
- The `integrity-check` pragma runs without raising. (If it errors, the FTS
  shadow tables are corrupt.)

---

## 6. Read app — navigation & viewer

**Do**

1. `uv run marimo run marimo/read_app.py --no-sandbox --port 2720`
2. Open <http://localhost:2720>. Confirm 3-column layout: nav (left), content
   (middle), chat (right).
3. Click through pages in the left nav: `index`, `overview`, each summary, each
   concept.

**Expect (conceptually)**

- The left nav lists the pages that exist on disk under `wiki/` (cross-check
  against `ls -R /tmp/test-wiki/wiki`).
- Selecting a page renders **its** markdown in the middle — headings, prose,
  working internal links. Clicking an internal cross-link navigates to the
  linked page (doesn't 404 or dump raw `[[...]]`).
- `overview.md` reads as a *synthesis across all four tales*, not a copy of one
  summary. `index.md` is a catalogue listing every page.
- No Python tracebacks in the middle panel; no empty white page for a file that
  exists on disk.

---

## 7. Chat — wiki-first RAG with citations

The default agent is **strict by design**: it answers *only* from your wiki and
sources, never from world knowledge, and every fact must be cited
(`base/domain/chat/config.py:_DEFAULT_SYSTEM_PROMPT`). This section tests that
contract.

> **Caveat on famous corpora.** These four tales are in essentially every
> model's pretraining, so a question about the *plot* ("what happens at the
> ball?") can't tell retrieval from recall — a model can answer correctly from
> memory. To actually probe grounding, use a **PDF-specific detail** (a phrase,
> name, or wording that exists only in *your* file), where a correct answer
> *proves* a tool fired. Questions 1 and 4 below are built for that.

**Do** (in the read app's right column)

Ask, one at a time:

1. **Grounding probe — PDF-specific detail.** Open one summary or source on disk,
   pick a distinctive phrase/detail that is *particular to this edition* (an
   unusual translation, a named minor character, a specific number), and ask
   about it. A correct, **cited** answer proves retrieval fired; a vague or wrong
   answer means it's leaning on memory.
2. *"What do these stories have in common?"* (cross-document synthesis)
3. *"List the royal characters across all the stories."* (forces breadth)
4. **Off-corpus refusal.** Ask something plainly outside the wiki, e.g.
   *"What's the capital of France?"*

**Expect (conceptually)**

- Answers **stream** in token by token.
- **Every factual claim is cited** — document name and, where available, page
  (e.g. `(Cinderella.pdf, p. 3)`). With the strict default this is mandatory, not
  best-effort: an uncited factual answer is a **failure**.
- Content is **grounded in the corpus** — names and events match the tales, no
  invented plot points. A character or fact that appears in no document is a
  grounding failure.
- The agent searches the wiki first (`read_wiki_page` / `search_wiki_fts`) and
  only falls back to `search_source_chunks` when the wiki pages lack detail.
- **Question 4 must be declined.** The assistant should say the question is
  outside your knowledge base and *not* answer "Paris" from world knowledge.
  Wording varies; the behaviour (refusal-to-roam) is the check. **If it answers
  "Paris", the grounding mandate isn't taking effect** — see below.

> **If grounding/citations leak** (off-corpus questions get answered, or facts
> arrive uncited): first check `WIKI_PATH/wiki_config.toml` isn't overriding the
> strict default with a looser `system_prompt`; then suspect the **model**. A
> model too weak to follow instructions will ignore the mandate and lean on
> pretraining no matter how strict the prompt — try a stronger `WIKI_LLM_*` /
> `LLM_*` model (see the README "Don't use too small a model" note). The prompt
> sets the contract; the model has to be capable of honouring it.

---

## 8. Chat — save-to-wiki (human-in-the-loop)

Saving a chat response as a wiki page is **the user's action, never the
agent's**. The agent has no write tool: it drafts the page and proposes a title
and category, then *you* commit it via the **"Save last response to wiki"** form
below the chat (which calls `save_to_wiki`). This keeps every page the user
explicitly approved — see the SECURITY.md "local + reviewable" stance.

**Do**

1. In chat: *"Write a new concept page that compares the villains across these
   stories and save it to the wiki."*
2. Read the agent's reply. It should produce a **cited** comparison draft, then
   propose a **Title** and a **Category** (*Concept* here) and tell you to use
   the Save form — it must **not** claim to have saved anything itself.
3. In the **"Save last response to wiki"** form under the chat, type the
   proposed title, pick **Concept**, and press **💾 Save to wiki**.
4. Wait for the green ✅ confirmation callout.

**SQL** (run *after* you submit the form — not before)

```sql
SELECT relative_path, source_kind, status, datetime(created_at) AS created
FROM documents
WHERE source_kind='wiki'
ORDER BY created DESC
LIMIT 5;
```

**Expect (conceptually)**

- **Before** you press the button: **no** new `wiki` row, and nothing new on
  disk under `wiki/concepts/`. The agent only drafted and proposed — confirm it
  did *not* assert it saved/created/filed the page (that would be a regression of
  the no-autonomous-write contract).
- The agent's draft is a real, **cited** comparison (Cinderella's stepmother,
  Snow White's queen, etc.), and it names a title + the *Concept* category.
- **After** you submit the form: a **new** `wiki` row appears (a `concepts/*.md`)
  with the newest `created_at`, the file exists on disk
  (`ls /tmp/test-wiki/wiki/concepts/`), and re-opening/refreshing the nav lists
  and renders it. This proves the user-driven save path updates **both** disk
  and DB index.
- An **empty** title in the form is rejected ("Title cannot be empty."), and
  submitting before any chat response is rejected ("Chat with the assistant
  first.") — the form validates, it doesn't save junk.

---

## 9. Lint — surface wiki health issues

**Do**

Run lint from the ingest app's maintenance controls (or however the UI exposes
it). The checks, by name, are: `orphan`, `stale`, `missing_xref`,
`missing_concept`, `contradiction`, `data_gap`, `gap_filled`.

**Expect (conceptually)**

- Lint completes and returns a **structured list** of findings, each with a
  `check` name, a `severity` (`info` / `warning` / `error`), and the page(s)
  involved.
- Typical fresh-corpus findings:
  - `missing_xref` (`info`) — two summaries that mention the same motif
    (e.g. a *prince*) but don't link to each other. Very likely to appear with
    these overlapping tales.
  - `orphan` (`warning`) — a concept page nothing links to.
  - `missing_concept` (`warning`) — a concept referenced in prose with no page
    on disk.
- `contradiction` (`error`) is LLM-judged and may legitimately be **empty** on
  fairy tales (they don't contradict each other factually). Empty ≠ broken.
- The point isn't a specific count — it's that lint **runs, categorises, and
  points at real pages** you can open and verify.

---

## 10. Repair — auto-fix the safe findings

There is one repair action per lint check (`repair_orphan`, `repair_stale`,
`repair_missing_xref`, `repair_missing_concept`, `repair_contradiction`,
`repair_data_gap`, `repair_gap_filled`).

**Do**

1. Note a specific `missing_xref` finding from §9 (page A ↔ page B).
2. Run repair.
3. Re-run lint.

**SQL** (confirm a reference edge was created)

```sql
SELECT s.relative_path AS from_page,
       t.relative_path AS to_page,
       r.reference_type
FROM document_references r
JOIN documents s ON s.id = r.source_document_id
JOIN documents t ON t.id = r.target_document_id
ORDER BY from_page;
```

**Expect (conceptually)**

- The previously-flagged `missing_xref` is **gone** on the re-run (or reduced).
- A corresponding `links_to` edge now exists in `document_references`, and the
  markdown for page A on disk now contains a real link to page B.
- Repair is **non-destructive**: source rows and existing content remain; counts
  from §3's health query don't shrink. Repair adds links/pages, it doesn't
  delete.

---

## 11. Change detection & re-ingest

**Do**

1. Modify a source: append a line to one of the PDFs' content is awkward, so
   instead **re-drop the same unchanged file** and Scan → confirm *no* re-ingest.
2. Then **replace** one source with a genuinely different file (e.g. copy a
   second tale over `Cinderella.pdf`'s name, or edit a `.txt` if you stage one)
   and Scan → confirm it **does** re-ingest.

**SQL**

```sql
SELECT filename, version, content_hash,
       datetime(last_indexed_at) AS indexed,
       datetime(stale_since)     AS stale_since
FROM documents
WHERE source_kind='source'
ORDER BY filename;
```

**Expect (conceptually)**

- **Unchanged re-scan:** `content_hash`, `version`, and `last_indexed_at` are
  unchanged. The pipeline skipped it (hash match). This is the idempotency
  guarantee.
- **Changed file:** `content_hash` changes, `last_indexed_at` advances, and the
  derived summary is regenerated (its content on disk differs from before).
- `stale_since` is `NULL` for healthy, current rows. A non-NULL value flags a
  row the system believes is out of sync — investigate if it lingers after a
  successful scan.

---

## 12. Deletion & cascade integrity

**Do**

Delete one source through the app's deletion tool (the `delete_source` path).
Use *Little Red Riding Hood* so the others stay intact.

**SQL** (run *before* and *after*)

```sql
-- Capture the id first
SELECT id, filename FROM documents
WHERE filename LIKE 'Little Red%' AND source_kind='source';

-- After deletion: the source and its children should be gone…
SELECT COUNT(*) AS source_rows FROM documents
WHERE filename LIKE 'Little Red%' AND source_kind='source';

-- …pages and chunks cascade-deleted (no orphans)
SELECT
  (SELECT COUNT(*) FROM document_pages  p
     LEFT JOIN documents d ON d.id=p.document_id WHERE d.id IS NULL) AS orphan_pages,
  (SELECT COUNT(*) FROM document_chunks c
     LEFT JOIN documents d ON d.id=c.document_id WHERE d.id IS NULL) AS orphan_chunks;

-- …FTS stays aligned…
SELECT (SELECT COUNT(*) FROM document_chunks) AS chunks,
       (SELECT COUNT(*) FROM chunks_fts)      AS fts_rows;

-- …and the derived summary is ORPHANED (kept), not destroyed
SELECT relative_path, source_document_id
FROM documents
WHERE source_kind='wiki' AND relative_path LIKE '%little-red%';
```

**Expect (conceptually)**

- `source_rows = 0` — the source document is gone.
- `orphan_pages = 0` and `orphan_chunks = 0` — `ON DELETE CASCADE` cleaned up
  pages and chunks.
- `chunks == fts_rows` still holds — delete triggers fired.
- The summary page for Little Red Riding Hood **still exists** but its
  `source_document_id` is now `NULL` (`ON DELETE SET NULL`). This is the
  deliberate "orphan the derived page rather than destroy it" behaviour. Confirm
  the markdown file is still on disk too.
- `document_references` rows touching the deleted document are gone
  (cascade on both endpoints) — no dangling edges:

```sql
SELECT COUNT(*) AS dangling
FROM document_references r
LEFT JOIN documents s ON s.id=r.source_document_id
LEFT JOIN documents t ON t.id=r.target_document_id
WHERE s.id IS NULL OR t.id IS NULL;   -- expect 0
```

---

## 13. Git snapshot of the wiki (if `WIKI_AUTOCOMMIT` ≠ 0)

**Do**

```bash
cd /tmp/test-wiki
git log --oneline
git status
```

**Expect (conceptually)**

- An ingest produced **labelled commits** (e.g. `ingest: Cinderella.pdf`) in the
  wiki's **own** repo (separate from this project's repo).
- `git status` shows a **clean** tree after an ingest — `wiki/` and the
  generated `.gitignore` are committed; `sources/` and `.llmwiki/` are **not**
  tracked (the `.gitignore` excludes them). Verify `sources/` and `.llmwiki/`
  don't appear in `git ls-files`.
- The commit identity is the local `LLM Wiki <llmwiki@local>` — it did **not**
  use your global git name/email.
- With `WIKI_AUTOCOMMIT=0`: no `.git` is created in the wiki and ingestion still
  succeeds (snapshots simply skipped). Re-test once with this flag set.

---

## 14. Multi-wiki picker

**Do**

1. Create a second scratch wiki: `mkdir -p /tmp/test-wiki-2/sources` and drop one
   PDF in it.
2. In either app, use the top-left picker to switch to `/tmp/test-wiki-2`.

**Expect (conceptually)**

- The picker lists discovered wikis (siblings of `WIKI_PATH`, or under
  `WIKI_HOME` if set) plus a recent list, and accepts a typed path.
- Switching **repoints** the app: nav, DB, and chat now reflect wiki-2's
  content, not wiki-1's. The two `.llmwiki/index.db` files stay independent
  (ingesting in one doesn't touch the other).

---

## 15. Ingestion trace (optional, developer-facing)

**Do**

```bash
WIKI_TRACE=1 uv run marimo run marimo/ingest_app.py --no-sandbox --port 2718
# ingest one PDF, then:
uv run marimo run marimo/trace_report_app.py --no-sandbox --port 2722
```

**Expect (conceptually)**

- A JSONL trace is written for the run (write-only; opt-in via the env var).
- The trace report app loads it and shows the LLM/data-flow steps for the
  ingest — prompts, model calls, chunk flow — in a readable timeline. Useful for
  diagnosing a bad summary in §2.

---

## Pass/fail summary checklist

Tick these before release. Each maps to a section above.

- [ ] **§2–3** All 4 PDFs ingest to `status='ready'`; one summary each; pages &
  chunks > 0; no `failed`/stuck rows.
- [ ] **§2/§6** Generated pages read like real encyclopedia entries, not dumps.
- [ ] **§4** Every summary's `source_document_id` resolves to the correct source.
- [ ] **§5** `chunks == fts_rows`; keyword search returns relevant hits;
  integrity-check passes.
- [ ] **§6** Read app renders all on-disk pages; internal links navigate.
- [ ] **§7** Chat streams; **every fact is cited**; stays grounded; **refuses
  off-corpus questions** (doesn't answer "Paris"). A PDF-specific detail is
  answered correctly *with* a citation (proves retrieval, not recall).
- [ ] **§8** Agent drafts + proposes title/category but does **not** self-save;
  pressing **💾 Save to wiki** creates a real page in both disk and DB; the form
  validates (rejects empty title / no chat yet).
- [ ] **§9** Lint runs, categorises by severity, points at real pages.
- [ ] **§10** Repair clears a finding and adds a real reference edge,
  non-destructively.
- [ ] **§11** Unchanged file skips re-ingest; changed file re-ingests
  (hash-based).
- [ ] **§12** Deletion cascades pages/chunks/refs, keeps FTS aligned, orphans
  (not destroys) the derived summary.
- [ ] **§13** Wiki git snapshots are labelled, clean, local-identity, and never
  track `sources/`/`.llmwiki/`.
- [ ] **§14** Picker switches wikis cleanly; indexes stay independent.
- [ ] **§15** (optional) Trace captured and viewable.

---

## Troubleshooting quick map

| Symptom | Likely cause | Where |
|---------|--------------|-------|
| `chunks = 0` for a PDF | scanned/image-only PDF (no OCR), or empty extraction | §2, README "Document formats" |
| `status='failed'` | LLM endpoint unreachable or extraction error — read `error_message` | §1, §2 |
| `chunks != fts_rows` | an FTS trigger didn't fire | §5 |
| Empty search on obvious term | tokenizer/trigger issue, or content never chunked | §5 |
| Chat answers off-corpus questions | grounding/system-prompt regression | §7 |
| Summary is a raw text dump | wiki-generation prompt/model problem — inspect trace | §2, §15 |
| Wiki git tree dirty after ingest | `.gitignore` not staging correctly, or autocommit half-ran | §13 |
| Derived page hard-deleted on source delete | `ON DELETE SET NULL` regression | §12 |

> Reset between full runs: `rm -rf /tmp/test-wiki && mkdir -p /tmp/test-wiki/sources && cp tests/fixtures/pdfs/*.pdf /tmp/test-wiki/sources/`
