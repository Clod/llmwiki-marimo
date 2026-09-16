"""Text extraction from PDF and DOCX files.

PDF:  opendataloader-pdf (via base/domain/ingestion/pdf_extract.py)
DOCX: LibreOffice headless → PDF → opendataloader-pdf

Returns list[tuple[int, str]] — (page_number, markdown_content).
"""

import contextlib
import shutil
import subprocess
import tempfile
import logging
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

from .pdf_extract import extract_pdf  # noqa: E402


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
            "'java' command, and a DOCX is converted to PDF before the same "
            "extractor reads it, so both file types need a Java runtime. "
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


def extract(file_path: Path, cache_dir: Path) -> tuple[list[tuple[int, str]], str]:
    """Extract text from a supported file.

    Returns (page_contents, parser_name).
    page_contents is list of (page_number, markdown).
    Raises JavaNotInstalledError when no Java runtime is installed: both file
    types reach opendataloader-pdf, which runs a .jar.
    Raises LibreOfficeNotInstalledError for DOCX when LibreOffice is missing.
    Raises RuntimeError on extraction failure.
    """
    ext = file_path.suffix.lower()
    if ext not in (".pdf", ".docx"):
        raise ValueError(f"Unsupported file type: {ext}")
    java = check_java()
    if not java:
        raise JavaNotInstalledError(file_path.name)
    with _java_on_path(java):
        if ext == ".pdf":
            return _extract_pdf(file_path)
        return _extract_docx(file_path, cache_dir)


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


def _extract_pdf(file_path: Path) -> tuple[list[tuple[int, str]], str]:
    logger.info("Extracting PDF: %s", file_path.name)
    pages = extract_pdf(str(file_path))
    return pages, "opendataloader"


def _extract_docx(file_path: Path, cache_dir: Path) -> tuple[list[tuple[int, str]], str]:
    lo = check_libreoffice()
    if not lo:
        raise LibreOfficeNotInstalledError(file_path.name)

    logger.info("Converting DOCX → PDF via LibreOffice: %s", file_path.name)

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

        converted_pdf = pdf_files[0]

        # Cache the converted PDF so it can be served to the viewer
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / "converted.pdf"
        shutil.copy2(converted_pdf, cached)
        logger.info("Cached converted PDF: %s", cached)

        pages = extract_pdf(str(converted_pdf))

    return pages, "libreoffice+opendataloader"
