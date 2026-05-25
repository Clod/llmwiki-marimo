# Finish repair actions: `missing_xref`, `contradiction`, `data_gap`

> Roadmap: programmer_manual.md §11.12 (and the `data_gap` half of §11.7).
> This task makes the lint→repair reconciliation cycle (§6.1–§6.2) able to fix
> three issue types that currently return `skipped`/stub.

## Goal

Implement real repair actions for the three lint issues that today do nothing:

| Issue          | Today (`repair/actions.py`)        | After this task                                                            |
| -------------- | ---------------------------------- | -------------------------------------------------------------------------- |
| `missing_xref` | returns `skipped` (L142)           | **Deterministic** — append a `## See also` link from page A to page B      |
| `contradiction`| returns `skipped` (L237)           | **Deterministic** — append a `> ⚠️` warning callout flagging it for a human |
| `data_gap`     | stub `skipped` (L254)              | **Deterministic** — insert a marked TODO note into the most-related page    |
| `gap_filled` (NEW) | — (does not exist)             | **Deterministic** — when a TODO topic is later covered by a source, replace the note with a link |

**Scope decision (locked):** repairs are *safe / advisory*, never ambitious.
We do **not** auto-resolve contradictions, do **not** LLM-rewrite authored prose
inline, and do **not** fetch external material. The `data_gap` lifecycle is the
"Option B" design: the TODO note lives **inside the most-related existing page**
(not in a new stub page — that would be auto-deleted by the orphan check).

**Confirmed simplicity decisions (do not elaborate beyond these):**
- *Filled-gap replacement is link-only* — `> ℹ️ See [Title](rel).` No extracted
  blurb, no synthesized prose, no LLM call (§4 below).
- *`data_gap` host selection is deterministic* — pick the host page via an FTS
  search of the topic against the wiki; do **not** ask the LLM to choose a host
  (§3 below).

## Out of scope (do NOT implement here)

- Web search / fetching new sources (programmer_manual §11.5).
- Wiring lint+repair to run automatically after ingest, or UI buttons (§11.11).
- Bidirectional cross-links (only A→B is needed to clear the lint).
- Any change to the orphan / stale / missing_concept repairs (already done).

---

## Background — how the pieces connect (read first)

- **Lint check** (`api_new/domain/lint/checks.py`) detects an issue → emits a
  `LintIssue`. **Repair action** (`api_new/domain/repair/actions.py`) consumes a
  `LintIssue` → returns a `RepairResult`.
- **Dispatch** (`repair/runner.py:20` `_DISPATCH`) maps `issue.check` → handler.
  Handlers in the set `_NEEDS_LLM` (`runner.py:19`) are called as
  `handler(issue, db_path, workspace, llm_client, model)`; all others as
  `handler(issue, db_path, workspace)`. **All four actions in this task are
  deterministic → keep them OUT of `_NEEDS_LLM`** (3-arg call).
- **`update_references(db_path, doc_id, content, doc_path)`**
  (`tools/references.py:63`) re-parses a page's markdown and rebuilds its
  `document_references` edges. A markdown link `[text](href)` to another wiki
  page becomes a `links_to` edge (`references.py:108-116`). **This is what
  clears a `missing_xref`.** `append_to_page` does NOT call it — repair must call
  it explicitly after editing a page.
- **Link resolution** (`references.py:_parse_wiki_links`, L31): an href is
  resolved relative to the page's own directory. From `/wiki/concepts/a.md`,
  `inflation.md` → `concepts/inflation.md`; `../summaries/x.md` → `summaries/x.md`;
  `/wiki/concepts/x.md` → `concepts/x.md`. **Use an on-disk-correct relative path**
  so the link both renders and produces the edge.
- **`search_chunks(db_path, query, limit, scope)`** (`tools/search.py:6`) →
  `list[dict]` with keys `content, page, filename, title, path, file_type,
  header_breadcrumb, chunk_index, score`. `scope ∈ {"all","wiki","sources"}`.
  **Note (`programmer_manual` §10):** the FTS5 tokenizer splits on hyphens and
  treats some punctuation as operators → always pass plain space-separated words
  (use `_fts_safe`, below). Empty/invalid query → returns `[]`.
- **`append_to_page(db_path, workspace, dir_path, slug, content)`**
  (`wiki_fs.py:181`) appends to disk + DB + re-chunks; returns `bool`. Does not
  touch references.
