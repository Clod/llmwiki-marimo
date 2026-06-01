-- =============================================================================
-- LLM Wiki Local Index Schema (SQLite + FTS5)
-- =============================================================================
--
-- PURPOSE FOR BEGINNERS:
-- This SQL file defines the structure (schema) of the SQLite database used by
-- the LLM Wiki. Think of this database as a highly-organized filing cabinet.
-- It keeps track of all documents, their text pages, smaller semantic text parts 
-- (chunks), and links between documents so the AI can search and read them instantly.
--
-- KEY CONCEPT: "Derived State"
-- This database contains "derived state". This means that if you delete this 
-- database file, the system can rebuild it completely by scanning the files 
-- inside the workspace filesystem again. No data is lost permanently!
--
-- =============================================================================

-- ── DATABASE TUNING (PRAGMAS) ───────────────────────────────────────────────

-- WAL stands for "Write-Ahead Logging". 
-- It is a high-performance logging mechanism that allows multiple readers to read 
-- the database at the same time another process is writing to it.
PRAGMA journal_mode=WAL;

-- Enforces "Foreign Key Constraints". 
-- This ensures database integrity. For example, if you have a page that belongs to 
-- a document, and you delete the document, SQLite will prevent having "orphan" pages 
-- or delete the pages automatically.
PRAGMA foreign_keys=ON;


-- ── 1. WORKSPACE TABLE ────────────────────────────────────────────────────────
-- Tracks general information about the active workspace project.
CREATE TABLE IF NOT EXISTS workspace (
    -- Unique ID for the workspace (e.g. a string UUID)
    id TEXT PRIMARY KEY,
    
    -- Human-friendly name of the workspace (e.g. "My Financial Research")
    name TEXT NOT NULL,
    
    -- Optional description about the workspace's purpose
    description TEXT DEFAULT '',
    
    -- The owner's user identifier
    user_id TEXT NOT NULL,
    
    -- When this workspace record was created (defaults to current date and time)
    created_at TEXT DEFAULT (datetime('now')),
    
    -- Constraint: Each user can only have one active workspace in this database
    UNIQUE(user_id)
);


-- ── 2. DOCUMENTS TABLE ────────────────────────────────────────────────────────
-- Represents the files inside your wiki/workspace (e.g. Markdown files, PDFs).
-- It stores key file metadata (size, paths) and indexing status.
CREATE TABLE IF NOT EXISTS documents (
    -- Unique identifier. 
    -- The default value generates a random 16-byte blob and converts it to a 
    -- 32-character lowercase hexadecimal string (a custom UUID format).
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    
    -- ID of the user who owns this document
    user_id TEXT NOT NULL,
    
    -- The actual filename (e.g., "federal-reserve.md")
    filename TEXT NOT NULL,
    
    -- A clean, human-readable title of the document
    title TEXT,
    
    -- Folder path inside the workspace directory (defaults to the root "/")
    path TEXT DEFAULT '/' NOT NULL,
    
    -- Path relative to the workspace root (e.g., "wiki/concepts/federal-reserve.md").
    -- Used to find the actual physical file.
    relative_path TEXT NOT NULL,
    
    -- Categories the files belong to. Must be 'wiki', 'source', or 'asset'.
    source_kind TEXT NOT NULL CHECK (source_kind IN ('wiki', 'source', 'asset')),
    
    -- File extension/mime-type (e.g. "md", "pdf", "txt")
    file_type TEXT NOT NULL,
    
    -- File size in bytes (defaults to 0)
    file_size INTEGER DEFAULT 0,
    
    -- Internal sequence/serial number for the document
    document_number INTEGER,
    
    -- Current processing status of the document. Must be one of the four:
    -- 'pending': Waiting to be read and analyzed
    -- 'processing': Currently being processed
    -- 'ready': Fully loaded, chunked, and searchable
    -- 'failed': An error occurred during processing
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'ready', 'failed')),
    
    -- Total pages (e.g., 5 pages for a PDF, or 1 page for standard markdown)
    page_count INTEGER,
    
    -- Full text content of the document
    content TEXT,
    
    -- Tags assigned to the document represented as a JSON array string (e.g., '["finance", "policy"]')
    tags TEXT DEFAULT '[]',
    
    -- Arbitrary date associated with the document (e.g. publication date)
    date TEXT,
    
    -- JSON text column storing unstructured metadata (e.g. author, custom attributes)
    metadata TEXT,
    
    -- Contains details about what went wrong if the status is set to 'failed'
    error_message TEXT,
    
    -- Revision/version number of the document (used to track updates)
    version INTEGER DEFAULT 0,
    
    -- Name of the parsing engine/system used to extract text (e.g. "markdown", "pdfplumber")
    parser TEXT,
    
    -- MD5/SHA256 hash of the content to quickly check if a file has changed on disk
    content_hash TEXT,
    
    -- Last modified timestamp in nanoseconds, used for efficient caching
    mtime_ns INTEGER,
    
    -- Timestamp of when the file was last indexed into the database
    last_indexed_at TEXT,
    
    -- Timestamp indicating when this database record became out of sync/stale
    stale_since TEXT,
    
    -- Timestamps indicating when this database record was created and last updated
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),

    -- Self-reference: a derived wiki page (e.g. a /wiki/summaries/ page) points back
    -- to the 'source' document it was generated from. ON DELETE SET NULL orphans the
    -- derived page rather than destroying it when the source is deleted.
    source_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,

    -- Constraint: Every document must have a unique relative path (no duplicates)
    UNIQUE(relative_path)
);


