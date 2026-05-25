> **ARCHIVED — historical reference only.**
> Superseded by [`docs/programmer_manual.md`](../programmer_manual.md).
> Preserved for design rationale and traceability.

---

# LLMWiki Architecture & Intelligent RAG Roadmap

This document serves as a comprehensive guide to the internal mechanics of the LLMWiki project. It details the journey of a document from raw ingestion to intelligent retrieval, and outlines how to implement "Agentic RAG" natively within the Marimo dashboard.

---

## 1. The Core Philosophy: Two Layers of Knowledge

The system is designed around a "Second Brain" philosophy, separating raw data from curated knowledge:

- **The Filing Cabinet (Raw Chunks)**: This is the AI's internal memory. Every PDF, spreadsheet, or document is broken down into small, precise pieces (chunks) and stored in a SQLite database (`index.db`). This allows the AI to be highly precise and cite specific pages or paragraphs.
- **The Encyclopedia (Wiki Pages)**: These are the Markdown (`.md`) files in your `/wiki` folder. They are the curated, synthesized "Executive Summaries" written for human consumption.

---

## 2. The Ingestion Process (The Mechanical Layer)

Ingestion is the mechanical process of taking raw files and making them searchable by the AI.

1. **File Detection**: When you place a file in the workspace, the background File Watcher (`api/domain/watcher.py`) detects it instantly.
2. **Text Extraction**: The `api/domain/local_processor.py` (and format-specific extractors like `api/services/pdf_extract.py`) extracts the raw text from the document (e.g., parsing PDFs via LibreOffice or OCR, parsing Markdown text).
3. **Chunking**: The `api/services/chunker.py` service splits the massive text blocks into smaller segments based on strict rules:
  - **Size**: ~512 tokens (approx. 2,000 characters).
  - **Overlap**: ~128 tokens, ensuring that sentences at the edge of a chunk don't lose context.
4. **Database Storage**: These chunks are inserted into the `document_chunks` table in the SQLite database via `api/infra/db/sqlite.py`.
5. **Traceability (Pointers)**: Every chunk is explicitly linked back to its origin:
  - `**document_id**`: Acts as a foreign key linking back to the `documents` table, which holds the exact file path.
  - `**page**`: For PDFs, the chunk records exactly which page it came from.
  - `**header_breadcrumb**`: For Markdown, the chunk records the header hierarchy (e.g., `## Risks > ### Currency Risk`) to provide immediate context.

*Note: Ingestion does NOT automatically generate Wiki pages. It only populates the database.*

---

## 3. The Retrieval Process (The RAG Pipeline)

Retrieval is how the system searches the ingested data to answer a question. LLMWiki uses **Keyword Search** (SQLite FTS5) rather than Vector Search. 

There are two ways to retrieve data:

### A. Static Retrieval (The Marimo "Brute Force" Method)

Currently, the Marimo notebook (`marimo/read_app.py`) bypasses the database chunks entirely. It simply reads **all** the Markdown files in the `/wiki` folder, combines them into one massive string (`build_context()`), and sends it to the LLM. 

- **Pros**: Fast for small wikis; the LLM sees the entire ecosystem at once.
- **Cons**: Breaks down as the wiki grows too large; ignores the raw PDF chunks in the database.

### B. Agentic Retrieval (The Claude Code Method)

Claude Code acts as an **Agent**. When you ask a question:

1. Claude evaluates its available "Tools" (Search, Read) exposed via the MCP Server (`mcp/local_server.py`).
2. Claude decides to call `search(query="ARS Bonds")`.
3. The MCP Server queries the FTS5 index in SQLite (`mcp/vaultfs/sqlite.py`) and returns the top 5 highly relevant chunks (with their breadcrumbs and page numbers).
4. Claude evaluates the chunks. If it needs more context, it autonomously calls the `read(path="...")` tool to view the full file.
5. Claude synthesizes the final answer.

---

## 4. The Synthesis & Maintenance Process (The Knowledge Layer)

Synthesis is the intelligent, creative step where raw chunks become human-readable knowledge. Furthermore, maintaining these pages ensures that your "Encyclopedia" doesn't become stale when the "Filing Cabinet" updates.

### A. Wiki Page Generation (Synthesis)
How does a Wiki page actually get created? It is not automatic upon ingestion. 
1. **Trigger**: Generation is triggered by the user (e.g., asking the AI to "Write a summary page on ARS Bonds" via the dashboard or a dedicated workflow like `marimo/chat_with_db.py`).
2. **Context Gathering**: The Agent uses the Retrieval Process (Step 3) to gather all relevant chunks and full documents from the database.
3. **Drafting**: The LLM synthesizes this raw data into a cohesive, structured Markdown document. It is specifically instructed to include explicit citations (e.g., `[[Document Name]]`) linking back to the source documents.
4. **Saving**: The output is written to a `.md` file in the `/wiki` directory.
5. **Self-Ingestion**: Because the new `.md` file is saved to the workspace, the File Watcher (`api/domain/watcher.py`) detects it, ingests it, and chunks it. The Wiki page itself now becomes part of the database (marked with `source_kind='wiki'`), creating a fully linked knowledge graph.