- **`create_page(..., overwrite=True)`** (`wiki_fs.py:98`) rewrites a page
  (needed for the gap_filled in-place replacement). Requires `title` and
  `tags: list[str]`.
- **`make_wiki_slug(name)`** (`ingestion/wiki_generator.py`) — canonical slug
  (NFKD-normalised). Import **inside the function** to avoid a load-time import
  cycle (same pattern as `wiki_fs._insert_chunks`).

---

## Worked example (anchor for the whole design)

1. Wiki has `concepts/interest-rates.md` (cites `fed-paper.pdf`) and
   `concepts/federal-reserve.md` (cites `fed-paper.pdf`). They don't link to each
   other → `missing_xref` fires → repair appends a `## See also` link in
   `interest-rates.md` → next lint is clean.
2. `data_gap` (LLM) reports `GAP: Inflation — interest rates depend on it`. The
   most-related existing page is `concepts/interest-rates.md` → repair inserts a
   marked TODO note **into that page**.
3. Later the user drops `inflation.pdf` into `sources/` and ingests it. A
   summary/concept page about inflation now exists and a source search for
   "inflation" returns hits.
4. Next lint run: `gap_filled` sees the `<!-- DATA_GAP: inflation -->` marker in
   `interest-rates.md`, confirms "inflation" is now covered → repair replaces the
   note with a one-line link to the new page (creating a `links_to` edge).

---

## Data-model change — `LintIssue`

File: `api_new/domain/lint/report.py`. Add two **optional** fields (defaults keep
all existing call sites valid):

```python
@dataclass
class LintIssue:
    check: str
    severity: str
    page: str
    description: str
    suggestion: str
    related_page: str = ""   # NEW: the "other" page (path_b) for xref/contradiction
    topic: str = ""          # NEW: gap topic slug for data_gap / gap_filled
```

---

## New shared module — `api_new/domain/lint/markers.py`

The DATA_GAP note format is **written by repair** and **read by lint**
(`gap_filled_check`) and by `repair_gap_filled`. To avoid a `lint → repair`
import, define the shared format in `lint/markers.py`; both `lint.checks` and
`repair.actions` import from here.

```python
"""Shared markers for the data_gap TODO lifecycle and FTS-safe queries."""
import re

# The TODO note repair inserts. {slug}=topic slug, {title}=human title,
# {suggestion}=lint suggestion text. EXACTLY a marker line + one blockquote line.
DATA_GAP_NOTE = (
    "<!-- DATA_GAP: {slug} -->\n"
    "> 🚧 **Missing topic: {title}.** {suggestion} "
    "Drop a source about this into `sources/` and re-ingest to fill it in."
)

# Matches a DATA_GAP note block: the marker line + its single following
# blockquote line. Group 1 = topic slug. Used to detect AND to replace.
DATA_GAP_BLOCK_RE = re.compile(
    r"<!-- DATA_GAP: (?P<slug>[a-z0-9-]+) -->\n> .*(?:\n|$)"
)

# Contradiction marker — keyed on the related page path so re-runs are idempotent.
def contradiction_marker(related_page: str) -> str:
    return f"<!-- CONTRADICTION: {related_page} -->"

def fts_safe(text: str) -> str:
    """Reduce a phrase to plain space-separated alphanumeric words for FTS5."""
    return re.sub(r"[^0-9a-zA-Z]+", " ", text).strip()
```

---

## 1. `repair_missing_xref` (deterministic)

### Lint side — `checks.py:missing_xref_check`
Currently emits `page=path_a` only. **Add `related_page=pair["path_b"]`** to the
`LintIssue(...)` at `checks.py:110`. (Description/suggestion unchanged.)

### Repair side — `repair/actions.py:repair_missing_xref(issue, db_path, workspace)`
Replace the skip body with:

1. `a_path = issue.page`, `b_path = issue.related_page`. If `b_path` is empty →
   `RepairResult(action="skipped", success=True, message="missing related_page")`.
2. Read page A: parse `(dir_a, slug_a)` with the existing `_parse_page_path`.
   `content_a = read_page(db_path, workspace, dir_a, slug_a)`. If `None` →
   `failed`.
