# web — the web application

A FastAPI application that renders HTML with Jinja2, updates the page with HTMX,
and streams the chat answers and the progress of long operations as
Server-Sent Events (SSE). The routes call `base/services/` (`wiki.py`,
`chat.py`, `ingest.py`) and hold no application logic.

The application replaces the marimo apps as the user interface. `marimo/` stays
in the repository until its removal is decided separately.

## Launch

```
uv sync --group web
WIKI_PATH=/path/to/wiki WIKI_HOME=/path/to/folder-of-wikis \
  uv run --group web uvicorn web.app:app --port 8765
```

Open http://localhost:8765. The server listens on `localhost` and has no
authentication: it is for one user on one machine.

## Environment variables

| Variable | Use | Read in |
|---|---|---|
| `WIKI_PATH` | The wiki selected by default. | `web/settings.py` |
| `WIKI_HOME` | The folder the picker scans for wikis. Without it, the parent folder of `WIKI_PATH`; without both, the home folder of the user. | `domain/wiki_registry.py`, `resolve_wiki_home` |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | The model of the chat. | `base/config.py` |
| `WIKI_LLM_BASE_URL`, `WIKI_LLM_API_KEY`, `WIKI_LLM_MODEL` | The model of the ingestion. Each falls back to its `LLM_*` value. | `base/config.py` |
| `WIKI_TRACE=1` | Writes the spans of each chat turn and ingestion to `<wiki>/.llmwiki/traces/spans.jsonl`. | `domain/tracing.py` |

The `.env` file at the repository root is read as in the marimo apps.

## Layout

| Path | Content |
|---|---|
| `app.py` | `create_app(settings)`: the application factory. `app`: the instance uvicorn loads. |
| `settings.py` | `WebSettings`: the wiki folders and the model settings. |
| `state.py` | `AppState`: the open wikis, the conversations, the pending turns and the running operations. One per application. |
| `deps.py` | The context every screen shares: the header, the index column. |
| `i18n.py` | The interface language: the catalogs, the choice of language (`negotiate`), `_()`, `N_()`, `ngettext()`. No FastAPI import. |
| `i18n_extract.py` | Extracts the message ids of `web/` and updates the catalogs: `uv run --group web python -m web.i18n_extract`. |
| `locale/` | `messages.pot` and `<lang>/LC_MESSAGES/messages.po`: one catalog per language except English, the source. |
| `routes/lang.py` | `POST /lang`: sets the `ui_lang` cookie and returns to the page the switch was on. |
| `routes/picker.py` | The wiki picker. |
| `routes/pages.py` | View, edit and delete a page; the live preview of the editor (`POST /w/{wiki_id}/preview/{page}`, rendered by `render.py`, nothing saved); the source documents at `/w/{wiki_id}/view/{filename}`. |
| `routes/chat.py` | A chat turn over SSE, the thread of a conversation, clear, and the two steps of saving: the draft (`/chat/draft`, which opens the review dialog), the preview, and the write of the page the user approved (`/chat/save`). |
| `routes/ingest.py` | The ingestion screen and its operations, streamed over SSE: ingest, scan, regenerate, lint and repair, rebuild the index (`services.wiki.reindex`, no model call), delete stale pages and sources. |
| `routes/history.py` | The history of the wiki and of each page: the list of commits, the revert as a streamed operation, the version of a page at a commit and its comparison with the current one. |
| `routes/relations.py` | The relations screen and the graph it draws (`relations.json`). |
| `routes/vocabulary.py` | The Vocabulary screen: the roster, the aliases, the rejected aliases and the blacklist of a wiki (`services.vocabulary`), and one route per change (`POST …/vocabulary/{op}`). |
| `render.py` | `render_markdown`: markdown to HTML with every internal reference rewritten to an application URL. No FastAPI import. |
| `templates/` | The Jinja2 templates. |
| `static/` | The CSS (design B, blue and yellow: every colour, size and spacing step is a custom property of `css/tokens.css`; `tests/web/test_design.py` checks the contrast) and the JavaScript, including `htmx.min.js` (htmx 2.0.4) and `htmx-ext-sse.js` (htmx-ext-sse 2.2.2). The Geist and Geist Mono stylesheet loads from Google Fonts; the page works without it, in the fallback fonts. |

## State

The state lives in memory and is lost on restart: open wikis, conversations,
running operations. The wikis on disk are not affected.

