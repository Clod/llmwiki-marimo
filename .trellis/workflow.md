# Development Workflow

> Based on [Effective Harnesses for Long-Running Agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)

---

## Table of Contents

1. [Quick Start (Do This First)](#quick-start-do-this-first)
2. [Workflow Overview](#workflow-overview)
3. [Session Start Process](#session-start-process)
4. [Development Process](#development-process)
5. [Session End](#session-end)
6. [File Descriptions](#file-descriptions)
7. [Best Practices](#best-practices)

---

## Quick Start (Do This First)

### Step 0: Initialize Developer Identity (First Time Only)

> **Multi-developer support**: Each developer/Agent needs to initialize their identity first

```bash
# Check if already initialized
python3 ./.trellis/scripts/get_developer.py

# If not initialized, run:
python3 ./.trellis/scripts/init_developer.py <your-name>
# Example: python3 ./.trellis/scripts/init_developer.py cursor-agent
```

This creates:
- `.trellis/.developer` - Your identity file (gitignored, not committed)
- `.trellis/workspace/<your-name>/` - Your personal workspace directory

**Naming suggestions**:
- Human developers: Use your name, e.g., `john-doe`
- Cursor AI: `cursor-agent` or `cursor-<task>`
- Claude Code: `claude-agent` or `claude-<task>`
- iFlow cli: `iflow-agent` or `iflow-<task>`

### Step 1: Understand Current Context

```bash
# Get full context in one command
python3 ./.trellis/scripts/get_context.py

# Or check manually:
python3 ./.trellis/scripts/get_developer.py      # Your identity
python3 ./.trellis/scripts/task.py list          # Active tasks
git status && git log --oneline -10              # Git state
```

### Step 2: Read Project Guidelines [MANDATORY]

**CRITICAL**: Read guidelines before writing any code:

```bash
# Read frontend guidelines index (if applicable)
cat .trellis/spec/frontend/index.md

# Read backend guidelines index (if applicable)
cat .trellis/spec/backend/index.md
```

**Why read both?**
- Understand the full project architecture
- Know coding standards for the entire codebase
- See how frontend and backend interact
- Learn the overall code quality requirements

### Step 3: Before Coding - Read Specific Guidelines (Required)

Based on your task, read the **detailed** guidelines:

**Frontend Task**:
```bash
cat .trellis/spec/frontend/hook-guidelines.md      # For hooks
cat .trellis/spec/frontend/component-guidelines.md # For components
cat .trellis/spec/frontend/type-safety.md          # For types
```

**Backend Task**:
```bash
cat .trellis/spec/backend/database-guidelines.md   # For DB operations (+ deletion semantics)
cat .trellis/spec/backend/chat-retrieval.md        # For chat retrieval / grounding / citations
cat .trellis/spec/backend/logging-guidelines.md    # For logging
cat .trellis/spec/backend/datasets-format.md       # For dataset sources
```

---

## Workflow Overview

### Core Principles

1. **Read Before Write** - Understand context before starting
2. **Follow Standards** - [!] **MUST read `.trellis/spec/` guidelines before coding**
3. **Incremental Development** - Complete one task at a time
4. **Record Promptly** - Update tracking files immediately after completion
5. **Document Limits** - [!] **Max 2000 lines per journal document**

### File System

```
.trellis/
|-- .developer           # Developer identity (gitignored)
|-- scripts/
|   |-- __init__.py          # Python package init
|   |-- common/              # Shared utilities: paths, developer, git_context,
|   |                        # config, phase, registry, task_queue, worktree, cli_adapter
|   |-- multi_agent/         # Pipeline: plan, start, status, create_pr, cleanup
|   |-- init_developer.py    # Initialize developer identity
|   |-- get_developer.py     # Get current developer name
|   |-- task.py              # Manage tasks
|   |-- get_context.py       # Get session context
|   |-- create_bootstrap.py  # Generate a bootstrap prompt
|   +-- add_session.py       # One-click session recording
|-- workspace/           # Developer workspaces (GITIGNORED in this repo — the
|                        # journal is a local logbook, it never enters a commit/PR)
|   |-- index.md         # Workspace index + Session template
|   +-- {developer}/     # Per-developer directories
|       |-- index.md     # Personal index (with @@@auto markers)
|       +-- journal-N.md # Journal files (sequential numbering)
|-- tasks/               # Task tracking
|   +-- {MM}-{DD}-{name}/
|       +-- task.json
|-- spec/                # [!] MUST READ before coding
|   |-- frontend/        # Frontend guidelines (if applicable)
|   |   |-- index.md               # Start here - guidelines index
|   |   +-- *.md                   # Topic-specific docs
|   |-- backend/         # Backend guidelines (if applicable)
|   |   |-- index.md               # Start here - guidelines index
|   |   +-- *.md                   # Topic-specific docs
|   +-- guides/          # Thinking guides
|       |-- index.md                      # Guides index
|       |-- cross-layer-thinking-guide.md # Pre-implementation checklist
|       +-- *.md                          # Other guides
+-- workflow.md             # This document
```

---

## Session Start Process

### Step 1: Get Session Context

Use the unified context script:

```bash
# Get all context in one command
python3 ./.trellis/scripts/get_context.py

# Or get JSON format
python3 ./.trellis/scripts/get_context.py --json
```

### Step 2: Read Development Guidelines [!] REQUIRED

**[!] CRITICAL: MUST read guidelines before writing any code**

Based on what you'll develop, read the corresponding guidelines:

**Frontend Development** (if applicable):
```bash
# Read index first, then specific docs based on task
cat .trellis/spec/frontend/index.md
```

**Backend Development** (if applicable):
```bash
# Read index first, then specific docs based on task
cat .trellis/spec/backend/index.md
```

**Cross-Layer Features**:
```bash
# For features spanning multiple layers
cat .trellis/spec/guides/cross-layer-thinking-guide.md
```

### Step 3: Select Task to Develop

Use the task management script:

```bash
# List active tasks
python3 ./.trellis/scripts/task.py list

# Create new task (creates directory with task.json)
python3 ./.trellis/scripts/task.py create "<title>" --slug <task-name>
```

---

## Development Process

### Task Development Flow

```
1. Create or select task
   --> python3 ./.trellis/scripts/task.py create "<title>" --slug <name> or list

2. Write code according to guidelines
   --> Read .trellis/spec/ docs relevant to your task
   --> For cross-layer: read .trellis/spec/guides/

3. Self-test
   --> uv run pytest tests/unit tests/regression   # exactly what CI runs
   --> uv run ruff check <changed files>
   --> Manual feature testing (see "Testing layers" below)

4. Commit code — only when the human asks
   --> git add <files>
   --> git commit -m "type(scope): description"
       Format: feat/fix/docs/refactor/test/chore

5. Record session (one command)
   --> python3 ./.trellis/scripts/add_session.py --title "Title" --commit "hash"
```

### Code Quality Checklist

**Must pass before commit**:
- [OK] Lint checks pass (project-specific command)
- [OK] Type checks pass (if applicable)
- [OK] Manual feature testing passes

**Project-specific checks**:
- See `.trellis/spec/frontend/quality-guidelines.md` for frontend (marimo notebooks)
- See `.trellis/spec/backend/quality-guidelines.md` for backend

### Testing layers

Not everything runs in CI — know which gate you are (and are not) passing.

| Layer | Command | In CI? | Needs |
|-------|---------|--------|-------|
| Unit + regression | `uv run pytest tests/unit tests/regression` | **Yes** — the only automated gate | nothing (FakeLLM, no network) |
| Lint | `uv run ruff check <paths>` | Yes | nothing |
| Demo acceptance UAT | `uv run python scripts/uat_finanzas.py --brief` | No | live LLM (`.env`) |
| E2E (marimo apps) | `HEADLESS=1 uv run pytest tests/e2e/<file> -v -s` | No | live LLM + `uv run playwright install chromium` |

**Consequences of that table** (learned the hard way):
- A green CI proves the deterministic core only. Anything touching **retrieval,
  grounding, citations or the marimo UI** must be exercised by the UAT/E2E layers
  before you call it done — the pre-retrieval feature once passed every unit test
  while being dead in the app (see `.trellis/spec/backend/chat-retrieval.md`).
- E2E suites are **stateful and ordered**: the ingest E2E builds the fixture
  workspace the read E2E consumes, so run ingest first. Each E2E owns its own
  workspace dir + port so suites never fight.
- Expensive or destructive E2E cases are **opt-in** via env flags (e.g.
  `E2E_FULL=1` for the LLM lint sweep, `E2E_DESTRUCTIVE=1` for deletion), so the
  default run stays cheap. Check the file's docstring for its flags.
- The wiki UATs (`examples/<demo>/UAT*.md`) are the manual acceptance scripts for
  a demo's behavior; keep them in sync when the behavior they assert changes.

### Keeping docs honest

Documentation rot is not a discipline problem — this project had a "update the
spec docs" checklist item and still shipped a reference to a file that did not
exist, six references to a deleted test, and a §6 that never mentioned a whole
subsystem. Split the problem:

**Derivable facts are never maintained by hand.** They are generated or
enforced:

| Fact | Mechanism |
|------|-----------|
| Relative links in docs | `tests/unit/test_docs_links.py` — fails CI on a dead link |
| Ingestion artifact counts | generated: `scripts/capture_ingestion_walkthrough.py` |
| Test counts / badges | prefer deleting the number over maintaining it |

If you find yourself typing a number into prose that a command could compute,
that is a rot surface — generate it, assert it, or drop it.

**Judgment facts need a trigger, not a reminder.** "Update the docs" is
unactionable; this is what actually enumerates what:

| If you changed… | …these enumerate it |
|---|---|
| a lint check | `docs/manual/workflows.md` §6.1 (table + diagram) + quick-status table, **and the "self-maintaining wiki" Highlights bullet in both READMEs, which counts them** |
| a repair action | §6.2 dispatch table |
| a pipeline step | §6.3 (steps table + sequence diagram), `docs/ingestion_walkthrough.md` |
| a chat tool | §6.7 tool inventory, `.trellis/spec/backend/chat-retrieval.md` |
| a test file's name | both READMEs, `CONTRIBUTING.md`, `spec/backend/quality-guidelines.md`, `spec/backend/directory-structure.md` |
| added a doc | the manual's index, the `docs/` tree in both READMEs |

---

## Session End

### One-Click Session Recording

After code is committed, use:

```bash
python3 ./.trellis/scripts/add_session.py \
  --title "Session Title" \
  --commit "abc1234" \
  --summary "Brief summary"
```

This automatically:
1. Detects current journal file
2. Creates new file if 2000-line limit exceeded
3. Appends session content
4. Updates index.md (sessions count, history table)

### Pre-end Checklist

Use `/trellis:finish-work` command to run through:
1. [OK] All code committed, commit message follows convention
2. [OK] Session recorded via `add_session.py`
3. [OK] No lint/test errors
4. [OK] Working directory clean (or WIP noted)
5. [OK] Spec docs updated if needed

---

## File Descriptions

### 1. workspace/ - Developer Workspaces

**Purpose**: Record each AI Agent session's work content

**Structure** (Multi-developer support):
```
workspace/
|-- index.md              # Main index (Active Developers table)
+-- {developer}/          # Per-developer directory
    |-- index.md          # Personal index (with @@@auto markers)
    +-- journal-N.md      # Journal files (sequential: 1, 2, 3...)
```

**When to update**:
- [OK] End of each session
- [OK] Complete important task
- [OK] Fix important bug

### 2. spec/ - Development Guidelines

**Purpose**: Documented standards for consistent development

**Structure** (Multi-doc format):
```
spec/
|-- frontend/           # Frontend docs (if applicable)
|   |-- index.md        # Start here
|   +-- *.md            # Topic-specific docs
|-- backend/            # Backend docs (if applicable)
|   |-- index.md        # Start here
|   +-- *.md            # Topic-specific docs
+-- guides/             # Thinking guides
    |-- index.md        # Start here
    +-- *.md            # Guide-specific docs
```

**When to update**:
- [OK] New pattern discovered
- [OK] Bug fixed that reveals missing guidance
- [OK] New convention established

### 3. Tasks - Task Tracking

Each task is a directory containing `task.json`:

```
tasks/
|-- 01-21-my-task/
|   +-- task.json
+-- archive/
    +-- 2026-01/
        +-- 01-15-old-task/
            +-- task.json
```

**Commands**:
```bash
python3 ./.trellis/scripts/task.py create "<title>" [--slug <name>]   # Create task directory
python3 ./.trellis/scripts/task.py archive <name>  # Archive to archive/{year-month}/
python3 ./.trellis/scripts/task.py list            # List active tasks
python3 ./.trellis/scripts/task.py list-archive    # List archived tasks
```

---

## Best Practices

### [OK] DO - Should Do

1. **Before session start**:
   - Run `python3 ./.trellis/scripts/get_context.py` for full context
   - [!] **MUST read** relevant `.trellis/spec/` docs

2. **During development**:
   - [!] **Follow** `.trellis/spec/` guidelines
   - For cross-layer features, use `/trellis:check-cross-layer`
   - Develop only one task at a time
   - Run lint and tests frequently

3. **After development complete**:
   - Use `/trellis:finish-work` for completion checklist
   - After fix bug, use `/trellis:break-loop` for deep analysis
   - Commit after the human asks (and after testing passes)
   - Use `/trellis:record-session` (or `add_session.py`) to record progress

### [X] DON'T - Should Not Do

1. [!] **Don't** skip reading `.trellis/spec/` guidelines
2. [!] **Don't** let journal single file exceed 2000 lines
3. **Don't** develop multiple unrelated tasks simultaneously
4. **Don't** commit code with lint/test errors
5. **Don't** forget to update spec docs after learning something
6. [!] **Don't** run `git commit` / `git push` on your own initiative — the AI
   commits **only when the human explicitly asks**, never as a side effect of
   finishing a task (pushing to a branch with an open PR is outward-facing too)

---

## Quick Reference

### Must-read Before Development

| Task Type | Must-read Document |
|-----------|-------------------|
| Frontend work | `frontend/index.md` → relevant docs |
| Backend work | `backend/index.md` → relevant docs |
| Cross-Layer Feature | `guides/cross-layer-thinking-guide.md` |

### Commit Convention

```bash
git commit -m "type(scope): description"
```

**Type**: feat, fix, docs, refactor, test, chore
**Scope**: Module name (e.g., auth, api, ui)

### Common Commands

```bash
# Session management
python3 ./.trellis/scripts/get_context.py    # Get full context
python3 ./.trellis/scripts/add_session.py    # Record session

# Task management
python3 ./.trellis/scripts/task.py list      # List tasks
python3 ./.trellis/scripts/task.py create "<title>" # Create task

# Slash commands
/trellis:start                # Begin a session (context + guidelines)
/trellis:before-backend-dev   # Read backend guidelines before coding
/trellis:check-backend        # Verify backend correctness after coding
/trellis:check-cross-layer    # Cross-layer verification
/trellis:finish-work          # Pre-commit checklist
/trellis:update-spec          # Capture a contract into .trellis/spec/
/trellis:record-session       # Record the session after the human commits
/trellis:break-loop           # Post-debug analysis
```

---

## Summary

Following this workflow ensures:
- [OK] Continuity across multiple sessions
- [OK] Consistent code quality
- [OK] Trackable progress
- [OK] Knowledge accumulation in spec docs
- [OK] Transparent team collaboration

**Core Philosophy**: Read before write, follow standards, record promptly, capture learnings
