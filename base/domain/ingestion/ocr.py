"""OCR for PDFs with no text layer.

`extract_pdf` reads the text layer of a PDF. A scanned PDF has none, so its pages
come back empty. This module decides which pages need OCR and runs the engine
chosen in the Settings screen or in `.env` (`OCR_ENGINE`): `none`, `tesseract` or
`vision`. The settings are read from `config.settings` at each call, so a change saved
in the Settings screen applies without a restart.

An engine implements `OcrEngine.run`: it receives the PDF path, the page numbers
to read and the wiki language, and returns `(page_number, markdown)` pairs, the
shape `extract_pdf` returns. A new engine is one class and one entry in
`_ENGINES`; nothing outside this module changes.

A DOCX never goes through OCR: LibreOffice writes its text as text.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Protocol

from .pdf_extract import extract_pdf

logger = logging.getLogger(__name__)

ENGINE_NONE = "none"
ENGINE_TESSERACT = "tesseract"
ENGINE_VISION = "vision"
ENGINE_NAMES = (ENGINE_NONE, ENGINE_TESSERACT, ENGINE_VISION)

# A page needs OCR when its extracted text has fewer than this many characters
# that are not whitespace. A page number, a running header or a stray mark has
# fewer; the shortest real paragraph has more.
MIN_CHARS_PER_PAGE = 20

# A document needs OCR when at least this share of its pages needs it. One blank
# separator page in a long text PDF stays below the limit; a scan with a few
# text pages (a typed cover) stays above it.
MAX_BLANK_PAGE_SHARE = 0.25

# Tesseract language codes by wiki language (ISO 639-1).
_TESSERACT_LANGUAGES = {"es": "spa", "en": "eng"}

_OCRMYPDF_TIMEOUT_SECONDS = 1800
_VISION_RENDER_SCALE = 2.0  # 72 dpi x 2 = 144 dpi: legible for a model, small enough to upload

Progress = Callable[[str], None]

_IMAGE_REFERENCE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


class OcrNotConfiguredError(RuntimeError):
    """A PDF needs OCR and `OCR_ENGINE` is `none`."""

    def __init__(self, filename: str = "", pages: int = 0):
        super().__init__(
            f"'{filename}' has no text layer in {pages} page(s): it looks like a scanned PDF, "
            "and no OCR engine is chosen. Choose the engine in the Settings screen "
            "(block Models, OCR engine), or set OCR_ENGINE in .env, to one of:\n"
            "  tesseract  local OCR with ocrmypdf and Tesseract (install them first)\n"
            "  vision     the vision model of the Settings screen reads each page\n"
            "Then ingest the file again."
        )


class OcrEngineUnavailableError(RuntimeError):
    """The engine in force cannot run: a program or a setting is missing."""


# ── Detection ─────────────────────────────────────────────────────────────────

def page_needs_ocr(markdown: str) -> bool:
    """True when the page text has fewer than MIN_CHARS_PER_PAGE characters.

    Image references that the extractor writes (`![image](...)`) are not text.
    """
    text = _IMAGE_REFERENCE.sub("", markdown or "")
    return len("".join(text.split())) < MIN_CHARS_PER_PAGE


def pages_needing_ocr(pages: list[tuple[int, str]]) -> list[int]:
    """The numbers of the pages the document needs to run OCR on.

    Empty when the document as a whole does not need OCR: fewer than
    MAX_BLANK_PAGE_SHARE of its pages lack text. Otherwise every page that
    lacks text, so a mixed PDF runs OCR only on its scanned pages.
    """
    if not pages:
        return []
    blank = [number for number, markdown in pages if page_needs_ocr(markdown)]
    if len(blank) / len(pages) < MAX_BLANK_PAGE_SHARE:
        return []
    return blank


# ── Engines ───────────────────────────────────────────────────────────────────

class OcrEngine(Protocol):
    name: str
    label: str  # for the console and the status line: "Tesseract"
    supports_pages: bool  # True when the engine reads only the pages it is given

    def run(
        self, pdf_path: Path, pages: list[int], language: str, *,
        cache_dir: Path, progress: Progress | None = None,
    ) -> list[tuple[int, str]]:
        """Read `pages` (1-based) of `pdf_path`; return `(page_number, markdown)` pairs.

        `cache_dir` is the document's cache directory, for an engine that keeps a
        file there. `progress` receives one line per event worth showing.
        """


class TesseractEngine:
    """ocrmypdf with Tesseract, run as an external program.

    Writes `ocr.pdf` (the input with a text layer) in the cache directory, then
    `extract_pdf` reads it, as it reads any PDF. ocrmypdf reads only the pages it
    is given and copies the others unchanged.
    """

    name = ENGINE_TESSERACT
    label = "Tesseract"
    supports_pages = True

    def run(self, pdf_path, pages, language, *, cache_dir, progress=None):
        ocrmypdf = shutil.which("ocrmypdf")
        if not ocrmypdf or not shutil.which("tesseract"):
            raise OcrEngineUnavailableError(
                "OCR_ENGINE=tesseract needs ocrmypdf and tesseract on the PATH. "
                "macOS: brew install ocrmypdf tesseract tesseract-lang. "
                "Debian and Ubuntu: sudo apt-get install ocrmypdf tesseract-ocr tesseract-ocr-spa.")
        cache_dir.mkdir(parents=True, exist_ok=True)
        output = cache_dir / "ocr.pdf"
        command = [
            ocrmypdf, "--skip-text", "--pages", ",".join(str(p) for p in sorted(pages)),
            "-l", tesseract_language(language), str(pdf_path), str(output),
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=_OCRMYPDF_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"ocrmypdf timed out after {_OCRMYPDF_TIMEOUT_SECONDS} seconds") from exc
        if result.returncode != 0:
            raise RuntimeError(f"ocrmypdf failed (exit {result.returncode}): "
                               f"{result.stderr.decode(errors='replace').strip()[:300]}")
        wanted = set(pages)
        return [(number, markdown) for number, markdown in extract_pdf(str(output)) if number in wanted]


_VISION_PROMPT = (
    "Transcribe the text of this scanned page as markdown. The page is written in {language}. "
    "Keep headings, lists and tables as markdown. Reply with the page text only: "
    "no comment, no introduction, no description of the image."
)

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n\s*```\s*$", re.DOTALL)


def strip_fence(text: str) -> str:
    """The text without a markdown code fence that wraps all of it."""
    match = _FENCE.match(text or "")
    return (match.group(1) if match else (text or "")).strip()


class VisionEngine:
    """A model with vision, through an OpenAI-compatible API.

    Each page is rendered to a PNG with pypdfium2 and sent in one request.
    A page that fails is reported and left empty; the other pages continue.
    """

    name = ENGINE_VISION
    label = "modelo externo"
    supports_pages = True

    def __init__(self, base_url: str, api_key: str, model: str, client=None):
        self.base_url, self.api_key, self.model = base_url, api_key, model
        self._client = client

    def client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def run(self, pdf_path, pages, language, *, cache_dir, progress=None):
        import pypdfium2

        results: list[tuple[int, str]] = []
        failures = 0
        document = pypdfium2.PdfDocument(str(pdf_path))
        try:
            for number in sorted(pages):
                try:
                    png = _render_png(document[number - 1])
                    results.append((number, self._read_page(png, language)))
                except Exception as exc:  # one page fails alone
                    failures += 1
                    logger.warning("OCR failed on page %d of %s: %s", number, pdf_path.name, exc)
                    if progress:
                        progress(f"⚠️ OCR falló en la página {number} de {pdf_path.name}: {str(exc)[:200]}")
                    results.append((number, ""))
        finally:
            document.close()
        if pages and failures == len(pages):
            raise RuntimeError(f"OCR failed on every page of {pdf_path.name}")
        return results

    def _read_page(self, png: bytes, language: str) -> str:
        data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        response = self.client().chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": _VISION_PROMPT.format(language=language_name(language))},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}],
        )
        return strip_fence(response.choices[0].message.content or "")


def _render_png(page) -> bytes:
    image = page.render(scale=_VISION_RENDER_SCALE).to_pil()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def tesseract_language(language: str) -> str:
    """The Tesseract language pack for a wiki language; English for an unknown one."""
    return _TESSERACT_LANGUAGES.get(language, "eng")


def language_name(language: str) -> str:
    from domain.i18n import get_locale

    return get_locale(language).name_native


# ── Settings → engine ─────────────────────────────────────────────────────────

# The groups of settings the vision engine reads, in order: the first value that is not
# blank wins. `VISION_*` is the vision model of the Settings screen.
_VISION_FALLBACK = ("OCR", "VISION", "WIKI_LLM", "LLM")


def vision_config(settings=None) -> tuple[str, str, str]:
    """(base_url, api_key, model) of the vision engine. Each value is the first one that
    is not blank among OCR_*, VISION_*, WIKI_LLM_* and LLM_*. A group missing from
    `settings` counts as blank."""
    if settings is None:
        from config import settings as default_settings

        settings = default_settings

    def first(field: str) -> str:
        for group in _VISION_FALLBACK:
            value = (getattr(settings, f"{group}_{field}", "") or "").strip()
            if value:
                return value
        return ""

    return first("BASE_URL"), first("API_KEY"), first("MODEL")


def engine_name(settings=None) -> str:
    """The engine in force; an unknown value counts as `none`."""
    if settings is None:
        from config import settings as default_settings

        settings = default_settings
    name = (settings.OCR_ENGINE or ENGINE_NONE).strip().lower()
    return name if name in ENGINE_NAMES else ENGINE_NONE


def get_engine(settings=None) -> OcrEngine | None:
    """The engine `OCR_ENGINE` selects, or None for `none`."""
    name = engine_name(settings)
    if name == ENGINE_TESSERACT:
        return TesseractEngine()
    if name == ENGINE_VISION:
        base_url, api_key, model = vision_config(settings)
        missing = [k for k, v in (("OCR_BASE_URL", base_url), ("OCR_API_KEY", api_key), ("OCR_MODEL", model)) if not v]
        if missing:
            raise OcrEngineUnavailableError(
                f"OCR_ENGINE=vision needs {', '.join(missing)} "
                "(or the VISION_*, WIKI_LLM_* or LLM_* value it falls back to) in .env.")
        return VisionEngine(base_url, api_key, model)
    return None


def apply_ocr(
    pdf_path: Path, pages: list[tuple[int, str]], cache_dir: Path, language: str = "en", *,
    progress: Progress | None = None, engine: OcrEngine | None = None,
) -> tuple[list[tuple[int, str]], str | None]:
    """Run OCR on the pages of a PDF that need it.

    Returns the pages with the OCR text in place of the empty ones, and the name
    of the engine that ran (None when the document did not need OCR). Raises
    OcrNotConfiguredError when it needs OCR and the engine is `none`.
    """
    # An `ocr.pdf` from an earlier ingestion of this document must not outlive it.
    (cache_dir / "ocr.pdf").unlink(missing_ok=True)
    needed = pages_needing_ocr(pages)
    if not needed:
        return pages, None
    engine = engine or get_engine()
    if engine is None:
        raise OcrNotConfiguredError(pdf_path.name, len(needed))

    # An engine that cannot read a subset reads every page; the text of the pages
    # that already had text is kept from the first extraction.
    requested = needed if engine.supports_pages else [number for number, _ in pages]
    if progress:
        progress(f"🔍 OCR ({engine.label}) en {len(requested)} páginas de {pdf_path.name}")
    read = dict(engine.run(pdf_path, requested, language, cache_dir=cache_dir, progress=progress))
    merged = [(number, read[number] if number in needed and number in read else markdown)
              for number, markdown in pages]
    return merged, engine.name


# ── Status ────────────────────────────────────────────────────────────────────

def _tesseract_languages(tesseract: str) -> set[str]:
    try:
        result = subprocess.run([tesseract, "--list-langs"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return set()
    # The first line is a heading: "List of available languages (3):".
    return {line.strip() for line in result.stdout.splitlines()[1:] if line.strip()}


def engine_status(language: str = "en", settings=None) -> dict[str, str | bool]:
    """The OCR engine in force and whether it can run.

    Keys: `engine` (`none`, `tesseract` or `vision`), `label` (the value of the
    "OCR" entry of the status line: "no", "Tesseract", "modelo externo (<model>)"),
    `usable`, and `problem` (what is missing and how to fix it; empty when
    usable). The API key is never part of the result.
    """
    name = engine_name(settings)
    if name == ENGINE_TESSERACT:
        pack = tesseract_language(language)
        ocrmypdf, tesseract = shutil.which("ocrmypdf"), shutil.which("tesseract")
        problem = ""
        if not ocrmypdf or not tesseract:
            absent = " and ".join(n for n, found in (("ocrmypdf", ocrmypdf), ("tesseract", tesseract)) if not found)
            problem = (f"{absent} not installed. macOS: brew install ocrmypdf tesseract tesseract-lang. "
                       "Debian and Ubuntu: sudo apt-get install ocrmypdf tesseract-ocr tesseract-ocr-spa.")
        elif pack not in _tesseract_languages(tesseract):
            problem = (f"The Tesseract language pack '{pack}' is not installed. "
                       f"Debian and Ubuntu: sudo apt-get install tesseract-ocr-{pack}. "
                       "macOS: brew install tesseract-lang.")
        return {"engine": name, "label": "Tesseract", "usable": not problem, "problem": problem}
    if name == ENGINE_VISION:
        base_url, api_key, model = vision_config(settings)
        absent = [k for k, v in (("OCR_BASE_URL", base_url), ("OCR_API_KEY", api_key), ("OCR_MODEL", model)) if not v]
        problem = (f"Missing {', '.join(absent)}. Set it in .env "
                   "(or the VISION_*, WIKI_LLM_* or LLM_* value it falls back to)."
                   if absent else "")
        return {"engine": name, "label": f"{VisionEngine.label} ({model or '?'})",
                "usable": not problem, "problem": problem}
    return {"engine": ENGINE_NONE, "label": "no", "usable": True, "problem": ""}