3. Compute the link: `rel = _relative_link(a_path, b_path)` (helper below).
   Derive B's display title: query `documents.title` for `relative_path =
   b_path.lstrip("/")`; fall back to `slug_b.replace("-"," ").title()`.
4. **Idempotency:** if `content_a` already contains `](` + the resolved target
   (or already contains a `## See also` line linking to B) → `skipped`
   ("already linked"). (The lint check itself won't re-fire once the edge
   exists, but guard anyway.)
5. Append a See-also section:
   ```python
   block = f"\n## See also\n\n- [{title_b}]({rel})\n"
   append_to_page(db_path, workspace, dir_a, slug_a, block.strip())
   ```
   If a `## See also` section already exists, append the bullet under it instead
   of adding a second heading (simple approach: if `"## See also"` in content_a,
   append just the `- [..](..)` bullet; else append the whole block).
6. Re-read A's full content + id, then
   `update_references(db_path, id_a, full_content_a, dir_a)` so the `links_to`
   edge is recorded.
7. Return `RepairResult(check="missing_xref", page=a_path,
   action="xref_added", success=True, message=f"Linked → {b_path}")`.

### Helper (add near top of `actions.py`)
```python
import os
def _relative_link(from_page: str, to_page: str) -> str:
    """On-disk relative href from one /wiki/.../x.md page to another."""
    return os.path.relpath(to_page, start=os.path.dirname(from_page))
```
(`_relative_link("/wiki/concepts/a.md","/wiki/concepts/b.md") == "b.md"`;
`_relative_link("/wiki/summaries/a.md","/wiki/concepts/b.md") == "../concepts/b.md"`.)

---

## 2. `repair_contradiction` (deterministic, annotate-only)

### Lint side — `checks.py:contradiction_check`
At `checks.py:218` add `related_page=pair["path_b"]` to the `LintIssue(...)`.
Keep `description` as the human detail (it already includes the detail text).

### Repair side — `repair/actions.py:repair_contradiction(issue, db_path, workspace, llm_client=None, model="")`
(Keep the optional llm args for signature uniformity; do **not** use them. Leave
`contradiction` OUT of `_NEEDS_LLM`.)

1. `a_path = issue.page`, `b_path = issue.related_page`. Parse `(dir_a, slug_a)`.
   Read A; if missing → `failed`.
2. **Idempotency:** `marker = contradiction_marker(b_path)`. If `marker` already
   in A's content → `skipped` ("already flagged").
3. Build a callout and append it:
   ```python
   rel = _relative_link(a_path, b_path)
   detail = issue.description
   note = (
       f"{marker}\n"
       f"> ⚠️ **Possible contradiction** with [{b_path}]({rel}): {detail}\n"
       f"> Review both pages and resolve the conflicting claims."
   )
   append_to_page(db_path, workspace, dir_a, slug_a, note)
   ```
4. Re-read A + id and `update_references(...)` (the link to B becomes an edge —
   acceptable and harmless).
5. Return `action="contradiction_flagged", success=True`.

> Rationale: resolving the conflict needs a human; repair only surfaces it inline,
> idempotently. This is intentional (programmer_manual §6.2 "safety net").

---

## 3. `repair_data_gap` (deterministic — insert TODO note into related page)

### Lint side — `checks.py:data_gap_check`
The LLM step is unchanged (it lists gaps). For **each** parsed gap, choose the
host page deterministically and emit a richer issue:

```python
from domain.lint.markers import fts_safe
from domain.ingestion.wiki_generator import make_wiki_slug   # deferred if needed
...
topic = topic.strip()
slug = make_wiki_slug(topic)
hits = search_chunks(db_path, fts_safe(topic), limit=1, scope="wiki")
if not hits:
    continue   # no related page to host a note → skip this gap
host_path = hits[0]["path"] + hits[0]["filename"]   # e.g. "/wiki/concepts/interest-rates.md"
issues.append(LintIssue(
    check="data_gap", severity="info", page=host_path,
    description=f"Missing or underdeveloped topic: {topic}",
    suggestion=suggestion.strip() or "Consider adding this concept page",
    topic=slug,
))
```
(Replace the current `page="/wiki/concepts/"` issue at `checks.py:276`.)

### Repair side — `repair/actions.py:repair_data_gap(issue, db_path, workspace, llm_client=None, model="")`
Deterministic; leave OUT of `_NEEDS_LLM`.

1. `slug = issue.topic`; if empty → `skipped`.
2. **Already covered?** If `search_chunks(db_path, fts_safe(slug.replace("-"," ")),
   limit=1, scope="sources")` is non-empty → `skipped` ("topic already covered";
   gap_filled will handle any stale note). This prevents inserting a note for a
   topic that is in fact present.
3. Parse host `(dir_h, slug_h)` from `issue.page`; read host content. If missing
   → `failed`.
4. **Idempotency:** if `f"<!-- DATA_GAP: {slug} -->"` already in host content →
   `skipped`.
5. Build note with `DATA_GAP_NOTE.format(slug=slug,
   title=slug.replace("-"," ").title(), suggestion=issue.suggestion)` and
   `append_to_page(db_path, workspace, dir_h, slug_h, note)`.
   (No `update_references` needed — the note contains no links yet.)
6. Return `action="gap_noted", success=True, message=f"TODO note for '{slug}' in {issue.page}"`.

---

## 4. `gap_filled_check` (NEW lint check) + `repair_gap_filled` (NEW repair)

### Lint side — NEW `checks.py:gap_filled_check(db_path)`
Deterministic; runs every lint pass.

```python
from domain.lint.markers import DATA_GAP_BLOCK_RE, fts_safe

def gap_filled_check(db_path: str) -> list[LintIssue]:
    """Find DATA_GAP TODO markers whose topic is now covered by a source."""
    issues = []
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT path, filename, content FROM documents "
            "WHERE source_kind='wiki' AND status!='failed' AND content IS NOT NULL"
        ).fetchall()
    for row in rows:
        for m in DATA_GAP_BLOCK_RE.finditer(row["content"] or ""):
            slug = m.group("slug")
            covered = search_chunks(db_path, fts_safe(slug.replace("-", " ")),
                                    limit=1, scope="sources")
            if covered:
                issues.append(LintIssue(
                    check="gap_filled", severity="info",
                    page=row["path"] + row["filename"],
                    description=f"TODO topic '{slug}' is now covered by a source",
                    suggestion="Replace the TODO note with a link to the new page",
                    topic=slug,
                ))
    return issues
```

### Repair side — NEW `repair/actions.py:repair_gap_filled(issue, db_path, workspace)`
Deterministic.

1. `slug = issue.topic`; parse host `(dir_h, slug_h)` from `issue.page`. Read host
   content; if missing → `failed`.
2. Find the fill-target wiki page:
   `hits = search_chunks(db_path, fts_safe(slug.replace("-"," ")), limit=5, scope="wiki")`.
   Pick the first hit whose `path+filename != issue.page`, preferring `path`
   starting `/wiki/concepts/` over `/wiki/summaries/`. If none → `skipped`
   ("covered by source but no wiki page yet"). Let `target_path =
   hit["path"]+hit["filename"]`, `target_title = hit["title"] or slug.title()`.
3. Build replacement line:
   ```python
   rel = _relative_link(issue.page, target_path)
   replacement = f"> ℹ️ See [{target_title}]({rel}).\n"
   ```
4. Replace the note block in host content:
   `new_content = DATA_GAP_BLOCK_RE.sub(lambda mm: replacement if mm.group("slug")==slug else mm.group(0), host_content)`.
   If `new_content == host_content` → `skipped` (marker not found).
5. Rewrite the host page (need title + tags from DB):
   ```python
   with get_connection(db_path) as conn:
       r = conn.execute("SELECT id, title, tags FROM documents WHERE path||filename=?",
                        (issue.page,)).fetchone()
   tags = json.loads(r["tags"]) if r and r["tags"] else []
   res = create_page(db_path, workspace, dir_h, slug_h, r["title"], new_content, tags, overwrite=True)
   update_references(db_path, res["id"], new_content, dir_h)
   ```
6. Return `action="gap_resolved", success=True, message=f"Linked '{slug}' → {target_path}"`.

---

## 5. Wiring

### `lint/runner.py`
Import `gap_filled_check` and add **after** `missing_concept_check`
(deterministic, not gated on `client`):
```python
issues.extend(gap_filled_check(db_path))
```

### `repair/runner.py`
Import `repair_gap_filled` and add to `_DISPATCH`:
```python
"gap_filled": repair_gap_filled,
```
Do **not** add any of the four to `_NEEDS_LLM` (all deterministic; the LLM work
lives entirely in the `data_gap`/`contradiction` *checks*, not the repairs).

---

## 6. Tests (TDD — write first, then implement)

Use existing fixtures: `tmp_workspace` (`tests/helpers/workspace.py` →
`.workspace`, `.db_path`, `.llm`) and `FakeLLMClient`
(`tests/helpers/fake_llm.py`, set `.responses=[...]`). Mirror the style of the
existing `tests/unit/test_lint_*.py` / `test_repair_*.py`.

New file `tests/unit/test_repair_finish.py` (or extend existing), covering:

1. **missing_xref:** create two concept pages that both cite the same source
   (so `missing_xref_check` fires); run `lint_wiki` → `repair_wiki`; assert page A
   now contains `## See also` + a link to B; assert a `links_to` edge exists
   (`get_forward_refs`); assert a second `lint_wiki` no longer reports the pair
   (idempotent).
2. **contradiction:** build a `LintIssue(check="contradiction", page=A,
   related_page=B, description="...")` and call `repair_contradiction` directly;
   assert the `<!-- CONTRADICTION: B -->` marker + `⚠️` callout were appended;
   call again → `action="skipped"` (idempotent).
3. **data_gap note:** `FakeLLMClient` returns `"GAP: Inflation — needed by rates"`;
   pre-create a related page (`concepts/interest-rates.md`) whose chunks match
   "inflation"/"rates" so the host search resolves; run `lint_wiki(client=...)` →
   `repair_wiki`; assert the host page gained `<!-- DATA_GAP: inflation -->`.
   Separately assert a gap whose topic is already covered by a source →
   `repair_data_gap` returns `skipped`.
4. **gap_filled:** start from a page containing `<!-- DATA_GAP: inflation -->`,
   plus a source whose chunks match "inflation" AND a wiki page about inflation;
   run `lint_wiki` → `repair_wiki`; assert the marker block is gone, replaced by a
   `See [..](..)` link; assert the `links_to` edge to the inflation page exists.
5. **Update existing tests:** any test asserting `repair_missing_xref`,
   `repair_contradiction`, or `repair_data_gap` returns `action="skipped"` for a
   valid issue must be updated to the new behavior. Grep:
   `grep -rn "skipped" tests/unit/test_repair*.py`.

---

## 7. Acceptance criteria

- [ ] `repair_missing_xref` adds a working See-also link and the pair stops being
      flagged on the next lint (proves the `links_to` edge formed).
- [ ] `repair_contradiction` appends an idempotent `⚠️` callout (no duplicates on
      re-run).
- [ ] `repair_data_gap` inserts an idempotent `<!-- DATA_GAP: slug -->` TODO note
      into the most-related existing page, and skips topics already covered.
- [ ] `gap_filled_check` + `repair_gap_filled` replace a TODO note with a link
      once the topic is covered by a source.
- [ ] `LintIssue` gains `related_page` and `topic` (optional, defaulted).
- [ ] `lint/runner.py` runs `gap_filled_check`; `repair/runner.py` dispatches
      `gap_filled`; none of the four are in `_NEEDS_LLM`.
- [ ] New + updated unit tests pass: `uv run pytest tests/unit/ -v`.
- [ ] Lint clean: `uv run ruff check api_new tests`.
- [ ] Update programmer_manual.md §6.2 status table (the three ⏭/🟡 rows → ✅) and
      the §6.2 "Today/Target" note after implementation.

## 8. Files to touch

| File | Change |
| ---- | ------ |
| `api_new/domain/lint/report.py` | add `related_page`, `topic` to `LintIssue` |
| `api_new/domain/lint/markers.py` | **NEW** — `DATA_GAP_NOTE`, `DATA_GAP_BLOCK_RE`, `contradiction_marker`, `fts_safe` |
| `api_new/domain/lint/checks.py` | set `related_page` in xref+contradiction; rewrite `data_gap_check` host selection; add `gap_filled_check` |
| `api_new/domain/lint/runner.py` | call `gap_filled_check` |
| `api_new/domain/repair/actions.py` | implement xref/contradiction/data_gap; add `repair_gap_filled`; add `_relative_link` |
| `api_new/domain/repair/runner.py` | add `gap_filled` to `_DISPATCH` |
| `tests/unit/test_repair_finish.py` | **NEW** tests; update any stale `skipped` assertions |
| `docs/programmer_manual.md` | update §6.2 statuses post-implementation |
