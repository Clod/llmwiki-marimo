# Design: Wiki rollback (git markdown + DB snapshot cache + reindex floor)

> Status: **proposed** · Branch: `feat/wiki-rollback` · Author: Clod
> Companion to the per-wiki git auto-commit already in `base/domain/tools/git_ops.py`.

## Problem

Each wiki workspace is its own git repo, but `auto_commit` stages **`wiki/` +
`.gitignore` only**, and the wiki's `.gitignore` excludes `.llmwiki/`. So git
versions exactly one thing: the **LLM-generated markdown**. The SQLite index
(`.llmwiki/index.db`) — `documents`, `document_pages`, `document_chunks`,
`chunks_fts`, `document_references` — is **never committed**.

Consequence: a plain `git checkout <old>` restores the markdown but leaves the
DB at the newer state. The viewer (`read_wiki_page`, reads files) looks rolled
back, but **search, citations, and chat** (which read the DB) still reflect the
post-rollback state. Split-brain.

A correct rollback must restore **both** layers to the same point.

## Why the DB doesn't need to live in git

The markdown is the model's **non-deterministic, irreproducible** output — that
is what git must preserve. The DB is **derived state**: a function of
`(sources/ on disk, wiki/*.md)`, all of it rebuildable **without the LLM**:

| DB content | Rebuilt from | LLM? |
|---|---|---|
| `documents` (kind=`source`), `document_pages`, `document_chunks`, `chunks_fts` | `sources/` via `extractor` + `chunker` | no |
| `documents` (kind=`wiki`) | the `wiki/*.md` files themselves | no |
| `document_references` (citation graph) | parse page content via `references.update_references` | no |

So: **git holds the irreproducible truth; the DB is a rebuildable cache.**

## Why we still want a binary snapshot (not just reindex)

A reindex is **semantically equivalent** but **not bit-identical**: `documents.id`
is `randomblob(16)` per row, so a rebuild mints fresh IDs (cascading to every
FK), and `created_at`/`updated_at`/`version`/`document_number` reset. Search,
chat, and citations work identically; the *exact* prior DB (IDs, timestamps,
ingest ordering, edit-count history) is gone.

Hence a **three-tier** model, each with one job:

| Tier | Role | Cost | Fidelity |
|---|---|---|---|
| **git** (`wiki/*.md`) | source of truth for the generated pages | tiny, diffable | exact |
| **DB snapshot cache** (`.llmwiki/snapshots/<sha>.db`) | *speed + fidelity* for recent rollbacks | k × DB size, local, gitignored | byte-exact |
| **reindex** | *correctness floor* — always available | slow | semantic (fresh IDs) |

Rollback prefers the snapshot when present and falls back to reindex.

## Goals / non-goals

**Goals**
- Restore a wiki to a prior committed state with a **consistent** markdown+DB.
- Make the common case ("undo my last ingest") near-instant via the snapshot.
- Guarantee correctness even with no snapshot, via a deterministic reindex.
- Keep git lightweight (no binaries in history) and the cache bounded + local.

**Non-goals (this iteration)**
- Reverting `sources/` (untracked, immutable inputs — see Edge cases).
- Rewriting git history (we restore forward with a new commit).
- Remote sync / collaboration (wikis are intentionally remote-less).
- Re-running the LLM (`regenerate_wiki_pages` is a *different*, paid operation).

## On-disk layout

```
WIKI_PATH/
├── wiki/                     # git-tracked (the only versioned layer)
├── sources/                  # untracked, immutable
└── .llmwiki/                 # gitignored
    ├── index.db              # the live working DB
    └── snapshots/
        ├── <commit-sha>.db   # byte-exact DB paired with that commit
        └── manifest.json     # [{sha, created_at, size_bytes}], newest-first
```

## Components & contracts

New package `base/domain/rollback/` (mirrors `lint/`, `repair/`).

### `snapshots.py` — the binary cache

```python
DEFAULT_KEEP = 5                       # env override: WIKI_SNAPSHOT_KEEP (0 disables)

@dataclass(frozen=True)
class Snapshot:
    commit_sha: str
    db_path: Path
    created_at: str        # ISO-8601
    size_bytes: int

def capture(workspace: Path, commit_sha: str) -> Snapshot | None:
    """Write a consistent copy of index.db to snapshots/<sha>.db.

    Uses the sqlite3 **online backup API**, NOT a filesystem copy: the DB runs in
    WAL mode (PRAGMA journal_mode=WAL), so a raw `cp` of index.db can miss
    uncheckpointed frames. Returns None if the DB is absent or snapshots disabled.
    """

def restore(workspace: Path, commit_sha: str) -> bool:
    """Atomically replace index.db with snapshots/<sha>.db (temp file + os.replace).
    Returns False if that snapshot is absent (caller then reindexes)."""

def list_snapshots(workspace: Path) -> list[Snapshot]:  # newest first
def prune(workspace: Path, keep: int = DEFAULT_KEEP) -> int:  # returns # removed
```

### `reindex.py` — the deterministic floor

```python
@dataclass(frozen=True)
class ReindexReport:
    sources_indexed: int
    wiki_pages_indexed: int
    edges_rebuilt: int
    duration_s: float

def reindex_workspace(workspace: Path) -> ReindexReport:
    """Rebuild .llmwiki/index.db from disk, no LLM:
      1. fresh DB (apply base schema, seed workspace row)
      2. for each file in sources/: extract + chunk -> documents(kind=source),
         document_pages, document_chunks, chunks_fts  (reuses ingestion.extractor,
         ingestion.chunker — the deterministic half of the pipeline)
      3. for each wiki/*.md: register documents(kind=wiki) with content + content_hash
      4. references.update_references(...) per wiki page -> document_references
    Standalone value: also repairs a missing/corrupt index even without a rollback."""
```

