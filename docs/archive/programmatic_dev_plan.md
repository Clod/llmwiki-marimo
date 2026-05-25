> **ARCHIVED — historical reference only.**
> Superseded by [`docs/programmer_manual.md`](../programmer_manual.md).
> Preserved for design rationale and traceability.

---

# Detailed Development Plan: Programmatic Wiki Maintenance

This plan outlines the steps to shift the wiki maintenance logic from the interactive MCP agent into the programmatic `api_new` pipeline, creating a fully automated, self-sufficient knowledge base.

## 1. The Core Philosophy
**The Idea:** The LLM incrementally builds and maintains a persistent wiki (an encyclopedia of markdown files) rather than rediscovering knowledge from scratch on every query (traditional RAG).
**Your Project:** You have perfectly captured this with your **"Two Layers of Knowledge"** architecture:
*   **The Filing Cabinet:** Raw chunks stored in a SQLite database.
*   **The Encyclopedia:** Human-readable Markdown files stored in the `/wiki` folder.

## 2. Architecture & Tooling
**The Idea:** Three layers: Raw sources (immutable), The Wiki (LLM-generated), and The Schema (conventions & workflows). Tooling lets the LLM operate efficiently.
**What is done:**
*   **Raw Sources:** You successfully isolate raw uploaded documents (PDFs, DOCXs) in `WIKI_PATH/sources/` and never modify them.
*   **The Wiki:** You generate standard `.md` files stored in `WIKI_PATH/wiki/` that can be opened in Obsidian.
*   **The Schema:** A persistent `wiki/schema.md` document that codifies how the wiki is structured — naming conventions, YAML frontmatter schema, page templates, and workflows. This file is co-evolved by the user and the LLM as conventions mature. It is referenced by the PydanticAI agent's system prompt.
*   **Tooling (MCP & SQLite):** You have a robust SQLite FTS5 search index and an extraction backend that extracts text from PDFs/DOCXs and chunks it into ~512 tokens for fast retrieval.
*   **Version Control (Git):** The `WIKI_PATH/` directory is managed as a git repository. After each ingestion cycle, the pipeline auto-commits all wiki changes with a descriptive message, providing free version history, diff-based review of LLM edits, and rollback capability.

## 3. Conceptual Definitions: Summaries vs. Concepts
To build a successful programmatic pipeline, the system strictly separates raw files from their markdown representations:

*   **Raw Sources (`WIKI_PATH/sources/*`):** The actual, immutable uploaded files (e.g., `Q2_Report.pdf`).
*   **Summary Pages (`wiki/summaries/*.md`):** A 1-to-1 markdown reflection of an uploaded source document. When you drop "Q2_Report.pdf" into the system, it generates one Summary Page containing the title, metadata, and a direct summary of *only* that document. These pages are generally static after creation.
*   **Concept Pages (`wiki/concepts/*.md`):** These are dynamic, topic-centric encyclopedia entries. Unlike Summary Pages, a Concept Page aggregates knowledge from *multiple* sources. They represent entities (e.g., "Federal Reserve", "Apple Inc"), instruments ("AL30 Bonds"), or macro themes ("Inflation"). 
    *   **Structure:** A Concept Page contains a high-level definition, a synthesized narrative of insights gathered over time, explicitly cited evidence linking back to the Summary Pages (e.g., `[[Q2-Report-Summary]]`), and cross-links to other Concept Pages.
    *   **Purpose:** They act as the "connective tissue" of the knowledge base. When querying a theme, you read the synthesized Concept Page for a holistic view rather than piecing together scattered document summaries.

## 4. The Knowledge Base Directory Schema
To prevent naming collisions and enforce the separation of concerns described above, the entire system operates on the following strict folder structure within `WIKI_PATH`:

