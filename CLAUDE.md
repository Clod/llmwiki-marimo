# llmwiki-marimo — Project Instructions

## How to answer me

Five rules, in force for every reply in this project.

1. **Answer what was asked, then stop.** Nothing extra: no "I also found", no
   summary of adjacent state, no sections I did not ask for.
2. **A side finding goes to `.trellis/workspace/Clod/backlog.md`, not to the
   reply.** Write it there and say nothing, unless it blocks the task at hand.
3. **No developer jargon.** Say what the thing does, or define the term the
   first time. "Merge" is "incorporate the branch into master"; "rebase" is
   "rewrite the commits on top of another commit". Same in English and Spanish.
4. **No tables unless comparing two or more things. No closing list of pending
   work.**
5. **One question per turn, and stop.** Ask the single most blocking question
   and wait for the answer before doing anything else. Never ask two questions
   in one reply, and never ask one and keep working past it.

The register rules — plain technical language, no idioms or metaphors, every
reference resolving backwards — apply on top of these.

## Skills

This is a Python/Marimo project using the Trellis workflow. Only use skills from the list below. Ignore all other skills (frontend, mobile, other languages, etc.) even if they appear in the system context.
When adding new GUI elements in Marimo, I want you to always take into account that, due to responsiveness, it is always a bad idea to gather many GUI elements in the same cell.

### Active Skills

| Skill | When to use |
|-------|-------------|
| `trellis:start` | Begin a new session |
| `trellis:finish-work` | Pre-commit checklist |
| `trellis:record-session` | Record progress after human commits |
| `trellis:before-backend-dev` | Before writing backend code |
| `trellis:check-backend` | Verify backend correctness |
| `trellis:check-cross-layer` | Cross-layer consistency check |
| `trellis:brainstorm` | Explore approaches |
| `trellis:update-spec` | Capture executable contracts |
| `trellis:parallel` | Run parallel sub-tasks |
| `python-testing` | Python test strategy and pytest patterns |
| `python-patterns` | Idiomatic Python patterns |
| `python-review` | Review Python code |
| `marimo-notebook` | Write or edit marimo notebooks |
| `marimo-batch` | Run marimo notebooks in batch mode |
| `test-ingest` | Run E2E ingestion test |
| `test-all` | Run full test suite |
| `test-read` | Run read-app tests |
| `tdd-workflow` | Test-driven development cycle |
| `code-review` | General code review |
