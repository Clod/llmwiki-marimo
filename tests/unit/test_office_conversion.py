"""The office formats go through the same LibreOffice conversion as .docx.

The tests skip when LibreOffice is missing or cannot convert (a machine can have
`soffice` without the Writer component). No model, no Java."""

import zipfile
from pathlib import Path

import pytest

from domain.ingestion.extractor import check_libreoffice, convert_office_to_pdf

RTF = rb"{\rtf1\ansi\deff0 {\fonttbl{\f0 Arial;}}\f0\fs24 Hola mundo\par}"

ODT_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">
<office:body><office:text><text:p>Hola mundo</text:p></office:text></office:body>
</office:document-content>"""

ODT_MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>
<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
</manifest:manifest>"""


def make_odt(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text", compress_type=zipfile.ZIP_STORED)
        z.writestr("content.xml", ODT_CONTENT)
        z.writestr("META-INF/manifest.xml", ODT_MANIFEST)


def sample(tmp_path: Path, suffix: str) -> Path:
    path = tmp_path / f"muestra{suffix}"
    if suffix == ".odt":
        make_odt(path)
    else:
        path.write_bytes(RTF)  # LibreOffice sniffs the content: a .doc may hold RTF
    return path


@pytest.fixture(scope="module")
def can_convert(tmp_path_factory):
    if not check_libreoffice():
        pytest.skip("LibreOffice is not installed")
    probe = tmp_path_factory.mktemp("probe")
    try:
        convert_office_to_pdf(sample(probe, ".rtf"), probe / "cache")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"LibreOffice cannot convert here: {exc}")


@pytest.mark.parametrize("suffix", [".rtf", ".odt", ".doc"])
def test_an_office_file_converts_to_the_cached_pdf(tmp_path, can_convert, suffix):
    cached = convert_office_to_pdf(sample(tmp_path, suffix), tmp_path / "cache")
    assert cached == tmp_path / "cache" / "converted.pdf"
    assert cached.read_bytes().startswith(b"%PDF")
