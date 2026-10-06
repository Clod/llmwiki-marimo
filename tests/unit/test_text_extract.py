"""The .md and .txt extractor, and how extract() dispatches each extension.

No model, no Java, no LibreOffice: the text formats are read directly, and the
PDF and office paths are replaced.
"""

import codecs
from pathlib import Path

import pytest

from domain.ingestion import extractor, pipeline
from domain.ingestion.extractor import extract
from domain.ingestion.formats import OFFICE_EXTENSIONS, SUPPORTED_EXTENSIONS, TEXT_EXTENSIONS
from domain.ingestion.text_extract import PAGE_CHARS, extract_text_file, split_pages


def write(tmp_path: Path, name: str, data: bytes | str) -> Path:
    path = tmp_path / name
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path


# ── encodings ────────────────────────────────────────────────────────────────

def test_utf8_is_read_and_recorded(tmp_path):
    pages, parser = extract_text_file(write(tmp_path, "a.txt", "Canción de acción\n"))
    assert parser == "text:utf-8"
    assert pages == [(1, "Canción de acción")]


def test_utf8_with_a_bom_drops_the_bom_and_is_recorded(tmp_path):
    pages, parser = extract_text_file(write(tmp_path, "a.md", codecs.BOM_UTF8 + "# Título\n".encode()))
    assert parser == "text:utf-8-sig"
    assert pages == [(1, "# Título")]


def test_cp1252_is_the_last_resort(tmp_path):
    pages, parser = extract_text_file(write(tmp_path, "a.txt", "Año nuevo — “comillas”".encode("cp1252")))
    assert parser == "text:cp1252"
    assert pages == [(1, "Año nuevo — “comillas”")]


def test_bytes_that_are_no_text_fail_clearly(tmp_path):
    # 0x81 is undefined in cp1252 and invalid as UTF-8.
    with pytest.raises(RuntimeError, match="not valid UTF-8 or cp1252"):
        extract_text_file(write(tmp_path, "a.txt", b"\x81\x8d\x8f"))


# ── pages ────────────────────────────────────────────────────────────────────

def paragraphs(n: int, size: int = 900) -> str:
    return "\n\n".join(f"P{i} " + "x" * size for i in range(n))


def test_a_short_txt_is_one_page_numbered_from_one(tmp_path):
    pages, _ = extract_text_file(write(tmp_path, "a.txt", "uno\n\ndos\r\n\r\ntres"))
    assert pages == [(1, "uno\n\ndos\n\ntres")]


def test_a_txt_splits_at_paragraph_boundaries_near_4000_characters(tmp_path):
    pages, _ = extract_text_file(write(tmp_path, "a.txt", paragraphs(10)))
    assert [n for n, _ in pages] == list(range(1, len(pages) + 1))
    assert len(pages) > 1
    for _, text in pages:
        assert len(text) <= PAGE_CHARS
        assert all(p.startswith("P") and p.endswith("x") for p in text.split("\n\n"))  # no paragraph cut
    # Nothing lost, nothing repeated.
    assert "\n\n".join(t for _, t in pages) == paragraphs(10)


def test_a_paragraph_longer_than_a_page_is_never_cut():
    long = "y" * (PAGE_CHARS * 2)
    pages = split_pages(f"corto\n\n{long}\n\notro", markdown=False)
    assert pages == ["corto", long, "otro"]


def test_a_md_starts_a_page_at_each_level_1_and_2_heading_only():
    text = "# Uno\n\ntexto\n\n## Dos\n\nmás\n\n### Tres\n\nsigue en la página de Dos\n\n# Cuatro\n\nfin"
    pages = split_pages(text, markdown=True)
    assert pages == ["# Uno\n\ntexto", "## Dos\n\nmás\n\n### Tres\n\nsigue en la página de Dos", "# Cuatro\n\nfin"]


def test_a_heading_glued_to_the_previous_line_still_opens_a_page():
    assert split_pages("texto\n## Dos\nmás", markdown=True) == ["texto", "## Dos\nmás"]


