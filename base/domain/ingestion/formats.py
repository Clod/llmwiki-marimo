"""The source file formats ingestion accepts, defined once.

Every place that restricts or lists formats imports from here: the extractor,
the pipeline, the wiki service, the web upload filter and the ingestion screen.
"""

PDF_EXTENSIONS = frozenset({".pdf"})
# Plain text: read directly, needs neither Java nor LibreOffice.
TEXT_EXTENSIONS = frozenset({".md", ".txt"})
# Word processor files: LibreOffice converts each to PDF, then the PDF extractor
# reads it (so Java is needed too).
OFFICE_EXTENSIONS = frozenset({".docx", ".doc", ".odt", ".rtf"})

SUPPORTED_EXTENSIONS = PDF_EXTENSIONS | TEXT_EXTENSIONS | OFFICE_EXTENSIONS


def sorted_extensions(extensions: frozenset[str] = SUPPORTED_EXTENSIONS) -> list[str]:
    """`extensions` in a stable order, for messages and `accept` attributes."""
    return sorted(extensions)