- A conversation belongs to one wiki. The browser keeps its id per wiki in
  `sessionStorage`, so the conversation survives navigation, the editor and a
  reload in the same tab (`static/js/reader.js`, `routes/chat.py:chat_thread`).
  A new tab starts a new conversation.
- One operation runs at a time per wiki (`state.py:start_operation`,
  `OpenWiki.busy`). A second ingestion, scan, regeneration, lint, deletion of
  stale pages, deletion of a source or save of a conversation gets the message
  "Another operation is running on this wiki." ("Hay otra operación en curso
  sobre esta wiki." in Spanish)

## Source formats

The accepted formats come from `base/domain/ingestion/formats.py`
(`SUPPORTED_EXTENSIONS`): `.pdf`; the office formats `.docx`, `.doc`, `.odt`,
`.rtf` (LibreOffice converts each to PDF); and `.md`, `.txt` (read directly, no
Java and no LibreOffice). The upload filter and the ingestion screen import the
list. `/w/{wiki}/view/<file>` shows a PDF or an office file as a PDF, a `.txt`
as `text/plain; charset=utf-8`, and a `.md` rendered inside the application
layout, read-only.

## Interface language

The interface is in English and in Spanish, and a person chooses. Two languages
exist and must not be confused:

- **The interface language** is the language of the labels, tooltips,
  confirmations, notices and console lines that `web/` writes. It is chosen by
  the `ui_lang` cookie (`en` or `es`), set by the "EN | ES" switch at the right of
  the header on every screen; without the cookie, by the browser's
  `Accept-Language`; without that, English. `<html lang>` follows it.
- **The wiki language** is `[wiki].language` in the wiki's `wiki_config.toml`. It
  governs what the model writes and what ends up inside the wiki: page bodies,
  the saved conversation (`services.chat.conversation_markdown`) and `log.md`.
  The interface language never reaches the wiki. A Spanish wiki with the English
  interface is a valid combination, and so is the reverse.

The message ids are the English texts. In a template they are written
`{{ _("…") }}` or `{% trans %}…{% endtrans %}` (`{% trans count=n %}…{% pluralize %}…{% endtrans %}`
for a plural); in `web/` Python, `_("…")`, `ngettext(…)` and, for a constant
defined at import time that is translated where it is used, `N_("…")`. A text with
a variable uses a named placeholder: `_("Deleted the page “%(title)s”.", title=title)`.
The scripts hold no human text: what they write comes from `data-*` attributes the
template renders (`data-label-edit`, `data-label-preview`, `data-counts-label`).

The catalogs are GNU gettext files compiled in memory when `web/i18n.py` loads; no
`.mo` file is written or committed. Each request sets its language in a context
variable (`app.interface_language`), which a template, a route and a thread started
from a route (`state.start_operation` copies the context) all read.

To add a language (Italian is planned): add its code and its name to `LANGUAGES` in
`web/i18n.py`, run `uv run --group web python -m web.i18n_extract` (it creates
`locale/it/LC_MESSAGES/messages.po` with every id empty), and translate it.
`tests/web/test_i18n.py` fails while an id has no translation, while a catalog holds
an id the code no longer uses, and when a translation changes the placeholders of its id.

Left in English on every interface language, because they are not written by
`web/`: the progress lines of `base/domain/` (ingestion, lint and repair, rebuild of
the index), the messages `services` return (`delete_source`, `PageConflict`,
`source_view`) and the errors of the model or of git that a notice quotes.

## Tests

```
uv run --group web pytest -q tests/web                        # routes, render, Playwright
uv run --group web pytest -q tests/web --ignore=tests/web/e2e # without a browser
```

No test calls a model, and none needs Java or LibreOffice. The agents are
simulated (`tests/web/conftest.py`, `FakeAgent`) and the ingestion services are
replaced. The Playwright tests (`tests/web/e2e/`) start the application on a free
port over a copy of `examples/finanzas-argentinas`, and skip when Playwright
cannot start a browser (`uv run playwright install chromium`).

## CI

`.github/workflows/test.yml` has a job `web` that runs `uv sync --group dev --group web`,
`uv run playwright install --with-deps chromium` and `uv run --group web pytest -q tests/web`.

## Limits

- The HTML of a page or an answer is sanitized with an allow-list
  (`web/render.py:sanitize_html`, `nh3`): no script, event handler or `javascript:`
  link reaches the browser, only `http`, `https` and `mailto` links, and the
  only style property kept is a table's `text-align`. The server still has no
  authentication: keep it on `localhost`, for one user.
- The progress lines of the ingestion come from `base/domain/` and are in English
  on both interface languages.
