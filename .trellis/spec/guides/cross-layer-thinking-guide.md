# Cross-Layer Thinking Guide

> **Purpose**: Think through data flow across layers before implementing.

---

## The Problem

**Most bugs happen at layer boundaries**, not within layers.

Common cross-layer bugs:
- API returns format A, frontend expects format B
- Database stores X, service transforms to Y, but loses data
- Multiple layers implement the same logic differently

---

## Before Implementing Cross-Layer Features

### Step 1: Map the Data Flow

Draw out how data moves:

```
Source → Transform → Store → Retrieve → Transform → Display
```

For each arrow, ask:
- What format is the data in?
- What could go wrong?
- Who is responsible for validation?

### Step 2: Identify Boundaries

| Boundary | Common Issues |
|----------|---------------|
| API ↔ Service | Type mismatches, missing fields |
| Service ↔ Database | Format conversions, null handling |
| Backend ↔ Frontend | Serialization, date formats |
| Component ↔ Component | Props shape changes |

### Step 3: Define Contracts

For each boundary:
- What is the exact input format?
- What is the exact output format?
- What errors can occur?

---

## Common Cross-Layer Mistakes

### Mistake 1: Implicit Format Assumptions

**Bad**: Assuming date format without checking

**Good**: Explicit format conversion at boundaries

### Mistake 2: Scattered Validation

**Bad**: Validating the same thing in multiple layers

**Good**: Validate once at the entry point

### Mistake 3: Leaky Abstractions

**Bad**: Component knows about database schema

**Good**: Each layer only knows its neighbors

---

## Checklist for Cross-Layer Features

Before implementation:
- [ ] Mapped the complete data flow
- [ ] Identified all layer boundaries
- [ ] Defined format at each boundary
- [ ] Decided where validation happens

After implementation:
- [ ] Tested with edge cases (null, empty, invalid)
- [ ] Verified error handling at each boundary
- [ ] Checked data survives round-trip

---

## When to Create Flow Documentation

Create detailed flow docs when:
- Feature spans 3+ layers
- Multiple teams are involved
- Data format is complex
- Feature has caused bugs before

---

## Lessons From This Codebase

### Lesson 1: One piece of syntax serving two layers (the H1 regression)

**What happened:** Concept-page `## Sources` entries were written as Markdown footnotes,
`- [^1]: file.pdf`. That `[^n]:` token was doing **two unrelated jobs**:

1. **Render layer** — Marimo's markdown renderer turned it into a (visually empty) bullet.
2. **Data layer** — `references.py:update_references` parsed `[^n]:` markers
   (`_CITATION_RE`) to build the `cites` edges of the citation graph.

A fix aimed only at job #1 (drop `[^n]:` from the templates to stop the empty bullets)
silently broke job #2: concept pages stopped producing **any** parseable citation, so
`update_references` wrote **zero `cites` edges** — quietly disabling `missing_xref`,
`find_uncited_sources`, and stale detection for every concept page. No error, no test
failure (all citation tests hand-wrote `[^1]:` content, so the template→parser seam was
uncovered).

**The boundary that bit us:** the *rendered representation* and the *parsed
representation* were the **same string**. Changing one changed the other.

**Rules to avoid it:**
- When a token is both **displayed** and **parsed**, treat that as a layer boundary and
  list *every* consumer before editing it. Grep for the syntax across `marimo/` (render)
  **and** `base/domain/` (parse) — not just the file you're touching.
- Prefer parsers that accept the **rendered** form directly. The fix broadened
  `update_references` to read plain `- file.pdf` bullets under `## Sources`, so the thing
  shown and the thing parsed are now identical — no hidden second syntax.
- Add a test that exercises the **producer → consumer seam** (template output fed through
  the parser), not just hand-written fixtures of the consumer's input.

### Lesson 2: Rebuild-not-patch makes parser bugs total (and fixes self-healing)

`update_references` doesn't diff — it `DELETE`s a page's outgoing edges and re-`INSERT`s
the full set parsed from current content. Consequence: the moment the parser stops
matching the page format, the next call **deletes the good edges and inserts nothing** —
one run zeroes a page out. The flip side is the fix needs no migration: correct the
parser, reprocess/regenerate the page, and the edges rebuild. When a store is rebuilt
rather than patched, a parser regression is **total and immediate**, so the
producer→consumer seam test (Lesson 1) is the safety net.

### Lesson 3: Commit-then-generate needs compensating rollback (M3)

`ingest_file` commits the source row at step 6 and **closes the connection**, then
generates wiki pages in steps 7–9 with their own connections — so no single transaction
spans the work. A failure mid-step-8 left already-created concept pages behind while the
source was marked `failed`. Fix: record a compensation per page created/overwritten
(`wiki_compensations`) and undo them in the `except` handler (delete new pages, restore
overwritten ones). **Lesson:** when you deliberately commit early for visibility, you owe
a compensating-cleanup path on failure — "the transaction will roll it back" is false once
the connection is closed.

### Lesson 4: Confine LLM-callable file paths (M1)

