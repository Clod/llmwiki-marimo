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

## Reporting a vulnerability

Please **do not** open a public issue for security reports. Instead, open a
[private security advisory](https://github.com/Clod/llmwiki-marimo/security/advisories/new)
on GitHub, or contact the maintainer directly. Include reproduction steps and the
affected version/commit. We aim to acknowledge reports within a few days.
