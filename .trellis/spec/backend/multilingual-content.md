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
- **Any code that parses a generated page must derive its structure from the
  locale, never from a literal English string.** A generated page's section
  headers are written from `Locale` (`domain/i18n.py`), so a parser that hardcodes
  one language reads an empty page in every other one — see *Parsing a generated
  page* below.

## Consequence (the "ensalada" that is not a bug)

A **Spanish question against an English (`en`) wiki** yields a **Spanish answer
about English content**, with citations pointing at English pages. This reads as
"English sources + Spanish output" but is **expected behavior**, not a config
mismatch — the content is English; only the reply mirrors the asker's language.

To get an all-Spanish experience (content **and** answers), the wiki itself must
be `[wiki].language = "es"` (see `examples/cuentos-de-hadas/`), not just the
question.

## Parsing a generated page

### 1. Scope / Trigger

Cross-layer contract: ingestion writes a page's section headers from the locale,
and the data layer parses those same headers back into the citation graph. The
two ends must agree for every registered language, not just for `en`.

### 2. Signatures

```python
# domain/i18n.py — the registry both ends read
_LOCALES: dict[str, Locale] = {"en": _EN, "es": _ES}
SUPPORTED_LANGUAGES: tuple[str, ...] = tuple(_LOCALES.keys())
def get_locale(language: str | None) -> Locale          # .h_sources, .h_see_also, …

# domain/tools/references.py — the parser, built from that registry
def _sources_section_pattern() -> re.Pattern[str]       # alternation over every h_sources
_SOURCES_SECTION_RE = _sources_section_pattern()
def update_references(db_path: str, document_id: str, content: str, doc_path: str) -> None
```

### 3. Contracts

- A **summary** page cites with footnotes, `[^1]: file.pdf` — language-independent.
- A **concept** page and a **chat-saved** page cite with plain bullets under the
  localized sources header: `## Sources`, `## Fuentes`, …
- `update_references` rebuilds a page's outgoing edges from scratch on every call
  (`DELETE` then `INSERT`), so a parser that stops matching does not degrade — it
  zeroes the page out.
- Adding a language stays one `Locale` entry: the parser pattern is derived from
  `SUPPORTED_LANGUAGES`, so a new locale's header is accepted with no second edit.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| Header matches any registered `h_sources` | bullets parsed, `cites` edges written |
| Header is a string no locale declares | **no error**, zero `cites` edges for that page |
| Bullet names a wiki page, not a source | skipped (`references.py`, `/wiki/` guard) |
| Page has no sources section at all | zero edges — indistinguishable from the case above |

The second row is the dangerous one: it is silent at every layer. Lint reports
nothing because the pairwise checks join on the edges that were never written.

### 5. Good / Base / Bad Cases

- **Good** — `## Fuentes` on an `es` concept page yields one `cites` edge per bullet.
- **Base** — `## Sources` on an `en` concept page, the case every test covered.
- **Bad** — the parser hardcodes `## Sources`: `examples/cuentos-de-hadas` (13
  concept pages) and `examples/finanzas-argentinas` (29) each measured **0**
  concept `cites` edges, against 14 of 14 in `examples/fairy-tales`.

### 6. Tests Required

- `tests/unit/test_references.py::test_update_references_creates_cites_edge_from_localized_sources_heading`
  — assertion point: a page written with `## Fuentes` produces a row in
  `document_references` with `reference_type='cites'`.
- The English twin, `…_from_plain_bullet`, must stay: the alternation has to keep
  accepting both.
- Assert through the **producer**, not a hand-written fixture, whenever the seam
  is template → parser (see the cross-layer guide, Lesson 1).

### 7. Wrong vs Correct

#### Wrong

```python
# One language's header frozen into the parser. Every other language reads empty.
_SOURCES_SECTION_RE = re.compile(r"^##\s+Sources\s*$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
```

#### Correct

```python
# Derived from the same registry the writer uses, so the two ends cannot drift.
headings = sorted({get_locale(lang).h_sources for lang in SUPPORTED_LANGUAGES}, key=len, reverse=True)
_SOURCES_SECTION_RE = re.compile(
    rf"^##\s+(?:{'|'.join(re.escape(h) for h in headings)})\s*$(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
```

**Recovery for an existing wiki**: re-run `update_references` over each page. It
rebuilds rather than patches and calls no model, so the graph repopulates without
regenerating any prose.

---

## Verified

Both shipped demos are internally language-consistent in their **generated
prose**: `examples/fairy-tales/` (all `en`), `examples/cuentos-de-hadas/` (all
`es`). E2E ingest+read pass in both paths.

> **Warning**: that verification was read-through-the-UI only, and it is what hid
> the defect above for the whole life of the `es` locale. "Language-consistent"
> was checked on what the pages *say*, never on what the database *derived from
> them*. When a language axis lands, verify the derived state too — the citation
> graph, the lint findings, the delete cascade — not only the rendered page.
