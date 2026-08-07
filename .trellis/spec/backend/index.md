# Backend Development Guidelines

> Best practices for backend development in this project.

---

## Overview

This directory contains guidelines for backend development. Fill in each file with your project's specific conventions.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | base/ layout, sys.path convention, module naming | Done |
| [Database Guidelines](./database-guidelines.md) | SQLite patterns, open_db, schema management (no migration layer), query conventions | Done |
| [Error Handling](./error-handling.md) | IngestResult, custom exceptions, marimo cell patterns | Done |
| [Quality Guidelines](./quality-guidelines.md) | Forbidden patterns, required conventions, test commands | Done |
| [Logging Guidelines](./logging-guidelines.md) | wiki logger hierarchy, debug mode, progress callbacks | Done |
| [Dataset Source Format](./datasets-format.md) | domain-neutral structured/transient source format (per-category markdown, self-describing front-matter, normalized rows; opt-in per workspace); contract only — producer/storage/agent deferred | Done |
| [Multilingual Content](./multilingual-content.md) | wiki-content language vs. chat-answer language contract; the "not-a-bug" language mix | Done |
| [Chat Retrieval & Grounding](./chat-retrieval.md) | FTS5 query sanitization (stop words are **per language**), the shape of an injected block (labelled by page, front-matter stripped), the `Referencia:`/`Fuente:` citation format, and the pre-retrieval plan order (both tiers roster-gated) | Done |

---

## How to Fill These Guidelines

For each guideline file:

1. Document your project's **actual conventions** (not ideals)
2. Include **code examples** from your codebase
3. List **forbidden patterns** and why
4. Add **common mistakes** your team has made

The goal is to help AI assistants and new team members understand how YOUR project works.

---

**Language**: All documentation should be written in **English**.