### `revert.py` — the orchestrator

```python
@dataclass(frozen=True)
class RevertResult:
    target_sha: str
    db_via: Literal["snapshot", "reindex"]
    restored_pages: int

def revert_wiki(workspace: Path, target: str, *, force: bool = False) -> RevertResult:
    """1. Resolve `target` (a sha or rev like 'HEAD~1') via `git rev-parse`.
       2. Refuse if there are uncommitted wiki/ changes (unless force) — don't eat work.
       3. Restore files forward (preserve history, no rewrite):
            git checkout <target> -- wiki .gitignore
            git commit -m "revert: restore wiki to <short-sha>"
       4. DB: snapshots.restore(workspace, target) if present, else reindex_workspace().
       5. capture() a snapshot for the new restore-commit; prune()."""
```

### `git_ops.py` integration (minimal)

After a successful `auto_commit` (returncode 0), capture + prune behind the same
`autocommit_enabled()` gate and a `snapshots_enabled()` check:

```python
sha = head_sha(workspace)              # new helper: git rev-parse HEAD
if sha and snapshots_enabled():
    snapshots.capture(workspace, sha)
    snapshots.prune(workspace)
```

The DB is already consistent with the just-written markdown at commit time, so
the snapshot pairs cleanly with the commit. **This pairing is the one invariant
that must hold** — every mutating commit (ingest, repair, chat→save) gets a
paired snapshot, or rollback to it falls back to reindex.

## The revert flow

```
revert_wiki(ws, "HEAD~1")
  ├─ rev-parse        -> target sha
  ├─ clean check      -> refuse if dirty (unless force)
  ├─ git checkout target -- wiki .gitignore ; git commit   (markdown restored)
  └─ DB:
       snapshots.restore(ws, target) ? ──yes──> byte-exact DB, instant
                                       └─no───> reindex_workspace(ws)  (equivalent)
  └─ capture(new restore sha) ; prune
```

## Edge cases & decisions

- **Sources added after the target.** `sources/` is untracked, so checkout won't
  remove a PDF ingested after the rollback point; a reindex would re-include it
  (a source with no concept pages → a lint `data_gap`). **Decision:** revert
  reverts the *generated* layer only; the user manages `sources/`. *Future:*
  store a per-commit source manifest to offer "also remove sources added since".
- **Uncommitted wiki edits.** Refuse without `force` (a chat→save not yet
  committed would be lost).
- **`WIKI_AUTOCOMMIT=0` or git missing.** No snapshots, no git → `revert_wiki`
  unavailable; `reindex_workspace` still works as a manual "rebuild the index".
- **Missing/corrupt snapshot.** `restore` returns False → auto-fallback to reindex.
- **WAL.** Capture via the sqlite backup API (above); restore via atomic
  `os.replace`, after closing any open connection to `index.db`.

## Config

| Env | Default | Meaning |
|---|---|---|
| `WIKI_AUTOCOMMIT` | `1` | existing — gates git + (transitively) snapshots |
| `WIKI_SNAPSHOT_KEEP` | `5` | snapshots retained; `0` disables the cache (reindex-only) |

## Testing strategy (no live LLM — all deterministic)

- **reindex equivalence:** ingest a fixture → record query results (page list,
  `search_wiki_fts` hits for a term, `document_references` edges) → drop the DB →
  `reindex_workspace` → assert the **same query results** (compare results, not IDs).
- **snapshot round-trip:** ingest A (capture), ingest B (capture), `revert_wiki`→A
  → assert `wiki/` matches A's tree and DB query results match A; assert the
  snapshot path was used (`db_via == "snapshot"`).
- **WAL consistency:** capture with uncheckpointed WAL frames → restored DB
  reflects the committed state.
- **retention:** capture N+2 → `prune(keep=N)` leaves the newest N.
- **fallback:** delete the target snapshot → `revert_wiki` uses reindex
  (`db_via == "reindex"`), equivalent result.
- **guards:** dirty tree refused without `force`; `WIKI_AUTOCOMMIT=0` makes
  capture/revert no-ops while reindex still succeeds.

## Phasing (each independently shippable)

1. **`reindex_workspace`** — the floor. Useful on its own ("rebuild a broken
   index") and unblocks everything else. + `scripts/wiki_reindex.py`.
2. **Snapshot capture/prune** wired into `auto_commit` (the speed cache).
3. **`revert_wiki`** orchestrator + `scripts/wiki_revert.py --to <sha|HEAD~1>`.
4. **read_app UI** — per-page history + a "revert to this snapshot" action
   (the data — git log + snapshots — is all local already).

## Open questions

- Are any `documents` columns for wiki pages **not** reconstructable from the
  markdown (e.g. `document_number` ordering, `date` used by the timeline)? If so,
  those are precisely the fields where snapshot-fidelity beats reindex — confirm
  during Phase 1.
- Snapshot size on large wikis: is a 5-deep ring of binary DBs acceptable, or
  should deep history compress / drop to SQL dumps? (Local + gitignored, so
  bounded, but worth measuring.)
- Should `revert_wiki` target git commits, or expose friendlier labels (each
  ingest already commits with a message)?
```
