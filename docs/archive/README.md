# Archived Documentation

These documents predate the current canonical reference at
[`../programmer_manual.md`](../programmer_manual.md). They are kept for
historical traceability — to see how the design evolved and why certain
decisions were made — but they should **not** be consulted for current
behaviour. The programmer manual is the only living source of truth.

| File | What it was | Why archived |
|---|---|---|
| [`programmatic_dev_plan.md`](programmatic_dev_plan.md) | High-level multi-step plan to move wiki maintenance from interactive MCP into a programmatic pipeline. Defined the schema (sources / wiki / summaries / concepts) and the eight implementation steps. | Plan is fully executed; its architecture is now §1–§6 of the manual. |
| [`implementation_plan.md`](implementation_plan.md) | Phase-by-phase build tracker (Phase 0 → Phase 6) with per-task status and test counts. | All phases completed; the tracker is historical. The manual replaces it with current workflow status (§6) and pending work (§11). |
| [`ingestion_design.md`](ingestion_design.md) | Original design for the `*_new` sibling-prototype layout (`api_new/`, `shared_new/`, `marimo_new/`) and the per-step pipeline (7a/7b/7c). | Prototype merged in; the pipeline diagrams are now part of §6.1–§6.4 of the manual. |
| [`diagnostic_alignment.md`](diagnostic_alignment.md) | Validation of the project's alignment with the Karpathy LLM-Wiki pattern, with concrete suggestions for programmatic logging, overview maintenance, concept extraction, and lint. | Suggestions implemented; conceptual alignment is now §1 of the manual. |
| [`llmwiki_architecture_rag_roadmap.md`](llmwiki_architecture_rag_roadmap.md) | Architecture write-up with a long Q&A transcript covering ingestion, retrieval, agentic RAG vs brute force, and a detailed SQLite schema breakdown. | Architecture, DB schema, and RAG routing all captured in §2, §4, §6.7 of the manual. |

If you need to revive any of these, check the file's git history first —
some details may have already been folded into the manual or refined.

The foundational pattern reference [`../../Karpathy_concepts.md`](../../Karpathy_concepts.md)
stays at the repo root: it is not LLMWiki documentation but the broader idea
the project implements.
