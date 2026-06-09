<!-- Generated: 2026-06-09 | Files scanned: 5 | Token estimate: ~600 -->

# Frontend (marimo)

UI is **marimo notebooks** (reactive Python cells), not a web SPA. Each cell
recomputes when its referenced UI elements change. Convention: keep unrelated
GUI elements in separate cells (responsiveness) — see project CLAUDE.md.

## Apps

```
marimo/ingest_app.py   (901)  Ingestion UI: wiki picker, upload/drop sources,
                                  run ingest (+ scoped lint/repair tail), scan
                                  dir, regenerate, delete source, activity log.
                                  Calls domain.ingestion pipeline/batch + deletion.
marimo/read_app.py     (557)  Reading + RAG chat + save-to-wiki (3-col grid):
                                  - top-left: wiki picker (dropdown + add path)
                                  - left: wiki page selector / viewer
                                  - middle: page content + nav links
                                  - right: chat_panel streams the agent via
                                    wiki_agent.run_stream → stream_text(delta=True)
                                  - save_form → save_action → save_to_wiki
                                  - delete_widget_cell + delete_event_cell
marimo/trace_report_app.py    Read-only WIKI_TRACE run viewer (timeline + tree).
```

## Wiki picker (both apps, §7.1) — base/domain/wiki_registry.py

```
wiki_state    mo.state: active_wiki (seeded from WIKI_PATH) + recent_list
wiki_context  derives path-bound objects on switch; consumers retarget by name:
                read_app  → WIKI_PATH, wiki_db_path, wiki_chat_config, wiki_agent
                ingest_app→ WORKSPACE, DB_PATH, SOURCES_DIR (+ workspace DB init)
wiki_picker   mo.ui.dropdown over discovered + recent wikis
wiki_add(_runner)  accordion text-path input → sanitise/validate → set active
```
Directory picking avoids `mo.ui.file_browser(selection_mode="directory")` (no
value emitted in marimo 0.23.x, GH #1478) — uses discovery + recent + text path.

## Widgets

```
marimo/widgets/delete_confirm.py  (179)  DeleteConfirmWidget — anywidget delete
   button with inline JS confirm panel (avoids checkbox-reset pitfall).
```

## Prototypes (excluded from lint/build)

`marimo/prototypes/*` — experimental notebooks (chat_app, read_app_with_edit,
wiki_picker spike, confirmation-button/popup spikes). Excluded in pyproject.

## UI → domain entry points

```
ingest_app  → pipeline.ingest_file / scan_and_ingest / batch_ingest,
              deletion.delete_source, wiki_registry.*
read_app    → chat.create_agent (RAG), wiki_tools.save_to_wiki,
              wiki_fs.delete_page, wiki_registry.*, config.require_llm_config
```
