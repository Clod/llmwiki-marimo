"""The ingestion of one file, pinned: the nodes of the trace, the result, the state of the
document and the pages written, for each path through `_ingest_file`
(`base/domain/ingestion/pipeline.py`).

The source is a markdown file, so no parser runs; the model is `FakeLLMClient`. The cases
were recorded on the code before the ingestion became a graph (2026-11-17); a change of
structure must leave them as they are. The case `malformed_extraction` was recorded again
when the extraction became a decision (I_CONCEPTS_RETRY): two replies that are not valid JSON
now fail the run with no page written, where the raw reply was the summary page before."""

import json

import pytest

from domain import tracing
from domain.ingestion import pipeline
from domain.ingestion.pipeline import ingest_file
from domain.tools.db import get_connection
from tests.helpers.workspace import tmp_workspace  # noqa: F401 — the fixture

SOURCE = "# Caución bursátil\n\nLa caución bursátil es un préstamo de corto plazo garantizado con títulos.\n"
EXTRACTION = json.dumps({
    "document_summary": "Un documento sobre la caución bursátil.",
    "concepts": [{"name": "Caución bursátil", "category": "instrument", "insight": "Un préstamo garantizado",
                  "aliases": ["caución"], "topics": ["préstamos"]}],
})
CONCEPT = "# Caución bursátil\n\nLa caución bursátil es un préstamo garantizado.[^1]\n\n[^1]: cauciones.md\n"
OVERVIEW = "# Knowledge Base Overview\n\nUna wiki sobre instrumentos financieros.\n"


def _source(ws, name="cauciones.md", text=SOURCE):
    path = ws.workspace / "sources" / name
    path.write_text(text, encoding="utf-8")
    return path


def _ingest(ws, path, responses, **kwargs):
    ws.llm.responses = list(responses)
    ws.llm._call_index = 0
    with tracing.collect() as trace:
        result = ingest_file(path, ws.db_path, ws.workspace, ws.llm, "fake", language="es", **kwargs)
    return result, [entry["node"] for entry in trace]


def _snapshot(ws, path, result, nodes):
    with get_connection(ws.db_path) as conn:
        row = conn.execute("SELECT status, error_message FROM documents WHERE relative_path = ?",
                           (f"sources/{path.name}",)).fetchone()
    pages = sorted(str(p.relative_to(ws.workspace)) for p in (ws.workspace / "wiki").rglob("*.md"))
    return {
        "nodes": nodes,
        "result": (result.status, result.message[:60]),
        "document": (row["status"], (row["error_message"] or "")[:60]) if row else None,
        "pages": pages,
    }


def _run(name, ws, monkeypatch):
    responses = [EXTRACTION, CONCEPT, OVERVIEW]
    if name == "invalid_file":
        path = _source(ws, "notas.xyz")
        return _snapshot(ws, path, *_ingest(ws, path, responses))
    if name == "unchanged_file":
        path = _source(ws)
        _ingest(ws, path, responses)
        return _snapshot(ws, path, *_ingest(ws, path, responses))
    if name == "new_document":
        path = _source(ws)
        return _snapshot(ws, path, *_ingest(ws, path, responses))
    if name == "batch_mode":
        path = _source(ws)
        return _snapshot(ws, path, *_ingest(ws, path, responses, _batch_mode=True))
    if name == "lint_after_ingest":
        path = _source(ws)
        return _snapshot(ws, path, *_ingest(ws, path, responses, lint_after_ingest=True))
    if name == "changed_document":
        path = _source(ws)
        _ingest(ws, path, responses)
        path.write_text(SOURCE + "\nUn párrafo nuevo sobre la tasa.\n", encoding="utf-8")
        return _snapshot(ws, path, *_ingest(ws, path, responses))
    if name == "concept_page_fails":
        def broken(*args, **kwargs):
            raise RuntimeError("the concept page could not be written")
        monkeypatch.setattr(pipeline, "build_concept_page", broken)
        path = _source(ws)
        return _snapshot(ws, path, *_ingest(ws, path, responses))
    if name == "malformed_extraction":
        path = _source(ws)
        return _snapshot(ws, path, *_ingest(ws, path, ['{"document_summary": "Un documento", "concepts": [{"na',
                                                       OVERVIEW]))
    if name == "aliases_fail":
        def broken(*args, **kwargs):
            raise RuntimeError("the aliases could not be written")
        monkeypatch.setattr(pipeline, "update_generated_aliases", broken)
        path = _source(ws)
        return _snapshot(ws, path, *_ingest(ws, path, responses))
    raise KeyError(name)


