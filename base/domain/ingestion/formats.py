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

# Image files (docs/design_image_handling.md, D11): one page whose text is the generated
# description of the image. They need neither Java nor LibreOffice. No .svg: it is a
# text file that can carry scripts.
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})

SUPPORTED_EXTENSIONS = PDF_EXTENSIONS | TEXT_EXTENSIONS | OFFICE_EXTENSIONS | IMAGE_EXTENSIONS


def supported_extensions(images: bool = True) -> frozenset[str]:
    """The extensions ingestion accepts. With `images` false, the image files are left
    out: a wiki with `[images] ignore = true` does not take an image as a source."""
    return SUPPORTED_EXTENSIONS if images else SUPPORTED_EXTENSIONS - IMAGE_EXTENSIONS


def sorted_extensions(extensions: frozenset[str] = SUPPORTED_EXTENSIONS) -> list[str]:
    """`extensions` in a stable order, for messages and `accept` attributes."""
    return sorted(extensions)
