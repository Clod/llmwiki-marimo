"""libreoffice_can_convert: the converter counts as usable only when it converts.
The subprocess is faked; LibreOffice is never started."""

import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from domain.ingestion import extractor


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(extractor, "_probe_results", {})
    monkeypatch.setattr(extractor, "check_libreoffice", lambda: "/usr/bin/soffice")


def fake_run(calls, *, write_pdf, returncode=0, stderr=b""):
    def run(cmd, **kwargs):
        calls.append(cmd)
        if write_pdf:
            outdir = Path(cmd[cmd.index("--outdir") + 1])
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / "probe.pdf").write_bytes(b"%PDF-1.4")
        return SimpleNamespace(returncode=returncode, stderr=stderr)
    return run


def test_a_libreoffice_that_writes_a_pdf_is_usable(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run(calls, write_pdf=True))
    assert extractor.libreoffice_can_convert() is True
    assert "--convert-to" in calls[0] and calls[0][-1].endswith("probe.docx")


def test_a_libreoffice_that_exits_cleanly_but_writes_no_pdf_is_not_usable(monkeypatch):
    # The partial install seen in practice: core without Writer, "source file could not be loaded".
    monkeypatch.setattr(subprocess, "run", fake_run([], write_pdf=False, stderr=b"source file could not be loaded"))
    assert extractor.libreoffice_can_convert() is False


def test_a_failing_or_hanging_libreoffice_is_not_usable(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run([], write_pdf=False, returncode=1))
    assert extractor.libreoffice_can_convert() is False

    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(extractor, "_probe_results", {})
    monkeypatch.setattr(subprocess, "run", hang)
    assert extractor.libreoffice_can_convert() is False


def test_the_probe_runs_once_per_process(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run(calls, write_pdf=True))
    extractor.libreoffice_can_convert()
    extractor.libreoffice_can_convert()
    assert len(calls) == 1


def test_no_executable_means_no_probe(monkeypatch):
    monkeypatch.setattr(extractor, "check_libreoffice", lambda: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("ran"))
    assert extractor.libreoffice_can_convert() is False


def test_the_probe_document_is_a_well_formed_docx(monkeypatch):
    seen = {}

    def run(cmd, **kwargs):
        with zipfile.ZipFile(cmd[-1]) as zf:
            seen["names"] = set(zf.namelist())
            assert zf.testzip() is None
        return SimpleNamespace(returncode=1, stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    extractor.libreoffice_can_convert()
    assert seen["names"] == {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
