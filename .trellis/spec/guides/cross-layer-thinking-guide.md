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
