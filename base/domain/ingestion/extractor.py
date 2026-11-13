"""Text extraction from PDF, office, markdown and plain text files.

PDF:     opendataloader-pdf (via base/domain/ingestion/pdf_extract.py)
Office:  (.docx .doc .odt .rtf) LibreOffice headless → PDF → opendataloader-pdf
Text:    (.md .txt) read directly (base/domain/ingestion/text_extract.py);
         needs neither Java nor LibreOffice

Returns list[tuple[int, str]] — (page_number, markdown_content).
"""

import contextlib
import shutil
import subprocess
import tempfile
import logging
from pathlib import Path
from typing import Callable, Iterator

logger = logging.getLogger(__name__)

from .images import IMAGE_LINK_RE, IMAGES_DIRNAME, drop_kept, enrich_figures  # noqa: E402
from .ocr import OcrNotConfiguredError, apply_ocr  # noqa: E402,F401
from .formats import IMAGE_EXTENSIONS, TEXT_EXTENSIONS, sorted_extensions, supported_extensions  # noqa: E402
from .pdf_extract import extract_pdf  # noqa: E402
from .text_extract import extract_image_file, extract_text_file  # noqa: E402


class LibreOfficeNotInstalledError(RuntimeError):
    def __init__(self, filename: str = ""):
        msg = (
            f"LibreOffice is required to process '{filename}'. "
            "Install it and restart:\n"
            "  macOS:   brew install --cask libreoffice\n"
            "  Linux:   sudo apt-get install libreoffice\n"
            "  Windows: winget install TheDocumentFoundation.LibreOffice"
        )
        super().__init__(msg)


class JavaNotInstalledError(RuntimeError):
    def __init__(self, filename: str = ""):
        msg = (
            f"A Java runtime is required to extract text from '{filename}'. "
            "The PDF extractor (opendataloader-pdf) runs a .jar through the "
            "'java' command, and an office file (.docx, .doc, .odt, .rtf) is "
            "converted to PDF before the same extractor reads it, so those "
            "file types need a Java runtime. Only .md and .txt files do not. "
            "Install one and restart:\n"
            "  macOS:   brew install --cask temurin\n"
            "  Linux:   sudo apt-get install default-jre\n"
            "  Windows: winget install EclipseAdoptium.Temurin.21.JRE"
        )
        super().__init__(msg)


def check_java() -> str | None:
    """Return the java executable path, or None if no Java runtime is installed.

    opendataloader-pdf shells out to `java` to run its bundled .jar, so text
    extraction fails without a Java runtime on PATH. JAVA_HOME is honoured for
    an installation that was never added to PATH.
    """
    found = shutil.which("java")
    if found:
        return found

    import os
    java_home = os.environ.get("JAVA_HOME", "").strip()
    if java_home:
        candidate = Path(java_home) / "bin" / "java"
        if candidate.exists():
            return str(candidate)
    return None


def check_libreoffice() -> str | None:
    """Return the LibreOffice executable path, or None if not installed."""
    found = shutil.which("libreoffice") or shutil.which("soffice")
    if found:
        return found

    # Check standard macOS path
    import os
    macos_path = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if os.path.exists(macos_path):
        return macos_path
    return None


_PROBE_TIMEOUT_SECONDS = 90
_probe_results: dict[str, bool] = {}

# The smallest DOCX LibreOffice Writer opens: three parts, one paragraph.
_PROBE_DOCX_PARTS = {
    "[Content_Types].xml": (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.main+xml"/></Types>'
    ),
    "_rels/.rels": (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    ),
    "word/document.xml": (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>probe</w:t></w:r></w:p></w:body></w:document>"
    ),
}


def libreoffice_can_convert() -> bool:
    """True when the installed LibreOffice converts a DOCX to PDF.

    ``check_libreoffice`` only finds the executable. A partial install (the core
    without Writer) has one and cannot open a DOCX, so the first call converts a
    minimal document and the answer is cached for the process, per executable.
    """
    lo = check_libreoffice()
    if not lo:
        return False
    if lo not in _probe_results:
        _probe_results[lo] = _probe_conversion(lo)
    return _probe_results[lo]


