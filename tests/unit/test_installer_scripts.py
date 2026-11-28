"""The installer scripts (`install.sh`, `install.ps1`, `install.cmd`): syntax, encoding,
line endings, and the same steps in the two versions. The Windows path cannot run here;
these checks catch what breaks it without running it."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def steps(text: str, pattern: str) -> list[tuple[str, str]]:
    return re.findall(pattern, text, flags=re.MULTILINE)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is missing")
def test_install_sh_parses():
    assert subprocess.run(["bash", "-n", str(ROOT / "install.sh")]).returncode == 0


def test_install_ps1_is_ascii_with_a_bom_and_crlf():
    raw = (ROOT / "install.ps1").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")        # Windows PowerShell 5.1 reads UTF-8 only with a BOM
    body = raw[3:]
    assert body.isascii() and b"\r\n" in body and b"\n" not in body.replace(b"\r\n", b"")


def test_install_cmd_is_ascii_with_crlf_and_runs_install_ps1():
    raw = (ROOT / "install.cmd").read_bytes()
    assert raw.isascii() and b"\n" not in raw.replace(b"\r\n", b"")
    assert b'-File "%~dp0install.ps1"' in raw


def test_install_sh_has_lf_endings():
    assert b"\r\n" not in (ROOT / "install.sh").read_bytes()


def test_the_two_installers_have_the_same_steps():
    sh = steps((ROOT / "install.sh").read_text(encoding="utf-8"), r'^step (\d) "([^"]+)"')
    ps1 = steps((ROOT / "install.ps1").read_text(encoding="utf-8-sig"), r'^Step (\d) "([^"]+)"')
    assert sh == ps1 and [n for n, _ in sh] == ["1", "2", "3", "4", "5", "6"]


def test_the_installers_call_the_python_steps():
    for name in ("install.sh", "install.ps1"):
        text = (ROOT / name).read_text(encoding="utf-8-sig")
        for command in ("check", "wikis", "finish"):
            assert re.search(rf"install_steps\.py\"?,? \"?{command}", text), (name, command)


def test_gitattributes_keeps_the_line_endings_of_the_installer():
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for rule in ("*.sh text eol=lf", "*.cmd text eol=crlf", "*.ps1 text eol=crlf"):
        assert rule in text