`read_wiki_page(path)` is called by the chat agent, and ingested content can carry prompt
injection, so `path` is **untrusted input crossing into the filesystem layer**. `lstrip("/")`
didn't neutralise `../`. Fix: `resolve()` the path and reject unless it
`is_relative_to(wiki_root)`. **Lesson:** any path that originates from an LLM/tool argument
is a boundary input — validate it like user input before it touches disk.

### Lesson 5: Citations are a multi-layer seam that fails silently

**What happened:** With the strict default agent (`chat/config.py:_DEFAULT_SYSTEM_PROMPT` —
"answer only from the wiki, cite every fact"), in-corpus answers still arrived **uncited**.
Nothing errored; the model simply omitted citations. The cause was not one bug but a seam
spanning four layers that all have to line up for a citation to appear:

1. **Tool output (data layer)** — the retrieval tool must emit attribution *inline* with the
   content. `search_wiki_fts` / `search_source_chunks` already prefixed `**path/filename** p.N`,
   but `read_wiki_page` returned **bare page text**, so a wiki-first answer had no anchor to
   cite. Fix: `read_wiki_page` now prepends `[wiki page: <rel-path>]`.
2. **Prompt (instruction layer)** — the prompt must define what a citation *is* for each
   source kind (wiki-page path vs. source doc + page) and demand one per claim. The old prompt
   only showed a source-PDF example, so wiki-derived facts had no matching format and were
   dropped.
3. **Format example (instruction layer)** — rules alone didn't make the model cite a
   cross-document **synthesis**; it treated a comparison as "its own analysis." A worked
   example of a fully-cited comparison in the prompt is what made synthesis citations land.
4. **Model capability** — even with 1–3 correct, a weak model leaks. `gpt-4o-mini` skipped
   citations / answered off-corpus; `gpt-4o` honoured the contract. The prompt is a *request*;
   the model has to be able to follow it.

**The boundary that bit us:** a citation is an emergent property of (tool attribution ×
prompt × example × model). Any single layer being wrong drops the citation **with no error and
no test failure** — the answer is just untraceable, which is the one thing a personal wiki must
not be.

**Rules to avoid it:**
- Treat every retrieval tool's return value as a **citation source**: it must carry its own
  attribution (path / filename + page) inline, never bare content. If you "clean up" tool
  output, you may be deleting the model's only citation anchor.
- When you tighten the prompt, cover **every** retrieval path's citation format and include a
  worked example for the hardest case (multi-document synthesis) — examples drive format
  compliance far harder than rules.
- Guard the contract with executable tests at the seam, not prose:
  `test_read_wiki_page_prefixes_citation_anchor` (tool attribution) and
  `test_system_prompt_enforces_strict_grounding_and_citations` (prompt mandate) in
  `tests/unit/test_wiki_tools.py`.
- Citation/grounding quality is **model-gated**. Document a recommended chat-model floor
  (see the README model-guidance note) rather than assuming the pipeline is broken when a
  small model leaks.

**Extension — the save path makes the seam longer (the `comparisson.md` regression):**
Saving a chat answer as a wiki page (`read_app.py` Save form → `wiki_tools.save_to_wiki` →
`wiki_generator.structure_chat_content`) re-runs the cited answer through a **second LLM pass**
that re-authors it into the concept-page shape. That pass is *more* layers the citation has to
survive — and they failed silently in two new ways, plus the request framing re-broke the
earlier ones:

5. **Re-authoring prompt (instruction layer, again).** `_CONCEPT_SYSTEM` + the chat-concept
   templates said "write a clear concept page" but nothing about *keeping* citations, and they
   hardcoded `## Sources` → `- Chat synthesis`. A perfectly cited draft was reshaped into an
   uncited page with a generic source line. Fix: the structuring prompts now mandate carrying
   every inline citation verbatim and build Sources from what was actually cited.
6. **Serialization (data layer).** The same pass returned the page wrapped in a ```​markdown
   fence; written verbatim it rendered the whole page as one literal code block. Whole-document
   LLM output must be fence-stripped before it hits disk. Fix: `_strip_wrapping_fence()` on all
   four markdown generators — it strips only an *outer* wrapper, preserving genuine inner code
   blocks.
7. **Request framing re-disables layers 2–3.** "Write a concept page that compares X and Y"
   reads to the model as *authoring*, not *answering*, so it composed from memory and skipped
   retrieval+citation entirely — even though the same model cited the *question* form ("what do
   X and Y have in common?") correctly. Fix: the prompt now declares a "write/create a page"
   request to be a factual question, with no drafting exception to grounding.

**The sharper rule:** a citation must survive **every** layer between retrieval and the bytes a
human reads. Count the LLM passes — the answer path is four layers; the **save path adds two
more** (re-authoring prompt + serialization), and re-framing the request can silently re-disable
the earlier ones. Any pass that re-touches cited text is a place the citation can die with no
error and no test failure. Guard each at the seam:
`test_concept_structuring_prompts_preserve_citations` and `test_strip_wrapping_fence_*` in
`tests/unit/test_structured_extraction.py`, and
`test_system_prompt_directs_save_to_the_form_not_autonomous` in `tests/unit/test_wiki_tools.py`.