def test_a_hash_line_in_a_txt_or_a_code_fence_is_not_a_heading():
    assert split_pages("# nota\n\notra", markdown=False) == ["# nota\n\notra"]
    fenced = "# Uno\n\n```\n# comentario\n\nsigue el bloque\n```\n\nfin"
    assert split_pages(fenced, markdown=True) == [fenced]


def test_a_long_md_section_splits_inside_and_the_next_heading_starts_a_page(tmp_path):
    text = "# Largo\n\n" + paragraphs(8) + "\n\n## Corto\n\nfin"
    pages, _ = extract_text_file(write(tmp_path, "a.md", text))
    assert len(pages) >= 3
    assert pages[0][1].startswith("# Largo")
    assert pages[-1] == (len(pages), "## Corto\n\nfin")


# ── markdown and front-matter ────────────────────────────────────────────────

def test_a_md_keeps_its_markdown_and_loses_its_front_matter(tmp_path):
    source = "---\ntitle: Notas\ntags: [a]\n---\n# Notas\n\n- **uno**\n- dos\n"
    pages, parser = extract_text_file(write(tmp_path, "notas.md", source))
    assert parser == "text:utf-8"
    assert pages == [(1, "# Notas\n\n- **uno**\n- dos")]
    assert "title:" not in pages[0][1]


def test_front_matter_is_dropped_after_a_bom_too(tmp_path):
    data = codecs.BOM_UTF8 + b"---\ntitle: x\n---\ncuerpo\n"
    assert extract_text_file(write(tmp_path, "n.md", data))[0] == [(1, "cuerpo")]


def test_a_txt_keeps_a_leading_dashes_block(tmp_path):
    pages, _ = extract_text_file(write(tmp_path, "n.txt", "---\nno es front-matter\n---\ncuerpo"))
    assert "no es front-matter" in pages[0][1]


# ── empty files ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["a.md", "a.txt"])
@pytest.mark.parametrize("content", ["", "  \n\t\n\r\n", "﻿"])
def test_an_empty_or_blank_file_fails_clearly(tmp_path, name, content):
    with pytest.raises(RuntimeError, match="is empty or contains only whitespace"):
        extract_text_file(write(tmp_path, name, content))


def test_a_md_of_only_front_matter_fails_clearly(tmp_path):
    with pytest.raises(RuntimeError, match="is empty or contains only whitespace"):
        extract_text_file(write(tmp_path, "a.md", "---\ntitle: x\n---\n\n"))


# ── extract() dispatch ───────────────────────────────────────────────────────

@pytest.fixture
def no_tools(monkeypatch):
    """Neither Java nor LibreOffice, and an extractor that fails if reached."""
    monkeypatch.setattr(extractor, "check_java", lambda: None)
    monkeypatch.setattr(extractor, "check_libreoffice", lambda: None)

    def boom(*_a, **_k):
        raise AssertionError("the PDF extractor must not run")

    monkeypatch.setattr(extractor, "extract_pdf", boom)


@pytest.mark.parametrize("name", ["a.md", "a.txt", "A.MD", "A.TXT"])
def test_extract_reads_md_and_txt_without_java_or_libreoffice(tmp_path, no_tools, name):
    pages, parser = extract(write(tmp_path, name, "hola"), tmp_path / "cache")
    assert pages == [(1, "hola")]
    assert parser == "text:utf-8"


def test_extract_a_pdf_goes_to_the_pdf_extractor(tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "check_java", lambda: "/usr/bin/java")
    monkeypatch.setattr(extractor, "extract_pdf", lambda path: [(1, f"pdf:{Path(path).name}")])
    assert extract(write(tmp_path, "a.pdf", b"%PDF"), tmp_path / "c") == ([(1, "pdf:a.pdf")], "opendataloader")


