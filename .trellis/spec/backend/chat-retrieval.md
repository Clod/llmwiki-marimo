# Chat Retrieval & Grounding Contracts

> Cross-layer contracts for the chat path (retrieval → plan → answer → citation).
> Captured after the vocab/pre-retrieval UAT surfaced three defects that each
> came from violating one of these. Executable: names files, functions, fields.

---

## 1. FTS5 queries must be sanitized before `MATCH`

**Contract.** `domain.tools.search.search_chunks(db_path, query, ...)` passes
`query` **verbatim** to `chunks_fts MATCH ?`. FTS5 reads `,` `?` `¿` `"` and the
bare keywords `AND`/`OR`/`NOT`/`NEAR` as **query syntax**, so a natural-language
question raises `sqlite3.OperationalError` — which `search_chunks` **swallows**
(`except Exception: return []`). The failure is therefore **silent**: the caller
sees "no hits", not an error.

**Any caller that passes user/NL text MUST first turn it into a valid MATCH
expression.** Reference implementation: `domain.chat.preretrieval._fts_query`
— tokenize `\w+`, drop stopwords + ≤2-char tokens, `OR`-join each token quoted
(`"plazo" OR "fijo"`). The two pre-retrieval wrappers (`retrieve_wiki`,
`retrieve_source_chunks`) are its only callers; the model's own `search_chunks`
tool is exempt because the model already emits keyword queries.

| Case | Input | Forwarded to MATCH |
|------|-------|--------------------|
| Good | `_fts_query("¿le gano a la inflación?")` | `"gano" OR "inflación"` → hits |
| Bad  | raw `"¿le gano a la inflación?"` | `OperationalError` → silently `[]` |
| Edge | `_fts_query("¿? , .")` | `""` → `search_chunks` short-circuits to `[]` |

**Regression bug this prevents:** hybrid pre-retrieval was dead for every real
Spanish question (the gate refused covered topics because retrieval always
returned empty). Tests: `tests/unit/test_chat_retrieve.py`.

---

## 2. The citation format is `Referencia:` / `Fuente:` lines, not parentheses

**Contract.** The canonical citation an answer carries is a **trailing line**:
- `Referencia: <wiki page or source file>` — from a curated page or a document
- `Fuente: <external origin>` — for a datum's external origin (e.g. `ambito.com`)

This is specified in every wiki's `wiki_config.toml` system prompt and emitted
deterministically by `domain.chat.postprocess.ensure_citation`. An inline
parenthesized `(wiki/….md)` may *also* appear, but is **not** the guaranteed
form.

**Any code that detects, counts, or extracts citations MUST recognize the
line form** (and source-doc extensions `.docx`/`.pdf`, not only `.md`).
Reference: `domain.eval.graders._CITATION_LINE` / `_line_refs`, and
`domain.chat.trace._CITATION_MARKERS`.

| Case | Answer ends with | `has_citation` / `cited` |
|------|------------------|--------------------------|
| Good | `Referencia: wiki/concepts/caucion-bursatil.md` | True |
| Good | `Fuente: ambito.com` | True |
| Good | `... (wiki/summaries/x.md).` | True |
| Bad  | no reference line, no `(…​.md)` | False |

**Regression bugs this prevents (bit twice):** `trace._looks_cited` matched only
`.md` → a curated answer citing a `.docx` read as uncited/ungrounded;
`graders.has_citation` matched only parenthesized refs → the `uat_finanzas`
concept-citation check false-failed a correctly cited answer. Tests:
`test_chat_trace.py`, `test_eval_graders.py`.

---

## 3. Pre-retrieval plan order — both tiers gated on the roster

**Contract.** `domain.chat.preretrieval.plan_retrieval(question, *, off_limits,
wiki_hits, doc_hits, has_data, in_roster)` decides in this order:

1. `is_off_limits` → **refuse** (blacklist wins over any hit).
2. `wiki_hits and in_roster` → **Tier-1 curated** (inject, no verify).
3. `has_data` → **tools only** (no injected context; `query_dataset` /
   `estimar_alternativas`).
4. `doc_hits and in_roster` → **Tier-2 raw doc** (inject, verify vs source + warn).
5. else → **refuse** without invoking the model.

**Both** tiers are gated on `in_roster` (the coverage *padrón*), because lexical
FTS can match a curated or raw chunk on a shared word for an uncovered topic — so
the padrón, not the search hit, is the coverage authority. `has_data` is checked
**before** Tier-2 so a data question (e.g. "¿a cuánto el billete verde?") reaches
`query_dataset` instead of being answered from raw prose.

**Caller (`pre_retrieval_answer`) computes:**
`has_data = mentions_known_data(q, vocab, aliases) or advisory_intent(q)` — the
second disjunct routes a generic advisory question (an amount + horizon, no named
instrument) to the tools instead of refusing. `in_roster = mentions_known_data(q,
coverage, aliases)` where `coverage = dataset vocab ∪ concept page names`.

Tests: `test_chat_retrieval_plan.py`, `test_chat_pre_retrieval_answer.py`,
`test_chat_scope.py`.
