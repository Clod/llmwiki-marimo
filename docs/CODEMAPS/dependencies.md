<!-- Written 2026-11-20 from pyproject.toml and base/config.py. -->

# Dependencies

## Runtime (`pyproject.toml`, `dependencies`)

```
openai               client of any OpenAI-compatible endpoint
pydantic-ai          the chat agent: tools, streaming
pydantic-graph       the chat workflows (base/domain/chat/workflow/)
pydantic-settings    configuration (base/config.py), with python-dotenv for .env
aiosqlite            async SQLite access of the chat tools
opendataloader-pdf   PDF text extraction (needs Java)
pypdfium2, pillow    page rendering for OCR by vision, image handling
snowballstemmer      the roots of the search index
pyyaml               front matter
tomlkit              writes wiki_config.toml keeping its comments
```

## Groups (`[dependency-groups]`)

```
web      fastapi, jinja2, uvicorn, sse-starlette, markdown, python-multipart,
         nh3 (HTML sanitising), babel (translations)
dev      pytest, pytest-asyncio, playwright, ruff
verify   transformers, torch, sentencepiece, protobuf: the optional sentence verifier;
         installed from the Maintain screen or with uv sync --group verify
```

## External programs

```
Java          opendataloader-pdf
LibreOffice   converts .docx, .doc, .odt and .rtf to PDF
git           optional: a missing git skips the commit with a warning
ocrmypdf      only with OCR_ENGINE=tesseract
```

## External services

```
LLM API      OpenAI-compatible (base URL, key and model from .env). Used by
             ingestion, lint (contradiction, data gap), repair (stale,
             missing concept), the chat and the relevance judge.
Vision API   descriptions of images, and OCR with OCR_ENGINE=vision.
Web search   not built (ROADMAP.md).
```

## Settings (`base/config.py`)

No provider or model is hard-coded. Every model value comes from `.env`, and the
Settings screen writes it.

```
LLM_BASE_URL, LLM_API_KEY, LLM_MODEL              the chat, and the default of the others
WIKI_LLM_BASE_URL, _API_KEY, _MODEL               ingestion, a blank value uses LLM_*
VISION_BASE_URL, _API_KEY, _MODEL                 descriptions of images
OCR_ENGINE                                        none | tesseract | vision
OCR_BASE_URL, OCR_API_KEY, OCR_MODEL              the vision model of OCR
WIKI_PATH                                         the wiki opened at launch
WIKI_HOME                                         the folder the wiki picker scans
WIKI_AUTOCOMMIT                                   0 turns off the git commits
WIKI_TRACE                                        1 writes the trace
```

Each wiki has its own `wiki_config.toml`. Its sections are listed in
[`ingestion_walkthrough.md`](../ingestion_walkthrough.md#wikiconfigtoml).