### B. Wiki Page Maintenance (Keeping Knowledge Fresh)
A wiki is a living entity. When a source PDF is updated, the wiki pages relying on it need maintenance.
1. **Detecting Stale Content**: When a source document is overwritten (e.g., a new version of a PDF is dropped in), the File Watcher (`api/domain/watcher.py`) updates its `mtime_ns` and marks the old chunks as stale. 
2. **Tracing Impact**: By querying the `document_references` table (which tracks which Wiki pages link to which PDFs via `api/services/references.py`), the system can easily identify which `.md` pages in the `/wiki` folder are now potentially outdated.
3. **Re-Synthesis**: You can ask the AI (or trigger a dashboard workflow) to review the outdated Wiki pages against the new chunks. The LLM will update the text and rewrite the Markdown file.
4. **Human Collaboration**: Because the Wiki pages are standard Markdown files on your local filesystem, you can edit them manually at any time. When you save your manual edits, the File Watcher detects the change and updates the database chunks, meaning your manual insights instantly become searchable for the AI.

---

## 5. Roadmap: Intelligent RAG in Marimo

**The Goal**: Bring the "Subtle, Agentic Search" of Claude Code directly into the Marimo Python notebook, using a cost-effective model like **Gemini 2.0 Flash**.

To achieve this without relying on the Claude Code CLI, you will need to implement an **Agentic Loop** inside a Marimo cell.

### Required Components:

1. **An Agent Framework**: Use a library like `PydanticAI` or `LangGraph`. These handle the complex "Think → Act → Observe" loop.
2. **An Agentic Model**: Configure the framework to use Gemini 2.0 Flash via OpenRouter. This model is exceptionally fast and optimized for function calling.
3. **Tool Definitions**: Expose your existing Python functions (from `mcp/vaultfs/sqlite.py`) as tools to the Agent:
  - `tool_search(query: str)`: Executes the SQLite FTS5 query and returns chunks.
  - `tool_read(path: str)`: Reads the full text of a document.

### The Execution Flow in Marimo:

1. **User Input**: You type a query into a Marimo text box.
2. **Agent Activation**: The PydanticAI agent receives the prompt and the tool list.
3. **Autonomous Loop**:
  - The Agent (Gemini) decides to call `tool_search`.
  - Your Python code executes the search against `index.db` and returns the chunks to the Agent.
  - The Agent decides it has enough context and generates the final answer.
4. **Display**: Marimo renders the markdown output beautifully on the screen.

By doing this, you replace the "Brute Force" `build_context()` method with a surgical, cost-effective, and highly intelligent RAG pipeline entirely native to your dashboard.

---

## 6. Conversation Transcript (Q&A)

**Q: Are you sure indexing for md is full file? I placed two files in my workspace and I see 38 entries in document_chunks.**  
**A:** You are correct! While Markdown files are stored in full in the `documents` table, they are *also* chunked by the `chunker.py` service. The chunker splits text every ~512 tokens with a ~128 token overlap to ensure context isn't lost. 38 chunks for 2 files means exactly 19 chunks per file.

**Q: Now let's understand retrieval. I ask a question in Claude Code. What happens next?**  
**A:** Claude Code acts as an Agent using the MCP server. It receives your question, decides it needs to use the `search` tool, and sends a query. The MCP server runs an FTS5 SQL query against `document_chunks`, extracts snippets with header breadcrumbs, and returns them to Claude. Claude reads the snippets and synthesizes an answer.

**Q: This is RAG, right?**  
**A:** Yes! It is "Retrieval-Augmented Generation." The unique twist is that it uses Keyword Search (FTS5) instead of Vector Embeddings, and the retrieval is orchestrated autonomously by the LLM (Agentic RAG).

**Q: app.py is injecting all sources in the prompt, right?**  
**A:** `app.py` (the Marimo dashboard) uses a "Full Context" approach, but *only* for the Wiki pages (`/wiki/*.md`). It reads all markdown files and joins them into one massive string. It does *not* inject the raw PDF chunks.

**Q: But I could also use RAG in the marimo notebook if I wanted to, right? After all, it is Python.**  
**A:** Absolutely. You can import the SQLite connection directly into a Marimo cell and write SQL queries against the `chunks_fts` table, allowing you to search raw PDFs directly from the dashboard.

**Q: But I should do something to trigger ingestion, right?**  
**A:** Ingestion is triggered automatically if the background server (`./llmwiki serve`) is running via the File Watcher. Alternatively, you can trigger it manually in Marimo using a subprocess call to `./llmwiki reindex`.

