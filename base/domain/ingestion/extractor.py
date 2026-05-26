"""Text extraction from PDF and DOCX files.

PDF:  opendataloader-pdf (via base/domain/ingestion/pdf_extract.py)
DOCX: LibreOffice headless → PDF → opendataloader-pdf

Returns list[tuple[int, str]] — (page_number, markdown_content).
"""

import shutil
import subprocess
import tempfile
import logging
from pathlib import Path

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
    Raises LibreOfficeNotInstalledError for DOCX when LibreOffice is missing.
    Raises RuntimeError on extraction failure.
    """
    ext = file_path.suffix.lower()
    if ext == ".pdf":
        return _extract_pdf(file_path)
    if ext == ".docx":
        return _extract_docx(file_path, cache_dir)
    raise ValueError(f"Unsupported file type: {ext}")


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
