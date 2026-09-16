"""Tests for the Java-runtime preflight in extraction and in the pipeline.

opendataloader-pdf runs a bundled .jar through the `java` command, and a DOCX is
converted to PDF before that same extractor reads it, so a missing Java runtime
breaks both file types. Before the preflight the failure surfaced as a
subprocess error raised inside the dependency. No LLM.
"""

from pathlib import Path

import pytest

from domain.ingestion import extractor
from domain.ingestion.extractor import JavaNotInstalledError, check_java, extract


def test_check_java_finds_the_executable_on_path(monkeypatch):
    monkeypatch.setattr(extractor.shutil, "which", lambda name: "/usr/bin/java")
    assert check_java() == "/usr/bin/java"


def test_check_java_falls_back_to_java_home(monkeypatch, tmp_path):
    """A runtime installed but never added to PATH still counts."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "java").write_text("")
    monkeypatch.setattr(extractor.shutil, "which", lambda name: None)
    monkeypatch.setenv("JAVA_HOME", str(tmp_path))
    assert check_java() == str(bin_dir / "java")


def test_check_java_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(extractor.shutil, "which", lambda name: None)
    monkeypatch.delenv("JAVA_HOME", raising=False)
    assert check_java() is None


@pytest.mark.parametrize("suffix", [".pdf", ".docx"])
def test_extract_refuses_without_a_java_runtime(monkeypatch, tmp_path, suffix):
    """Both file types reach the .jar, so both fail the same way."""
    monkeypatch.setattr(extractor, "check_java", lambda: None)
    document = tmp_path / f"documento{suffix}"
    document.write_bytes(b"")

    with pytest.raises(JavaNotInstalledError) as excinfo:
        extract(document, tmp_path / "cache")

    message = str(excinfo.value)
    assert document.name in message
    assert "Java runtime is required" in message


def test_unsupported_extension_still_reports_the_extension(monkeypatch, tmp_path):
    """The type check runs before the Java check, so the message stays precise."""
    monkeypatch.setattr(extractor, "check_java", lambda: None)
    with pytest.raises(ValueError) as excinfo:
        extract(Path(tmp_path / "notas.txt"), tmp_path / "cache")
    assert ".txt" in str(excinfo.value)


def test_pipeline_reports_the_missing_runtime_as_a_failed_ingest(monkeypatch, tmp_path):
    """The ingest app shows this message instead of a subprocess traceback."""
    from domain.ingestion import pipeline

    monkeypatch.setattr(pipeline, "check_java", lambda: None)
    document = tmp_path / "documento.pdf"
    document.write_bytes(b"%PDF-1.4")

    result = pipeline.ingest_file(
        file_path=document,
        db_path=str(tmp_path / "index.db"),
        workspace=tmp_path,
        llm_client=None,
        model="",
    )

    assert result.status == "failed"
    assert "Java runtime is required" in result.message


def test_extract_puts_the_java_home_runtime_on_path_for_the_extractor(monkeypatch, tmp_path):
    """check_java accepts a runtime found only through JAVA_HOME, but the
    dependency runs the bare `java` command against PATH. The extractor must
    make that runtime reachable, and restore PATH afterwards."""
    bin_dir = tmp_path / "jdk" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "java").write_text("")
    monkeypatch.setattr(extractor.shutil, "which", lambda name: None)
    monkeypatch.setenv("JAVA_HOME", str(tmp_path / "jdk"))
    monkeypatch.setenv("PATH", "/nonexistent")
    seen: list[str] = []

    def fake_extract_pdf(path: str):
        import os
        seen.append(os.environ["PATH"])
        return [(1, "text")]

    monkeypatch.setattr(extractor, "extract_pdf", fake_extract_pdf)
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")

    pages, parser = extract(pdf, tmp_path / "cache")

    import os
    assert pages == [(1, "text")] and parser == "opendataloader"
    assert seen == [str(bin_dir) + os.pathsep + "/nonexistent"]
    assert os.environ["PATH"] == "/nonexistent"


def test_extract_leaves_path_alone_when_java_is_already_on_it(monkeypatch, tmp_path):
    monkeypatch.setattr(extractor.shutil, "which", lambda name: "/usr/bin/java")
    monkeypatch.setenv("PATH", "/usr/bin")
    seen: list[str] = []

    def fake_extract_pdf(path: str):
        import os
        seen.append(os.environ["PATH"])
        return [(1, "text")]

    monkeypatch.setattr(extractor, "extract_pdf", fake_extract_pdf)
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")

    extract(pdf, tmp_path / "cache")

    assert seen == ["/usr/bin"]