```text
WIKI_PATH/
├── llmwiki.db              # The SQLite database (Filing Cabinet)
├── sources/                # The Raw Intake Folder
│   ├── Q2_Report.pdf
│   └── meeting_notes.docx
└── wiki/                   # The Generated Encyclopedia
    ├── schema.md           # The Schema — LLM conventions & workflows
    ├── index.md            # Structured catalog of all pages (machine-scannable)
    ├── overview.md         # Narrative synthesis / home page
    ├── log.md              # The chronological append-only audit trail
    ├── summaries/          # 1-to-1 Document Summaries
    │   ├── q2-report.md
    │   └── meeting-notes.md
    └── concepts/           # Synthesized Topic Pages
        ├── inflation.md
        └── federal-reserve.md
```

### Detailed Directory Breakdown
*   **`llmwiki.db`**: The machine's "Filing Cabinet". It stores the raw text chunks of every ingested document, powers the FTS5 search index, and maintains the edge graph mapping which concepts cite which sources.
*   **`sources/`**: The drop-zone for humans. You place raw, immutable files (PDFs, DOCX) here. The Python pipeline extracts text from these but *never* modifies the files themselves.
*   **`wiki/`**: The root of the human-readable Markdown encyclopedia. Everything inside this directory is generated and maintained by the LLM pipeline. You read it; the LLM writes it. The entire `WIKI_PATH/` directory is a git repository — every ingestion cycle is auto-committed with a descriptive message.
    *   **`schema.md`**: The Schema layer. A persistent, version-controlled document that tells any LLM session how the wiki is structured: naming conventions for slugs, the YAML frontmatter schema, page structure templates for summaries vs. concepts, and the workflows to follow during ingest, query, and lint operations. This file is co-evolved by the user and the LLM over time as conventions mature.
    *   **`index.md`**: The structured catalog. Every page in the wiki is listed here with a link, a one-line summary, and metadata (date added, source count), organized by category (summaries, concepts). The LLM updates it on every ingest. When answering a query, the LLM reads this file first to locate relevant pages before drilling into them.
    *   **`overview.md`**: The narrative synthesis. A human-readable, high-level summary of the knowledge base's themes, key findings, and evolving thesis. The LLM rewrites this periodically to reflect the current state of knowledge. Unlike `index.md`, this is prose — it tells the story of what the wiki contains.
    *   **`log.md`**: The timeline. Python appends chronological logs here (e.g., `## [2026-05-18] Ingested | Q2_Report.pdf`) so both humans and the LLM can see what changed and when.
    *   **`wiki/summaries/`**: Contains the exact 1-to-1 markdown representations of the raw files.
    *   **`wiki/concepts/`**: The connective tissue of the wiki. These pages aggregate and synthesize knowledge about a single entity/topic across the entire knowledge base.

---

## Step 1: Deprecate MCP & Rebuild Native Tools (The Foundation)
**Objective:** The MCP server (`mcp/` directory) will be completely removed. All of its functionalities (`write`, `read`, `search`, `references`, etc.) must be absorbed into a new, clean set of Python tools (e.g., `api_new/domain/tools/`). These tools will be natively callable from Marimo notebooks and accessible to a PydanticAI agent.
*   **Actions:**
    *   Create a new module structure (e.g., `api_new/domain/tools/`) to house the standalone functions.
    *   Migrate the core logic from `mcp/vaultfs` and `mcp/tools/` into these new Python functions. Ensure they rely on standard Python arguments rather than MCP context.
    *   Create the initial `wiki/schema.md` document codifying: slug naming conventions, YAML frontmatter schema for concept pages, page structure templates (summary vs. concept), and the ingest/query/lint workflows. This file will be referenced by the PydanticAI agent's system prompt and co-evolved over time.
    *   Initialize `WIKI_PATH/` as a git repository (if not already) with a `.gitignore` excluding `llmwiki.db` and `sources/` (raw files are tracked separately).
    *   Delete the entire `mcp/` directory.
    *   Delete the entire `tests/` directory (including `conftest.py` and old pytest logic).
*   **Verification Tests:**
    *   **Functional Script:** Create a standalone Python script (or a new Marimo cell) that imports the new tools and programmatically executes a `create_page` and `search` command to verify they work natively.