-- ── 3. DOCUMENT PAGES TABLE ──────────────────────────────────────────────────
-- For multi-page documents (like PDFs), this table stores the raw text 
-- segregated page by page.
CREATE TABLE IF NOT EXISTS document_pages (
    -- Unique ID for this page record
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    
    -- Links back to the parent document. 
    -- 'ON DELETE CASCADE' means if the document is deleted, all its pages are deleted automatically.
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    
    -- The page number (1-indexed)
    page INTEGER NOT NULL,
    
    -- The actual raw text content found on this specific page
    content TEXT NOT NULL,
    
    -- Structured layouts/elements on the page stored as JSON (e.g. bounding boxes, images, coordinates)
    elements TEXT,
    
    -- Constraint: A document cannot have duplicate entries for the exact same page number
    UNIQUE(document_id, page)
);


-- ── 4. DOCUMENT CHUNKS TABLE ─────────────────────────────────────────────────
-- Large documents are split into smaller overlapping "chunks" (semantic pieces) 
-- to feed into LLMs/AIs. This table stores those chunks.
CREATE TABLE IF NOT EXISTS document_chunks (
    -- Unique ID for this chunk record
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    
    -- Links back to the parent document (deleted automatically if parent document is deleted)
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    
    -- The order index of the chunk (0 for first chunk, 1 for second chunk, etc.)
    chunk_index INTEGER NOT NULL,
    
    -- The plain text inside this specific chunk
    content TEXT NOT NULL,
    
    -- Which page number of the document this chunk originated from (optional)
    page INTEGER,
    
    -- The character offset in the original document where this chunk starts
    start_char INTEGER,
    
    -- Number of words or AI tokens contained in this chunk
    token_count INTEGER NOT NULL,
    
    -- Section structure hierarchy breadcrumb (e.g., "Intro > Definition > Background")
    header_breadcrumb TEXT,
    
    -- Record creation timestamp
    created_at TEXT DEFAULT (datetime('now')),
    
    -- Constraint: A document cannot have duplicate entries for the exact same chunk index
    UNIQUE(document_id, chunk_index)
);


-- ── 5. DOCUMENT REFERENCES TABLE ─────────────────────────────────────────────
-- Represents a graph showing how different documents reference or link to 
-- each other (e.g. document A links to document B).
CREATE TABLE IF NOT EXISTS document_references (
    -- Unique ID for this reference link record
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    
    -- The document containing the link (source)
    source_document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    
    -- The document being linked to (target)
    target_document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    
    -- The style/nature of the reference link. Must be:
    -- 'cites': Syntactic academic-style citation
    -- 'links_to': Standard hyperlink
    reference_type TEXT NOT NULL CHECK (reference_type IN ('cites', 'links_to')),
    
    -- Optional page number where the reference is located
    page INTEGER,
    
    -- Constraint: Avoid recording duplicate identical links of the same type between two documents
    UNIQUE(source_document_id, target_document_id, reference_type)
);


-- ── 6. FULL-TEXT SEARCH (FTS5) ───────────────────────────────────────────────
-- FTS5 is a specialized SQLite extension designed to search millions of words 
-- in milliseconds (just like Google Search, but local to your app!).
-- A "VIRTUAL TABLE" is a special table backed by SQLite's internal code rather 
-- than basic rows on disk.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    -- The columns we want to index for search
    content,
    
    -- Point FTS5 to index the 'content' column from our actual 'document_chunks' table
    content='document_chunks',
    
    -- Link the virtual search rows to standard table rows using the 'rowid'
    content_rowid='rowid',
    
    -- Tokenizer instructions:
    -- 'unicode61' handles accent characters and diverse punctuation
    -- 'porter' performs word stemming (e.g. searching "running" matches "runs" and "run")
    tokenize='porter unicode61'
);


-- ── 7. TRIGGERS (FTS AUTO-SYNCRONIZATION) ─────────────────────────────────────
-- Triggers are automatic programs that run whenever a change happens to a table.
-- These three triggers keep our virtual FTS index in perfect sync with the 
-- 'document_chunks' table automatically!

-- A. Automatically index new chunks when they are added
CREATE TRIGGER IF NOT EXISTS chunks_fts_insert AFTER INSERT ON document_chunks BEGIN
    INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
END;

-- B. Automatically remove chunks from the search index when they are deleted
CREATE TRIGGER IF NOT EXISTS chunks_fts_delete AFTER DELETE ON document_chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES('delete', old.rowid, old.content);
END;

-- C. Automatically update search indexing when chunks are edited
CREATE TRIGGER IF NOT EXISTS chunks_fts_update AFTER UPDATE ON document_chunks BEGIN
    -- 1. Remove the old text index
    INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES('delete', old.rowid, old.content);
    -- 2. Insert the brand new text index
    INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
END;


-- ── 8. SPEED INDEXES ─────────────────────────────────────────────────────────
-- Indexes are like the alphabetical index at the back of a textbook.
-- Instead of reading the whole database from top to bottom (a slow scan), 
-- SQLite uses these index pointers to instantly find specific records.

-- Optimizes searching or filtering files by path or relative path
CREATE INDEX IF NOT EXISTS idx_documents_relative_path ON documents(relative_path);
CREATE INDEX IF NOT EXISTS idx_documents_path ON documents(path);

-- Optimizes filtering files by kind ('wiki', 'source', etc.) or status ('ready', 'failed')
CREATE INDEX IF NOT EXISTS idx_documents_source_kind ON documents(source_kind);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

-- Optimizes backlinks from a derived wiki page to its origin document (summary -> source)
CREATE INDEX IF NOT EXISTS idx_documents_source_doc ON documents(source_document_id);

-- Optimizes fetching chunks that belong to a particular document
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON document_chunks(document_id);

-- Optimizes traversing or building the document link/citation graph
CREATE INDEX IF NOT EXISTS idx_refs_source ON document_references(source_document_id);
CREATE INDEX IF NOT EXISTS idx_refs_target ON document_references(target_document_id);