def _probe_conversion(lo: str) -> bool:
    import zipfile

    with tempfile.TemporaryDirectory() as tmpdir:
        docx = Path(tmpdir) / "probe.docx"
        with zipfile.ZipFile(docx, "w") as zf:
            for name, xml in _PROBE_DOCX_PARTS.items():
                zf.writestr(name, xml)
        outdir = Path(tmpdir) / "out"
        try:
            result = subprocess.run(
                [lo, "--headless", "--norestore", "--convert-to", "pdf",
                 "--outdir", str(outdir), str(docx)],
                capture_output=True,
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("LibreOffice probe failed to run: %s", exc)
            return False
        converted = result.returncode == 0 and any(outdir.glob("*.pdf"))
        if not converted:
            logger.warning("LibreOffice is installed but cannot convert a DOCX: %s",
                           result.stderr.decode(errors="replace")[:200])
        return converted


def extract(
    file_path: Path, cache_dir: Path, language: str = "en",
    progress_cb: Callable[[str], None] | None = None, *, keep_images: bool = True,
) -> tuple[list[tuple[int, str]], str]:
    """Extract text from a supported file.

    A PDF with no text layer goes through the OCR engine of ``OCR_ENGINE``
    (`ocr.apply_ocr`); `language` is the wiki language the engine reads in.
    A DOCX never does.

    Returns (page_contents, parser_name).
    page_contents is list of (page_number, markdown).
    A .md or .txt file is read directly and needs neither Java nor LibreOffice; the
    image links of a .md are kept in `cache_dir/images/`. An image file is one page, its
    link (`extract_image_file`), and needs neither tool.
    Raises JavaNotInstalledError for a PDF or an office file when no Java
    runtime is installed: both reach opendataloader-pdf, which runs a .jar.
    Raises LibreOfficeNotInstalledError for an office file when LibreOffice is
    missing.
    Raises OcrNotConfiguredError when a PDF needs OCR and OCR_ENGINE is `none`.
    Raises RuntimeError on extraction failure, including an empty text file.
    The images of a PDF (or an office file) are kept in `cache_dir/images/`, the
    document's ingestion folder, and the markdown links to them (domain/ingestion/images.py).
    With `keep_images` false (`[images] ignore = true`), no image is kept and the text has
    no link to one: the images folder and `figures.json` of an earlier extraction are
    deleted, and an image file raises ValueError as an unsupported type.
    """
    ext = file_path.suffix.lower()
    supported = supported_extensions(keep_images)
    if ext not in supported:
        raise ValueError(
            f"Unsupported file type: {ext}. Supported: {', '.join(sorted_extensions(supported))}"
        )
    if ext in TEXT_EXTENSIONS:
        if keep_images:
            return extract_text_file(file_path, cache_dir)
        drop_kept(cache_dir)
        return extract_text_file(file_path, None)
    if ext in IMAGE_EXTENSIONS:
        return extract_image_file(file_path, cache_dir)
    java = check_java()
    if not java:
        raise JavaNotInstalledError(file_path.name)
    with _java_on_path(java):
        if ext == ".pdf":
            return _extract_pdf(file_path, cache_dir, language, progress_cb, keep_images=keep_images)
        return _extract_office(file_path, cache_dir, keep_images=keep_images)


def extract_images(file_path: Path, cache_dir: Path) -> None:
    """Extract the images of a PDF or DOCX again into `cache_dir/images/`.

    It runs the same conversion as `extract` and drops the text: the stored text
    is not touched, no model is called. A PDF does not go through OCR: OCR gives
    text, and the text is dropped. Used by the missing-image repair.
    Raises the same errors as `extract`, except OcrNotConfiguredError.
    """
    if file_path.suffix.lower() != ".pdf":
        extract(file_path, cache_dir)
        return
    java = check_java()
    if not java:
        raise JavaNotInstalledError(file_path.name)
    with _java_on_path(java):
        _extract_pdf(file_path, cache_dir, "en", None, ocr=False)


@contextlib.contextmanager
def _java_on_path(java: str) -> Iterator[None]:
    """Make the runtime check_java found reachable as the bare `java` command.

    opendataloader-pdf runs `["java", ...]` through subprocess and resolves it
    against PATH only; it never reads JAVA_HOME. So a runtime that check_java
    accepted through the JAVA_HOME fallback still failed inside the dependency
    with "No such file or directory: 'java'". Prepending that runtime's bin
    directory to PATH for the duration of the extraction closes the gap; when
    java is already on PATH nothing changes.
    """
    import os
    if shutil.which("java"):
        yield
        return
    previous = os.environ.get("PATH", "")
    os.environ["PATH"] = str(Path(java).parent) + os.pathsep + previous
    try:
        yield
    finally:
        os.environ["PATH"] = previous


def _extract_pdf(
    file_path: Path, cache_dir: Path, language: str, progress_cb: Callable[[str], None] | None,
    *, ocr: bool = True, keep_images: bool = True,
) -> tuple[list[tuple[int, str]], str]:
    """The pages of a PDF. The images are kept first; then OCR reads the pages with no
    text layer. A page that OCR read keeps the links to its kept images
    (`keep_image_links`), so the image handling sees them as on any other page. With
    `keep_images` false no image is kept, and the kept images of an earlier extraction
    are deleted."""
    logger.info("Extracting PDF: %s", file_path.name)
    if keep_images:
        pages = extract_pdf(str(file_path), cache_dir / IMAGES_DIRNAME)
        enrich_figures(cache_dir)
    else:
        pages = extract_pdf(str(file_path), None)
        drop_kept(cache_dir)
    if not ocr:
        return pages, "opendataloader"
    read, engine = apply_ocr(file_path, pages, cache_dir, language, progress=progress_cb)
    return keep_image_links(pages, read), "opendataloader" if engine is None else f"opendataloader+ocr-{engine}"


def keep_image_links(before: list[tuple[int, str]], after: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """`after` with each link to a kept image of `before` that its page lost appended
    to that page. OCR replaces the text of a scanned page; the links to the images
    kept from that page are not text, and the image handling needs them."""
    links = {number: [m.group(0) for m in IMAGE_LINK_RE.finditer(markdown)] for number, markdown in before}
    merged = []
    for number, markdown in after:
        lost = [link for link in links.get(number, []) if link not in markdown]
        merged.append((number, "\n\n".join([markdown.rstrip(), *lost]).strip() if lost else markdown))
    return merged


def convert_office_to_pdf(file_path: Path, cache_dir: Path) -> Path:
    """Convert an office file (.docx, .doc, .odt, .rtf) to PDF with LibreOffice; return `cache_dir/converted.pdf`.

    The PDF stays in the document's cache directory: ingestion reads its text,
    and a user interface can show it, since a browser cannot show these formats.
    Raises LibreOfficeNotInstalledError when LibreOffice is missing, and
    RuntimeError when the conversion fails.
    """
    lo = check_libreoffice()
    if not lo:
        raise LibreOfficeNotInstalledError(file_path.name)

    logger.info("Converting %s → PDF via LibreOffice: %s", file_path.suffix.lower(), file_path.name)

    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [lo, "--headless", "--norestore", "--convert-to", "pdf",
             "--outdir", tmpdir, str(file_path)],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"LibreOffice conversion failed: {result.stderr.decode()[:300]}"
            )

        pdf_files = list(Path(tmpdir).glob("*.pdf"))
        if not pdf_files:
            raise RuntimeError("LibreOffice produced no PDF output")

        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / "converted.pdf"
        shutil.copy2(pdf_files[0], cached)
        logger.info("Cached converted PDF: %s", cached)
    return cached


# The old name, kept for callers that still import it.
convert_docx_to_pdf = convert_office_to_pdf


def _extract_office(
    file_path: Path, cache_dir: Path, *, keep_images: bool = True,
) -> tuple[list[tuple[int, str]], str]:
    converted = str(convert_office_to_pdf(file_path, cache_dir))
    if keep_images:
        pages = extract_pdf(converted, cache_dir / IMAGES_DIRNAME)
        enrich_figures(cache_dir)
    else:
        pages = extract_pdf(converted, None)
        drop_kept(cache_dir)
    return pages, "libreoffice+opendataloader"
