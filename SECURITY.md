# Security Policy

## Security model

LLM Wiki is **local-first** and designed to keep your data on your machine:

- **Your documents never leave your machine** except as text sent to the LLM
  endpoint *you* configure. If you point it at a local model (Ollama, LM Studio),
  nothing leaves the machine at all.
- **Source files are never modified.** The pipeline only reads from
  `WIKI_PATH/sources/`; it writes generated pages to `WIKI_PATH/wiki/` and an
  index to `WIKI_PATH/.llmwiki/`.
- **Secrets live in `.env`**, which is gitignored. API keys are read via
  `pydantic-settings` and are never written into the wiki, the SQLite index, or
  logs. Never commit a real `.env`.
- **The `wiki/` directory is its own git repo** — review its history before
  pushing it anywhere, in case ingested content is sensitive.

## Things to be aware of

- Ingested documents are sent to your configured LLM provider. Choose a provider
  whose data-handling policy matches the sensitivity of your corpus, or use a
  local model.
- A custom `wiki_config.toml` system prompt is passed verbatim to the chat agent.
  Treat it as trusted configuration.
- DOCX ingestion shells out to LibreOffice for conversion. Only ingest documents
  you trust.
- **Prompt injection from ingested documents.** Document text is *untrusted
  input* that reaches the chat agent — and the agent can **write files** to your
  wiki via its save tools (`file_to_wiki` / `save_to_wiki`). A maliciously
  crafted source could, in principle, try to steer the agent into writing or
  overwriting wiki pages with attacker-chosen content. In ordinary personal use
  you ingest your own documents, so this is low risk; but treat any file from an
  untrusted origin (for example a PDF downloaded from the web) the way you'd
  treat untrusted code — review it before ingesting, and review the wiki's git
  history before relying on or sharing pages the agent saved. This version ships
  no automatic defense against injection; the safeguard is that the wiki is
  local, every change is a reviewable git commit, and you choose what to ingest.

## Reporting a vulnerability

Please **do not** open a public issue for security reports. Instead, open a
[private security advisory](https://github.com/Clod/llmwiki-marimo/security/advisories/new)
on GitHub, or contact the maintainer directly. Include reproduction steps and the
affected version/commit. We aim to acknowledge reports within a few days.