## Step 2: Implement Programmatic Logging
**Objective:** Automatically append an entry to `wiki/log.md` whenever a document finishes ingestion.
*   **Actions:**
    *   In `api_new/domain/ingestion/pipeline.py`, after the wiki page is saved to SQLite and disk, instantiate the new native file-writing tool.
    *   Call the append tool (e.g., `append_to_page(path="/wiki/log.md", content="## [Date] Ingested | FileName")`).
*   **Verification Tests:**
    *   **Functional Script:** Run the ingestion pipeline from `marimo_new/ingest_app.py` with a test document.
    *   **Assertion:** Manually verify that `WIKI_PATH/wiki/log.md` contains the new formatted string (parseable via unix tools like `grep "^## \[" log.md`).

## Step 3: Dynamic `index.md` & `overview.md` Maintenance
**Objective:** Keep both the structured catalog and the narrative synthesis updated as the knowledge base grows.
*   **Actions:**
    *   **`index.md` (Structured Catalog):** In `wiki_generator.py`, add `update_index_page(new_page_path: str, one_line_summary: str, category: str)`. This function deterministically appends/updates a catalog entry (link + summary + metadata) under the appropriate category heading (Summaries, Concepts). This is a mechanical operation — no LLM call needed.
    *   **`overview.md` (Narrative Synthesis):** In `wiki_generator.py`, add `update_overview_page(new_summary: str, current_overview_content: str, llm_client)`. This function prompts the LLM to rewrite the narrative overview, incorporating the new knowledge and its implications for the wiki's evolving thesis.
    *   In `pipeline.py`, after generating the summary and concept pages, call both update functions. Use the native `create_page` tool (with `overwrite=True`) to save the outputs.
*   **Verification Tests:**
    *   **Functional Script:** Ingest a document introducing a distinctly new topic (e.g., "Quantum Computing") using `marimo_new/ingest_app.py`.
    *   **Assertion 1:** Read `index.md` and verify it contains a new entry with a link to the summary page and any new concept pages, organized under the correct category headings.
    *   **Assertion 2:** Read `overview.md` and verify that "Quantum Computing" (or related terms) now appears in the narrative text.

## Step 4: JSON Entity Extraction & Concept Synthesis
**Objective:** Replace the single-pass summary generation with a structured PydanticAI extraction workflow that separates raw sources from curated concepts, utilizing high-precision synthesis for updates.
*   **Actions:**
    *   **Phase 1 (Structured Extraction):** Update `wiki_generator.py` to use PydanticAI. The agent will read the chunks and output a strict JSON schema containing a `document_summary` and a list of `concepts` (each with a name, category, and a specific "new insight" derived from the document).
    *   **Phase 2 (Summary Page Creation):** Save the `document_summary` as a new markdown file explicitly under `wiki/summaries/{source-slug}.md`.
    *   **Phase 3 (Graph Synthesis & YAML):** Iterate through the extracted concepts using the native tools.
        *   If `wiki/concepts/{concept-slug}.md` *does not* exist: Prompt the LLM to generate a new page based on the extracted insight.
        *   If it *does* exist: Load the existing page content. Send it to the LLM alongside the new insight to **synthesize and rewrite** the concept page, integrating the new data with citations.
        *   *Crucial formatting:* Ensure the LLM injects **YAML Frontmatter** (tags, dates, source counts) at the top of every concept page to enable dynamic querying tools like Obsidian Dataview.
*   **Verification Tests:**
    *   **Functional Script:** Ingest "Document A" referencing "Federal Reserve". Verify `wiki/concepts/federal-reserve.md` is created. Ingest "Document B" also referencing "Federal Reserve".
    *   **Assertion:** Verify that the existing concept page was rewritten smoothly, includes correct YAML frontmatter, and `document_references` in the DB reflects both sources.

