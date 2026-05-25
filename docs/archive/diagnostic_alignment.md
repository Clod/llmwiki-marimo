> **ARCHIVED — historical reference only.**
> Superseded by [`docs/programmer_manual.md`](../programmer_manual.md).
> Preserved for design rationale and traceability.

---

# LLMWiki Diagnostic: Alignment with "LLM Wiki" Pattern

Your project is fundamentally **identical in spirit and architecture** to the high-level ideas described in Karpathy's "LLM Wiki" pattern. In fact, based on your `README.md`, your project was explicitly built as an open-source implementation of this exact pattern.

Here is a breakdown of how your project aligns with the core concepts, what you have already successfully implemented, and what needs to be done to achieve a fully **self-sufficient, programmatic maintenance system** (without relying on external agents like Claude Code).

---

## 1. The Core Philosophy
**The Idea:** The LLM incrementally builds and maintains a persistent wiki (an encyclopedia of markdown files) rather than rediscovering knowledge from scratch on every query (traditional RAG).
**Your Project:** You have perfectly captured this with your **"Two Layers of Knowledge"** architecture:
*   **The Filing Cabinet:** Raw chunks stored in a SQLite database.
*   **The Encyclopedia:** Human-readable Markdown files stored in the `/wiki` folder.

## 2. Architecture & Tooling
**The Idea:** Three layers: Raw sources (immutable), The Wiki (LLM-generated), and tooling to let the LLM operate efficiently.
**What is done:**
*   **Raw Sources:** You successfully isolate sources (`WIKI_PATH/sources/`) and never modify them.
*   **The Wiki:** You generate standard `.md` files that can be opened in Obsidian.
*   **Tooling (MCP & SQLite):** You have built a robust MCP Server and a SQLite FTS5 search index that provides the exact primitives (`guide`, `search`, `read`, `write`, `delete`) needed to interact with the local filesystem and the search index.
*   **Extraction & Chunking:** You have a fully working backend that extracts text from PDFs/DOCXs, chunks it into ~512 tokens, and stores it in SQLite for fast retrieval.

---

## 3. Conceptual Foundation: `overview.md` vs `log.md`

To understand how the wiki maintains itself, it is critical to understand the differing purposes of its two foundational files. They help both you (the human) and the LLM navigate the knowledge base as it grows.

### `overview.md` (The Content Index)
*   **Purpose:** This is the "Home Page" or "Table of Contents" of your knowledge base. It is purely **content-oriented**.
*   **Function:** It synthesizes the *current state* of your knowledge. It should contain high-level summaries of your research, list out key entities or categories, and link directly to the most important sub-pages (e.g., `[[concepts/interest-rates.md]]`).
*   **Maintenance:** It is highly mutable. Every time a new document is ingested that shifts the "big picture" or introduces a new major category, this page should be heavily rewritten to reflect the new paradigm.

### `log.md` (The Timeline)
*   **Purpose:** This is the audit trail. It is strictly **chronological** and **append-only**.
*   **Function:** It records *what happened and when*. Instead of explaining the content, it tracks the events: what documents were ingested, what queries were run, and what maintenance passes occurred. By standardizing the prefix (e.g., `## [2026-05-17] Ingested | Report.pdf`), it allows both you and the system to easily see the recent history of the wiki's evolution.
*   **Maintenance:** It is never rewritten, only appended to at the bottom.

---

## What needs to be done: The Programmatic Architecture Shift

While your backend infrastructure is extremely strong, the "bookkeeping and maintenance" workflows described in the text (updating the index, logging, and entity cross-referencing) are currently bypassed. Your current pipeline (`pipeline.py`) does a single linear pass to generate a source summary, leaving the cross-referencing work undone.

To build a **self-sufficient tool** that does not rely on an external agent, you need to shift the orchestration logic into your Python backend. The pipeline should evolve from:
`File` ➔ `Extract` ➔ `Chunk` ➔ `LLM writes Summary` ➔ `Save`

**To a multi-step orchestration pipeline:**
`File` ➔ `Extract` ➔ `Chunk` ➔ `LLM extracts Summary & Concepts (JSON)` ➔ `Save Summary` ➔ `Python checks existing Concepts` ➔ `LLM updates/creates Concept Pages` ➔ `LLM rewrites Overview.md` ➔ `Python appends to log.md`

### 1. Programmatic Logging (Appending to `log.md`)
*   **The Goal:** Maintain an append-only chronological record of ingests.
*   **Implementation:** At the end of your `ingest_file` function in `pipeline.py`, use Python to read `WIKI_PATH/wiki/log.md`, append a formatted string (e.g., `## [{current_date}] Ingested | {filename} \n - Created summary page: [[{slug}]]`), write it back to disk, and update the SQLite `documents` table. This step requires no LLM call.

### 2. Maintaining the `overview.md` (The Index)
*   **The Goal:** The `overview.md` should adapt dynamically as new documents are ingested.
*   **Implementation:** Create an `update_overview_page()` function in `wiki_generator.py`. Pass the newly generated document summary and the current `overview.md` content to the LLM. Prompt the LLM to rewrite the overview page, gracefully incorporating any new key findings or shifts in perspective from the new document, while remaining a high-level summary. Overwrite the file on disk and update the database.

### 3. Entity & Concept Extraction (Cross-Referencing)
*   **The Goal:** When a new document introduces a concept or discusses an existing one, the wiki should automatically update that specific concept's page.
*   **Implementation:** Split your LLM generation into two phases:
    *   **Phase 1 (Extraction):** Use structured output (JSON mode) to ask the LLM to return a summary of the document AND a list of 3-5 key "Entities/Concepts", along with a 2-sentence summary of what the document says about each.
    *   **Phase 2 (Graph Updating):** For each entity returned, Python checks if `wiki/concepts/{entity-slug}.md` exists. 
        *   If it does *not* exist: Prompt the LLM to write a brand new page for it based on the 2-sentence summary.
        *   If it *does* exist: Prompt the LLM with the existing concept page text and the new 2-sentence summary, instructing it to seamlessly integrate the new info and add a citation to the new source document.

### 4. The "Lint" Operation
*   **The Goal:** Periodically health-check the wiki for contradictions, orphan pages, or stale claims.
*   **Implementation:** Formalize a "Lint" workflow in your Marimo dashboard. This can be a Python script that queries the `document_references` graph to identify missing links or orphan pages, and then prompts the LLM to review flagged (stale) pages to rewrite them or resolve contradictions.

---

## Summary
Your project is completely consistent with the high-level ideas. You have successfully built the hard part: the **infrastructure** (SQLite chunking, File Watchers, MCP server, LibreOffice/PDF extraction). 

The remaining work is moving the "bookkeeping" logic out of an interactive agent and into a **programmatic, multi-step pipeline** within your FastAPI/Marimo backend. This will create a system that automatically weaves a rich, interconnected knowledge graph the moment you drop a PDF into the folder.