# Multilingual Content — Language Contract

> Two distinct language axes govern the app. Confusing them looks like a bug but
> is not. This documents the actual runtime contract.

---

## The two axes

| Axis | Set by | Governs |
|------|--------|---------|
| **Wiki content language** | `[wiki].language` in each wiki's `wiki_config.toml` (`en` / `es`, extensible) | Language of **generated** pages, section headers, catalogue/overview — during **ingestion**. |
| **Chat answer language** | The **user's question**, at runtime | The language the chat agent replies in. |

These are independent. `[wiki].language` does **not** force the chat answer
language.

## Contract

- Ingestion (`base/domain/ingestion/…`) reads `[wiki].language` and writes the
  wiki in that language, regardless of the **source documents'** language (a
  Spanish wiki can be built from English PDFs — output is Spanish).
- Chat (`base/domain/chat/agent.py` `create_agent(..., language=…)`) passes the
  wiki language into the system prompt, but the agent **mirrors the language of
  the incoming question** for its reply. For an `en` wiki the language plumbing
  adds **no** answer-language directive, so the model naturally answers in
  whatever language it was asked.

## Consequence (the "ensalada" that is not a bug)

A **Spanish question against an English (`en`) wiki** yields a **Spanish answer
about English content**, with citations pointing at English pages. This reads as
"English sources + Spanish output" but is **expected behavior**, not a config
mismatch — the content is English; only the reply mirrors the asker's language.

To get an all-Spanish experience (content **and** answers), the wiki itself must
be `[wiki].language = "es"` (see `examples/cuentos-de-hadas/`), not just the
question.

## Verified

Both shipped demos are internally language-consistent: `examples/fairy-tales/`
(all `en`), `examples/cuentos-de-hadas/` (all `es`). E2E ingest+read pass in both
paths.