**Q: The Marimo version will not be so subtle, right? (Regarding Agentic search vs Brute force)**  
**A:** Correct. The current Marimo version is "Loud"—it forces the entire wiki context into the prompt every time. The MCP version is "Subtle" because the Agent decides exactly what to search for dynamically.

**Q: I meant even if I do RAG from Marimo, not the current implementation that is completely brute force.**  
**A:** Yes, standard RAG in Marimo is "Deterministic RAG" (you code the exact search steps). Claude Code is "Agentic RAG" (the AI decides the search steps).

**Q: Aren't you using Claude for both claude code and the model? I am getting a bit confused.**  
**A:** Claude Code is the CLI interface (using Anthropic's Claude model as the Agent). The LLMWiki project itself is configured (via your `.env`) to use Gemini for writing wiki pages. They are separate systems interacting together.

**Q: Can I use Claude Code from Marimo instead of going directly to Gemini? / Isn't there an SDK to talk to Claude Code programmatically?**  
**A:** You cannot use the "Claude Code" CLI programmatically. However, you can replicate its exact "Agentic" behavior inside Marimo by using an Agent framework (like PydanticAI or LangGraph) paired with an "Agentic Ready" model (like Gemini 2.0 Flash) and feeding it your existing SQLite search tools.

**Q: The other libraries you mentioned would also allow me to use a cheaper model, right? Provided it is an "agentic ready" one.**  
**A:** Exactly. Gemini 2.0 Flash is incredibly cheap, fast, and optimized for function calling. By building the agentic loop in Marimo with Gemini, you get the intelligence of Claude Code's search at a fraction of the cost.

**Q: So in the retrieval part the sequence is: Agent receives question, makes intelligent retrieval, sends whole context to model, gives GUI display?**  
**A:** Close, but the loop is collaborative. The Agent sends the question + *tools* to the Model. The Model *asks* the Agent to run a search. The Agent runs the search and sends the *results* back to the Model. The Model then synthesizes the answer. 

**Q: Doesn't the ingestion process also generate md wiki pages?**  
**A:** No. Ingestion is purely mechanical (raw text to database chunks). Synthesis is a separate, intelligent step where the AI uses those chunks to write a clean Markdown Wiki page.

**Q: So the chunks are for the background ai and the wikipages are for me?**  
**A:** Bingo. Chunks are the AI's highly precise filing cabinet. Wiki pages are your curated, readable encyclopedia.

**Q: How do the chunks point back to their origin?**  
**A:** Via database foreign keys. Every chunk has a `document_id` linking to the exact file path, a `page` number for PDFs, and a `header_breadcrumb` for Markdown sections.

---

## 7. Database Schema Details (`sqlite_schema.sql`)

This section breaks down the entire SQLite database schema responsible for managing the local index (the "Filing Cabinet"). The schema is designed to be fully reproducible from the filesystem state. It uses Write-Ahead Logging (`PRAGMA journal_mode=WAL`) for concurrent read/write performance and enforces foreign keys (`PRAGMA foreign_keys=ON`).

### `workspace`

Tracks top-level workspaces, acting as the container for all documents and knowledge.

- `**id**` (`TEXT PRIMARY KEY`): Unique identifier for the workspace.
- `**name**` (`TEXT NOT NULL`): The human-readable name of the workspace.
- `**description**` (`TEXT`): Optional description.
- `**user_id**` (`TEXT NOT NULL UNIQUE`): Associates the workspace with a specific user. Would require to add authentication.
- `**created_at**` (`TEXT`): Timestamp of creation.

### `documents`

The central registry for every file tracked by the system (fed and generated). It maintains metadata, state, and extraction status.

- `**id**` (`TEXT PRIMARY KEY`): Auto-generated unique identifier for the document.
- `**user_id**` (`TEXT NOT NULL`): The owner of the document.
- `**filename**` (`TEXT NOT NULL`): The name of the file (e.g., `report.pdf`).
- `**title**` (`TEXT`): Extracted or user-defined title.
- `**path**` (`TEXT NOT NULL`): The directory path within the workspace.
- `**relative_path**` (`TEXT NOT NULL UNIQUE`): Full relative path from the workspace root. Used for uniqueness and lookups.
- `**source_kind**` (`TEXT NOT NULL`): Categorizes the file: `wiki` (markdown files), `source` (raw documents like PDFs), or `asset` (images/media).
- `**file_type**` (`TEXT NOT NULL`): The file extension or MIME type (e.g., `pdf`, `md`).
- `**file_size**` (`INTEGER`): File size in bytes.
- `**document_number**` (`INTEGER`): Optional sequential identifier.
- `**status**` (`TEXT`): Ingestion status: `pending`, `processing`, `ready`, or `failed`.
- `**page_count**` (`INTEGER`): Total number of pages, useful for paginated documents.
- `**content**` (`TEXT`): The full, raw extracted text of the document.
- `**tags**` (`TEXT`): JSON array of associated tags.
- `**date**` (`TEXT`): Extracted document date or user-defined date.
- `**metadata**` (`TEXT`): Additional structured metadata in JSON format.
- `**error_message**` (`TEXT`): Logs any errors encountered during the `processing` state.
- `**version**` (`INTEGER`): Counter for tracking document updates.
- `**parser**` (`TEXT`): The specific parser engine used (e.g., PyPDF, MarkItDown).
- `**content_hash**` (`TEXT`): A hash of the file contents, used to detect changes efficiently.
- `**mtime_ns**` (`INTEGER`): Filesystem modification time (in nanoseconds).
- `**last_indexed_at**` (`TEXT`): Timestamp of the last successful indexing run.
- `**stale_since**` (`TEXT`): Timestamp indicating when the file was modified on disk and marked for re-indexing.
- `**created_at**` / `**updated_at**` (`TEXT`): Standard audit timestamps.

### `document_pages`

Stores the extracted text strictly broken down by page, which is essential for accurate citation in PDFs.

- `**id**` (`TEXT PRIMARY KEY`): Unique identifier for the page record.
- `**document_id**` (`TEXT NOT NULL`): Foreign key linking back to the `documents` table (`ON DELETE CASCADE`).
- `**page**` (`INTEGER NOT NULL`): The page number.
- `**content**` (`TEXT NOT NULL`): The raw text extracted specifically from this page.
- `**elements**` (`TEXT`): JSON representation of structured elements found on the page (like tables, images, or figures).
- *Constraint*: `UNIQUE(document_id, page)` ensures one record per page.

### `document_chunks`

The foundational table for AI retrieval. Documents are split into chunks of ~512 tokens for semantic precision.

- `**id**` (`TEXT PRIMARY KEY`): Unique identifier for the chunk.
- `**document_id**` (`TEXT NOT NULL`): Foreign key to the `documents` table (`ON DELETE CASCADE`).
- `**chunk_index**` (`INTEGER NOT NULL`): The sequential position of this chunk within the document.
- `**content**` (`TEXT NOT NULL`): The actual text segment to be ingested by the LLM.
- `**page**` (`INTEGER`): The page number this chunk belongs to (can be null for non-paginated files like Markdown).
- `**start_char**` (`INTEGER`): The character offset where this chunk begins in the original text.
- `**token_count**` (`INTEGER NOT NULL`): The size of the chunk in tokens.
- `**header_breadcrumb**` (`TEXT`): Contextual hierarchy, mostly used for Markdown files (e.g., `## Architecture > ### Database`).
- `**created_at**` (`TEXT`): Timestamp of creation.
- *Constraint*: `UNIQUE(document_id, chunk_index)` prevents duplicate chunks.

### `document_references`

Tracks internal linkage and citations between different documents (e.g., a wiki page linking to a PDF).

- `**id**` (`TEXT PRIMARY KEY`): Unique identifier.
- `**source_document_id**` (`TEXT NOT NULL`): Foreign key to the document making the reference.
- `**target_document_id**` (`TEXT NOT NULL`): Foreign key to the document being referenced.
- `**reference_type**` (`TEXT NOT NULL`): Defines the relationship, constrained to `cites` or `links_to`.
- `**page**` (`INTEGER`): The page in the source document where the link occurs.
- *Constraint*: `UNIQUE(source_document_id, target_document_id, reference_type)` prevents duplicate links of the same type.

### Full-Text Search (FTS5) & Triggers

To enable blazing-fast keyword search across chunks, the schema uses an SQLite FTS5 virtual table.

- `**chunks_fts**` (`VIRTUAL TABLE`): Uses the FTS5 extension. It is configured as an "external content" table tied to `document_chunks`, meaning it doesn't duplicate the text storage, saving disk space. It uses the `porter unicode61` tokenizer for word stemming and unicode support.
- **Triggers (`chunks_fts_insert`, `chunks_fts_delete`, `chunks_fts_update`)**: These are crucial. They automatically ensure that any time a row is added, removed, or modified in the `document_chunks` table, the `chunks_fts` search index is instantly and consistently updated without requiring application-level logic.

### Indexes

Several standard B-Tree indexes are created to optimize query performance during file-watcher checks and RAG operations:

- `idx_documents_relative_path`, `idx_documents_path`, `idx_documents_source_kind`, `idx_documents_status` on the `documents` table for fast path lookups and status polling.
- `idx_chunks_doc` on `document_chunks(document_id)` for quick retrieval of all chunks for a specific document.
- `idx_refs_source`, `idx_refs_target` on `document_references` for fast graph traversal (finding all links to or from a file).