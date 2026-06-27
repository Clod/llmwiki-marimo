# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(pre-1.0: **minor** = features, **patch** = fixes). English is the canonical
language for this file, matching the README policy. There is no PyPI package —
users clone and run — so versions are a communication tool, not a dependency
contract. See [`RELEASING.md`](RELEASING.md) for the process.

## [Unreleased]

### Added
- **Per-wiki multilingual content (en/es, extensible).** `[wiki] language` in
  `wiki_config.toml` now governs the language of generated pages, section
  headers, *and* chat answers — independent of the source documents' language.
  Includes an i18n locale registry, localized ingestion / chat / lint / repair,
  a Spanish `README_ES.md` (English remains canonical), and
  `wiki_config_es.example.toml`.

### Fixed
- Guard `None` LLM message content and log previously-swallowed backend errors.
- Render delete feedback in the marimo apps and surface notebook errors in logs.

## [0.1.0] - 2026-06-12

Initial public release — a local-first, agentic LLM-wiki.

### Added
- **Ingestion pipeline** — PDF/DOCX → page text → overlapped chunks → structured
  concept extraction → summary + concept pages, catalogue, overview, and
  timeline; content-hash change detection and optional git snapshots per ingest.
- **Agentic wiki-first chat** — a PydanticAI agent that reads the curated wiki
  first (`index.md` → wiki FTS5 → raw source chunks as fallback) and cites the
  document + page for every fact; streamed answers; a human-in-the-loop
  **Save to wiki** step.
- **Self-maintenance** — lint checks (contradictions, stale pages, orphans,
  missing concepts/cross-refs, data gaps) with auto-repair of the safe ones.
- **Evaluation** — a judge-ready eval packet (frozen 1–5 rubric) and a
  one-command model-suitability PASS/FAIL check.
- **Multi-wiki picker** — discovery + recent list + path hygiene, shared by both
  marimo apps.
- **Transparency** — a queryable SQLite citation graph and opt-in JSONL tracing
  (`WIKI_TRACE=1`) with a dedicated trace-report app.
- **Local-first & provider-agnostic** — runs on-device against any
  OpenAI-compatible endpoint; split chat/ingestion models via `.env`.

[Unreleased]: https://github.com/Clod/llmwiki-marimo/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Clod/llmwiki-marimo/releases/tag/v0.1.0