CASES = ("invalid_file", "unchanged_file", "new_document", "batch_mode", "lint_after_ingest",
         "changed_document", "concept_page_fails", "malformed_extraction", "aliases_fail")

EXPECTED = {'aliases_fail': {'document': ('ready', ''),
                  'nodes': ['document',
                            'I_VALIDATE',
                            'I_IS_CHANGED',
                            'I_DOCUMENT_ROW',
                            'I_EXTRACT',
                            'W_CHUNK',
                            'W_STEM',
                            'W_INSERT_CHUNKS',
                            'I_CONCEPTS',
                            'I_CONCEPT_PAGES',
                            'W_CHUNK',
                            'W_STEM',
                            'W_INSERT_CHUNKS',
                            'I_ALIASES',
                            'I_SUMMARY_PAGE',
                            'W_CHUNK',
                            'W_STEM',
                            'W_INSERT_CHUNKS',
                            'I_IS_BATCH',
                            'I_OVERVIEW',
                            'I_LOG',
                            'I_COMMIT',
                            'E_INGESTED'],
                  'pages': ['wiki/concepts/caucion-bursatil.md',
                            'wiki/index.md',
                            'wiki/log.md',
                            'wiki/overview.md',
                            'wiki/summaries/cauciones.md'],
                  'result': ('ingested', '1 pages')},
 'batch_mode': {'document': ('ready', ''),
                'nodes': ['document',
                          'I_VALIDATE',
                          'I_IS_CHANGED',
                          'I_DOCUMENT_ROW',
                          'I_EXTRACT',
                          'W_CHUNK',
                          'W_STEM',
                          'W_INSERT_CHUNKS',
                          'I_CONCEPTS',
                          'I_CONCEPT_PAGES',
                          'W_CHUNK',
                          'W_STEM',
                          'W_INSERT_CHUNKS',
                          'I_ALIASES',
                          'I_SUMMARY_PAGE',
                          'W_CHUNK',
                          'W_STEM',
                          'W_INSERT_CHUNKS',
                          'I_IS_BATCH',
                          'E_INGESTED'],
                'pages': ['wiki/concepts/caucion-bursatil.md',
                          'wiki/index.md',
                          'wiki/log.md',
                          'wiki/overview.md',
                          'wiki/summaries/cauciones.md'],
                'result': ('ingested', '1 pages')},
 'changed_document': {'document': ('ready', ''),
                      'nodes': ['document',
                                'I_VALIDATE',
                                'I_IS_CHANGED',
                                'I_DOCUMENT_ROW',
                                'I_EXTRACT',
                                'W_CHUNK',
                                'W_STEM',
                                'W_INSERT_CHUNKS',
                                'I_CONCEPTS',
                                'I_CONCEPT_PAGES',
                                'W_CHUNK',
                                'W_STEM',
                                'W_INSERT_CHUNKS',
                                'I_ALIASES',
                                'I_SUMMARY_PAGE',
                                'W_CHUNK',
                                'W_STEM',
                                'W_INSERT_CHUNKS',
                                'I_IS_BATCH',
                                'I_OVERVIEW',
                                'I_LOG',
                                'I_COMMIT',
                                'E_INGESTED'],
                      'pages': ['wiki/concepts/caucion-bursatil.md',
                                'wiki/index.md',
                                'wiki/log.md',
                                'wiki/overview.md',
                                'wiki/summaries/cauciones.md'],
                      'result': ('ingested', '1 pages')},
 'concept_page_fails': {'document': ('failed', 'the concept page could not be written'),
                        'nodes': ['document',
                                  'I_VALIDATE',
                                  'I_IS_CHANGED',
                                  'I_DOCUMENT_ROW',
                                  'I_EXTRACT',
                                  'W_CHUNK',
                                  'W_STEM',
                                  'W_INSERT_CHUNKS',
                                  'I_CONCEPTS',
                                  'I_CONCEPT_PAGES',
                                  'E_ROLLED_BACK'],
                        'pages': ['wiki/index.md', 'wiki/log.md', 'wiki/overview.md'],
                        'result': ('failed', 'the concept page could not be written')},
 'invalid_file': {'document': None,
                  'nodes': ['document', 'I_VALIDATE', 'E_INVALID'],
                  'pages': ['wiki/index.md', 'wiki/log.md', 'wiki/overview.md'],
                  'result': ('failed', "Unsupported file type '.xyz'. Supported: .doc, .docx, .gif, ")},
 'lint_after_ingest': {'document': ('ready', ''),
                       'nodes': ['document',
                                 'I_VALIDATE',
                                 'I_IS_CHANGED',
                                 'I_DOCUMENT_ROW',
                                 'I_EXTRACT',
                                 'W_CHUNK',
                                 'W_STEM',
                                 'W_INSERT_CHUNKS',
                                 'I_CONCEPTS',
                                 'I_CONCEPT_PAGES',
                                 'W_CHUNK',
                                 'W_STEM',
                                 'W_INSERT_CHUNKS',
                                 'I_ALIASES',
                                 'I_SUMMARY_PAGE',
                                 'W_CHUNK',
                                 'W_STEM',
                                 'W_INSERT_CHUNKS',
                                 'I_IS_BATCH',
                                 'I_OVERVIEW',
                                 'I_LOG',
                                 'I_COMMIT',
                                 'I_LINT',
                                 'E_INGESTED'],
                       'pages': ['wiki/concepts/caucion-bursatil.md',
                                 'wiki/index.md',
                                 'wiki/log.md',
                                 'wiki/overview.md',
                                 'wiki/summaries/cauciones.md'],
                       'result': ('ingested', '1 pages')},
 'malformed_extraction': {'document': ('failed',
                                       'the reply of the extraction is not valid JSON, after one ret'),
                          'nodes': ['document',
                                    'I_VALIDATE',
                                    'I_IS_CHANGED',
                                    'I_DOCUMENT_ROW',
                                    'I_EXTRACT',
                                    'W_CHUNK',
                                    'W_STEM',
                                    'W_INSERT_CHUNKS',
                                    'I_CONCEPTS',
                                    'I_CONCEPTS_RETRY',
                                    'E_ROLLED_BACK'],
                          'pages': ['wiki/index.md', 'wiki/log.md', 'wiki/overview.md'],
                          'result': ('failed',
                                     'the reply of the extraction is not valid JSON, after one ret')},
 'new_document': {'document': ('ready', ''),
                  'nodes': ['document',
                            'I_VALIDATE',
                            'I_IS_CHANGED',
                            'I_DOCUMENT_ROW',
                            'I_EXTRACT',
                            'W_CHUNK',
                            'W_STEM',
                            'W_INSERT_CHUNKS',
                            'I_CONCEPTS',
                            'I_CONCEPT_PAGES',
                            'W_CHUNK',
                            'W_STEM',
                            'W_INSERT_CHUNKS',
                            'I_ALIASES',
                            'I_SUMMARY_PAGE',
                            'W_CHUNK',
                            'W_STEM',
                            'W_INSERT_CHUNKS',
                            'I_IS_BATCH',
                            'I_OVERVIEW',
                            'I_LOG',
                            'I_COMMIT',
                            'E_INGESTED'],
                  'pages': ['wiki/concepts/caucion-bursatil.md',
                            'wiki/index.md',
                            'wiki/log.md',
                            'wiki/overview.md',
                            'wiki/summaries/cauciones.md'],
                  'result': ('ingested', '1 pages')},
 'unchanged_file': {'document': ('ready', ''),
                    'nodes': ['document', 'I_VALIDATE', 'I_IS_CHANGED', 'E_UNCHANGED'],
                    'pages': ['wiki/concepts/caucion-bursatil.md',
                              'wiki/index.md',
                              'wiki/log.md',
                              'wiki/overview.md',
                              'wiki/summaries/cauciones.md'],
                    'result': ('skipped', 'already up to date')}}


@pytest.mark.parametrize("name", CASES)
def test_the_ingestion_of_a_file_keeps_its_path(name, tmp_workspace, monkeypatch):  # noqa: F811
    assert _run(name, tmp_workspace, monkeypatch) == EXPECTED[name]
