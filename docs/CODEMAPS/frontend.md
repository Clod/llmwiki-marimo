<!-- Generated: 2026-05-25 | Files scanned: 4 | Token estimate: ~550 -->

# Frontend (marimo_new)

UI is **marimo notebooks** (reactive Python cells), not a web SPA. Each cell
recomputes when its referenced UI elements change. Convention: keep unrelated
GUI elements in separate cells (responsiveness) — see project CLAUDE.md.

## Apps

```
marimo_new/ingest_app.py   (501)  Ingestion UI: upload/drop sources, run ingest,
                                  scan dir, delete source, view log. Calls
                                  domain.ingestion.pipeline / batch + deletion.
marimo_new/read_app.py     (356)  Reading + RAG chat + save-to-wiki:
                                  - left: wiki page selector / viewer
                                  - right: chat_panel (cell ~L174) streams agent
                                    via Agent.iter_stream
                                  - save_form → save_action → save_to_wiki
                                  - delete_widget_cell + delete_event_cell
```

## Widgets

```
marimo_new/widgets/delete_confirm.py  (179)  DeleteConfirmWidget — anywidget
   delete button with inline JS confirm panel (avoids checkbox-reset pitfall).
```

## Prototypes (excluded from lint/build)

`marimo_new/prototypes/*` — experimental notebooks (chat_app, read_app_with_edit,
confirmation-button/popup spikes). Not production; excluded in pyproject.

## UI → domain entry points

```
ingest_app  → pipeline.ingest_file / scan_and_ingest / batch_ingest, deletion.delete_source
read_app    → chat.create_agent (RAG), wiki_tools.save_to_wiki, wiki_fs.delete_page
```