## Step 5: The Global Lint (Health-Check) Workflow
**Objective:** Maintain the global health of the knowledge base by periodically sweeping for contradictions, orphan pages, stale claims, missing cross-references, and under-documented concepts that fall outside the immediate blast radius of a single ingestion.
*   **Actions:**
    *   Create a `lint_wiki()` function in the `api_new` pipeline. The function runs a suite of checks and returns a structured report.
    *   **Orphan Check:** Graph analysis to find unlinked concept pages and prompt the LLM to weave them into `overview.md` and `index.md`.
    *   **Contradiction Sweep:** Feed interconnected concept pages to the LLM to identify and resolve conflicting information.
    *   **Staleness Check:** Compare the date metadata of sources cited by each concept page against the newest ingested sources. Flag concepts where the most recent citation is significantly older than recent ingests, and prompt the LLM to check if newer sources contain updated information on the same topic. This is distinct from contradictions — a stale claim was correct at the time but may now be outdated.
    *   **Missing Cross-References:** Analyze concept pages for thematic overlap. Identify pairs of pages that discuss related topics but don't link to each other. Either auto-add the wikilinks or flag them for the user.
    *   **Mentioned-but-Missing Concepts:** Scan all concept pages for wikilinks or key entity names that don't resolve to an existing `wiki/concepts/*.md` page. Prompt the LLM to either create stub pages or add them to a "to-investigate" list in the lint report.
    *   **Data Gaps & Missing Sources:** Identify areas where the wiki lacks sufficient depth. The LLM should explicitly suggest specific new questions to investigate and propose web searches or new sources to acquire.
    *   **Trigger Mechanism:** Integrate a "Run Lint" button into `marimo_new/ingest_app.py` for manual execution. Additionally, hook this function to run automatically at the tail-end of a batch ingestion cycle or individual document ingest.
*   **Verification Tests:**
    *   **Functional Script:** Manually introduce a contradiction between two existing concept pages and create a concept page that mentions a non-existent concept.
    *   **Assertion:** Trigger the Lint workflow via the Marimo UI and verify that: (a) the contradiction is identified and resolved, (b) the missing concept is flagged, (c) stale claims are detected, and (d) the LLM proposes related web searches for data gaps.

## Step 6: The Query & Interaction Workflow (No-RAG Strategy)
**Objective:** Align the querying mechanism with the LLM Wiki philosophy by completely bypassing traditional "raw chunk" RAG as the primary search, and ensure that valuable synthesis generated during a chat is actively filed back into the knowledge base.
*   **Actions:**
    *   **The Agent Decision Tree (When to use RAG):** The PydanticAI agent will be provided with two distinct search tools: `read_wiki_page` and `search_raw_chunks`. Its system prompt will enforce a strict routing logic:
        1.  **Start:** It MUST always read `wiki/index.md` first to locate relevant pages, then optionally read `wiki/overview.md` for narrative context.
        2.  **Explore:** It reads relevant `wiki/concepts/*.md` pages based on the overview.
        3.  **Evaluate:** It asks itself, *"Does the synthesized wiki contain the answer?"* If yes, it synthesizes the response for the user and STOPS.
        4.  **Fallback (RAG Trigger):** It is ONLY allowed to use the `search_raw_chunks` (FTS5 RAG) tool if: 
            *   The user asks for a hyper-granular detail (e.g., *"What was the exact revenue number on page 43?"*) that a concept page naturally summarized away.
            *   The concept page explicitly states the data is missing.
            *   The concept doesn't exist in the wiki at all.
            *   The user explicitly instructs it to check the raw documents or original sources.
    *   **Diverse Outputs:** Instruct the agent that answers do not just have to be plain text. Depending on the question, the agent should generate comparison tables, Marp-compatible slide decks, or charts.
    *   **Interaction Capture:** Implement a "File to Wiki" workflow in the chat interface. When the user and the agent collaborate to discover a new connection, or the agent generates a valuable table/slide deck, the agent is prompted to automatically generate a new Concept Page (or append to an existing one) permanently capturing that chat synthesis in its optimal format.
*   **Verification Tests:**
    *   **Functional Script:** Ask a complex analytical question requiring a comparison table in the chat.
    *   **Assertion:** Inspect the agent's tool-call history to verify it read `overview.md` and the relevant concept pages *instead* of executing a semantic similarity search. Confirm the final synthesis is saved as a new `.md` file containing the comparison table.