@pytest.mark.parametrize("suffix", sorted(OFFICE_EXTENSIONS))
def test_extract_an_office_file_converts_then_reads_the_pdf(tmp_path, monkeypatch, suffix):
    converted = []
    monkeypatch.setattr(extractor, "check_java", lambda: "/usr/bin/java")

    def convert(path, cache_dir):
        converted.append(path.name)
        return cache_dir / "converted.pdf"

    monkeypatch.setattr(extractor, "convert_office_to_pdf", convert)
    monkeypatch.setattr(extractor, "extract_pdf", lambda path: [(1, Path(path).name)])
    pages, parser = extract(write(tmp_path, f"a{suffix}", b"x"), tmp_path / "c")
    assert converted == [f"a{suffix}"]
    assert pages == [(1, "converted.pdf")] and parser == "libreoffice+opendataloader"


@pytest.mark.parametrize("suffix", [".pdf", *sorted(OFFICE_EXTENSIONS)])
def test_extract_still_needs_java_for_pdf_and_office(tmp_path, no_tools, suffix):
    with pytest.raises(extractor.JavaNotInstalledError):
        extract(write(tmp_path, f"a{suffix}", b"x"), tmp_path / "c")


def test_extract_refuses_other_extensions_and_lists_the_supported_ones(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        extract(write(tmp_path, "a.xlsx", b"x"), tmp_path / "c")
    assert ".xlsx" in str(excinfo.value) and ".md" in str(excinfo.value)


def test_the_old_converter_name_is_an_alias():
    assert extractor.convert_docx_to_pdf is extractor.convert_office_to_pdf


# ── the lists of formats ─────────────────────────────────────────────────────

def test_the_format_lists_are_defined_once():
    assert TEXT_EXTENSIONS == {".md", ".txt"}
    assert OFFICE_EXTENSIONS == {".docx", ".doc", ".odt", ".rtf"}
    assert SUPPORTED_EXTENSIONS == TEXT_EXTENSIONS | OFFICE_EXTENSIONS | {".pdf"}
    assert pipeline.SUPPORTED_EXTENSIONS is SUPPORTED_EXTENSIONS


# ── the pipeline ─────────────────────────────────────────────────────────────

@pytest.fixture
def pipeline_without_tools(monkeypatch):
    monkeypatch.setattr(pipeline, "check_java", lambda: None)
    monkeypatch.setattr(pipeline, "check_libreoffice", lambda: None)


def test_validation_accepts_md_and_txt_without_java_or_libreoffice(tmp_path, pipeline_without_tools):
    for name in ("a.md", "a.txt"):
        assert pipeline._validation_error(write(tmp_path, name, "x")) is None


@pytest.mark.parametrize("suffix", sorted(OFFICE_EXTENSIONS))
def test_validation_asks_for_libreoffice_for_each_office_format(tmp_path, pipeline_without_tools, suffix):
    assert "LibreOffice is required" in pipeline._validation_error(write(tmp_path, f"a{suffix}", "x"))


def test_validation_asks_for_java_for_a_pdf_and_lists_the_formats_of_a_refusal(tmp_path, pipeline_without_tools):
    assert "Java runtime is required" in pipeline._validation_error(write(tmp_path, "a.pdf", "x"))
    refusal = pipeline._validation_error(write(tmp_path, "a.xlsx", "x"))
    assert "'.xlsx'" in refusal and all(e in refusal for e in SUPPORTED_EXTENSIONS)


def test_the_scan_of_sources_takes_every_supported_extension(tmp_workspace, monkeypatch):
    sources = tmp_workspace.workspace / "sources"
    sources.mkdir(exist_ok=True)
    names = sorted(f"f{e}" for e in SUPPORTED_EXTENSIONS)
    for name in [*names, "skip.xlsx", "skip.png", ".hidden.md"]:
        (sources / name).write_bytes(b"x")
    (sources / "sub").mkdir()
    (sources / "sub" / "nested.txt").write_bytes(b"x")
    seen = []
    monkeypatch.setattr(pipeline, "ingest_file", lambda fp, *a, **k: seen.append(fp.name) or
                        pipeline.IngestResult(fp, "skipped", "x"))
    pipeline.scan_and_ingest(tmp_workspace.workspace, tmp_workspace.db_path, None, "m")
    assert sorted(seen) == sorted([*names, "nested.txt"])
