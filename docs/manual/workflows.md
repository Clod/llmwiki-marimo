# LLMWiki Workflows (§6)

> Part of the [LLMWiki Programmer Manual](programmer_manual.md) — this file
> is **§6 Workflows**. Section numbers are **global**: a `§N` means the same
> section wherever it is cited. Where each lives:
>
> | Sections | File |
> |---|---|
> | §1 §2 §3 §10 §11 §13 | [`programmer_manual.md`](programmer_manual.md) — orientation, the nine layers, directory map, constraints, glossary |
> | §6 | this file |
> | §4 §5 §14 | [`internals.md`](internals.md) — schema, tool layer, tracing |
> | §7 §8 §9 §15 | [`apps.md`](apps.md) — Marimo apps, configuration, testing, datasets |

## 6. Workflows

> **Looking for the big picture instead?** This section is *reference* — one
> entry per workflow, with contracts. For the narrative version, the
> [Ingestion Walkthrough](../ingestion_walkthrough.md) follows one small corpus
> through its whole lifecycle (first document · second document · no-op
> re-ingest · edited source · deletion), and the
> [Query Walkthrough](../query_walkthrough.md) follows seven questions through
> the routing gate on the read side. Both use real, regenerable numbers and
> link back here for each contract.

Each of the ten files linked from the table below follows the same template:

> **Status · Entry · Steps · LLM prompts (inline) · Triggers · Today vs Target · Verification**

### Quick-status table

| #    | Workflow           | Status | Entry                                                                | Pending                                                |
| ---- | ------------------ | ------ | -------------------------------------------------------------------- | ------------------------------------------------------ |
| 6.1  | [Lint](workflows/6.1-lint.md)               | ✅      | `lint/runner.py:lint_wiki`                                                  | `data_gap` shallow; `gap_filled_check` runs always; `vocabulary`, `thin_page` + `unpaged_source` checks added; auto-tail done for ingest; scan/regenerate pending |
| 6.2  | [Repair](workflows/6.2-repair.md)             | ✅      | `repair/runner.py:repair_wiki`                                                | All eight deterministic repairs implemented             |
| 6.3  | [Single ingest](workflows/6.3-single-document-ingestion.md)      | ✅      | `ingestion/pipeline.py:ingest_file`                                           | Lint+repair tail opt-in today                 |
| 6.4  | [Batch ingest](workflows/6.4-batch-ingestion.md)       | ✅      | `ingestion/batch.py:batch_ingest`                                   | Lint+repair tail opt-in today                 |
| 6.5  | [Scan sources](workflows/6.5-scan-sources.md)       | ✅      | `ingestion/pipeline.py:scan_and_ingest`                                          | Should chain into lint+repair                 |
| 6.6  | [Regenerate](workflows/6.6-regenerate-pages.md)         | ✅      | `ingestion/pipeline.py:regenerate_wiki_pages`                                          | Should chain into lint+repair                  |
| 6.7  | [Chat / RAG](workflows/6.7-chat-rag.md)         | ✅      | `chat/agent.py:create_agent` + `chat/config.py:_DEFAULT_SYSTEM_PROMPT` | Two modes: agent-driven (default) and opt-in pre-retrieval. Phases 1–3 (wiki + sources) complete; web search (Phase 4) is deliberately not built — see the ROADMAP |
| 6.8  | [Chat → Wiki](workflows/6.8-chat-to-wiki.md)        | ✅      | `read_app.py` Save form → `chat/wiki_tools.py:save_to_wiki` (user-driven; agent has no write tool) | Post-save lint+repair + cross-linking ✅; LLM-gated checks & bidirectional links deferred — see the ROADMAP |
| 6.9  | [Source deletion](workflows/6.9-source-deletion.md)    | ✅      | `tools/deletion.py:delete_source`                                               | —                                                      |
| 6.10 | [Wiki page deletion](workflows/6.10-page-deletion.md) | ✅      | `tools/wiki_fs.py:delete_page`                               | —                                                     |

Every ingestion and save workflow shares one goal: **leave the wiki in an
internally consistent state.** The mechanism is the **lint → repair reconciliation
cycle**, documented first (§6.1–§6.2) because §6.3–§6.6 and §6.8 all converge on it.

**Mental model.** Ingestion does the reconciliation *inline* — it creates/updates
the concept and summary pages, rewrites `overview.md`, and updates the citation
graph and `index.md`. **Lint is the verification gate**: if ingestion did its job,
a follow-up lint should report *"no actions needed."* **Repair is the safety net**
for whatever lint still flags. The steady-state success criterion for any ingest
is therefore *"lint comes back clean."*

**Two-column convention.** Each workflow is described as **Today** (what the
code does now) and **Target** (the intended end state, tracked in the [ROADMAP](../../ROADMAP.md)). The status
legend (✅ implemented · 🟡 partial · ❌ missing) still applies per workflow.

**Plan note** (tracked in the [ROADMAP](../../ROADMAP.md)). The app has a wiki-wide "Run Wiki Lint & Repair" button
(`lint_repair_widget_cell` + `lint_repair_runner`), and the ingest runner closes
every ingest with a scoped lint+repair tail (deterministic by default, full-LLM
via the form checkbox). The remaining Target is auto-tails for scan and regenerate,
and separate standalone "Run Lint" / "Run Repair" buttons.

**Entry duality.** Single (§6.3) and batch (§6.4) ingestion can start either from
the GUI (upload widget) **or** by dropping files into `workspace/sources/` and
running Scan sources (§6.5).

### Table-write matrix

What each workflow does to the four DB tables and the wiki filesystem.
**C**reate · **R**ead · **U**pdate · **D**elete · `D+I` = rebuilt (delete-then-insert) · – = untouched.
`chunks_fts` mirrors `document_chunks` via triggers, so it tracks that column.

| Workflow | `documents` | `document_pages` | `document_chunks` | `document_references` | `wiki/` FS |
| --- | --- | --- | --- | --- | --- |
| 6.1 Lint | R | R | R | R | R |
| 6.2 Repair | C/U/D | – | C/U/D | C/U/D | C/U/D |
| 6.3 Single ingest | C/U | D+I | D+I | C/U | C/U |
| 6.4 Batch ingest | C/U | D+I | D+I | C/U | C/U |
| 6.5 Scan sources | C/U | D+I | D+I | C/U | C/U |
| 6.6 Regenerate | U | R | D+I | – | U |
| 6.7 Chat / RAG | R | – | R | R | R |
| 6.8 Chat → Wiki | C/U | – | D+I | C/U | C/U |
| 6.9 Source delete | U/D | D | D | D | D |
| 6.10 Page delete | D | – | D | D | U/D |

6.3 (via the `ingest_app` runner) closes with a 6.1/6.2 reconciliation pass —
deterministic by default, full LLM if the form checkbox is ticked, scoped to the
pages the ingest touched — so the 6.2-row writes can also fire as the tail of an
ingest (without touching unrelated pages). 6.4/6.5 reuse 6.3 per file (6.4 defers
overview/log/commit to once per batch). 6.6 touches **summary pages only** — no
`document_references`, `index.md`, `overview.md`, or lint. 6.7 is fully read-only; the agent has no write tool, so 6.8 (Chat → Wiki) is a separate user-driven save. 6.9/6.10
deletions cascade via `ON DELETE CASCADE` + the `chunks_fts` triggers; 6.10 also
**U**pdates *other* pages when stripping dead links to the deleted page.

The per-workflow diagram at the top of each §6.x file shows the routines
and stores involved; 🧠 marks a step that calls the LLM.

---