---

## Step 7: Batch Ingestion Workflow
**Objective:** Support dropping multiple files at once and processing them as a coordinated batch, with wiki-wide updates deferred to the end.
*   **Actions:**
    *   Create a `batch_ingest(files: list[Path])` wrapper function in the pipeline.
    *   Files are processed sequentially (so concept pages compound incrementally as each source is ingested).
    *   **Deferred updates:** `overview.md` rewrite and `lint_wiki()` are called only once at the end of the batch, not after each individual file.
    *   **Batch logging:** All files in the batch are logged as a single timestamped group in `log.md` (e.g., `## [2026-05-18] Batch Ingested | 5 files`).
    *   **Git:** A single auto-commit is made at the end of the batch with a summary message.
    *   Expose in the Marimo UI via a multi-file upload widget with a "Batch Ingest" button.
*   **Verification Tests:**
    *   **Functional Script:** Drop 3 test documents referencing overlapping concepts.
    *   **Assertion:** Verify that concept pages reflect all 3 sources, `log.md` has a single batch entry, `overview.md` was rewritten once, and lint ran once at the end.

## Step 8: Wiki Search UI
**Objective:** Provide a search textbox in the Marimo UI that lets the user search across all wiki pages (summaries + concepts) by keyword.
*   **Actions:**
    *   Add a search textbox and button to `marimo_new/ingest_app.py` (or a dedicated `wiki_browser.py` Marimo notebook).
    *   The search queries the existing FTS5 index scoped to wiki pages (not raw chunks) and returns matching page titles, snippets, and links.
    *   Results are displayed as clickable cards that expand to show the full page content inline or link to the `.md` file.
*   **Verification Tests:**
    *   **Functional Script:** Ingest a document about "Federal Reserve" and search for "Federal Reserve" in the search box.
    *   **Assertion:** Verify that both the summary page and the concept page appear in the results with relevant snippets highlighted.

---

## Future Enhancements

**The "Two-Step" Human-in-the-Loop Ingestion Pipeline:** 
Currently, document ingestion is fully automated (ingest -> extract -> update wiki). In the future, to achieve higher precision and control during bulk ingestions, the pipeline can be decoupled into two distinct stateless functions within the Marimo UI:
1.  `extract_only(file)`: Outputs the LLM's proposed JSON extraction (summary and concepts) to an editable UI form.
2.  `commit_to_wiki(edited_json)`: The user edits the JSON form and clicks "Approve", triggering the actual graph synthesis and file writes. 

*(Note: Human-in-the-loop interaction is already natively supported at the wiki level during the **Query & Interaction Phase (Step 6)**, where the user can manually guide the LLM to update or create concepts during a chat session without interrupting the backend ingestion script).*

**Web Search → Ingest Loop:**
When the lint or a query reveals a knowledge gap, define a `web_search_and_ingest` tool that can search the web, present candidate articles to the user, and upon approval, ingest the content as a new source. This closes the loop: the wiki not only identifies what's missing but can actively fill in the gaps.

**Image Handling:**
Store images from clipped articles in `sources/assets/`. During ingestion, detect inline image references in markdown sources and optionally pass images to a vision-capable LLM for additional context extraction. Embed image references in wiki pages.

**Knowledge Graph Visualization:**
The `document_references` table in SQLite already stores the citation graph. Generate an interactive graph visualization (D3.js or Mermaid) surfaced in the Marimo UI, showing which concepts link to which sources and to each other. Use it as a diagnostic tool alongside the lint workflow.

**Marp Slide Deck Generation:**
Create a `generate_marp_deck(topic, pages)` tool with a Marp template stored in `wiki/templates/`. Integrate with `marp-cli` or a Marimo cell to render presentation-quality slide decks directly from wiki content.

**Obsidian Canvas Output:**
Generate Obsidian Canvas (`.canvas` JSON files) as an output format for spatial/visual layouts of related concepts.
