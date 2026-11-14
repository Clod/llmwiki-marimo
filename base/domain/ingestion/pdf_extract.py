"""PDF extraction via opendataloader-pdf.

Converts a PDF to per-page markdown for the ingestion pipeline. It reads the text
layer only: an image-only (scanned) PDF yields empty pages, and `ocr.py` fills
them in with the OCR engine chosen in `.env`.

Adapted from lucasastorian/llmwiki (api/services/pdf_extract.py), Apache-2.0.
See the NOTICE file at the project root for attribution.
"""

import json
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

from .images import FIGURES_FILENAME, image_link, replace_dir


def _element_to_markdown(el: dict, doc_id: str | None = None) -> str:
    """Convert a single JSON element to markdown.

    An image becomes a link to the kept copy when `doc_id` is given (see
    `domain.ingestion.images`); without it the image has no copy and is left out.
    """
    t = el.get("type", "")
    content = el.get("content", "")

    if t == "heading":
        level = max(1, min(el.get("heading level", 1), 6))
        prefix = "#" * level
        return f"{prefix} {content}"

    if t == "paragraph":
        return content

    if t == "list":
        lines = []
        for item in el.get("list items", []):
            lines.append(f"- {item.get('content', '')}")
            for child in item.get("kids", []):
                lines.append(f"  - {child.get('content', '')}")
        return "\n".join(lines)

    if t == "image":
        file = Path(el.get("source", "")).name
        if not (doc_id and file):
            return ""
        return image_link(doc_id, file, el.get("page number", 0))

    if t == "caption":
        return f"*{content}*" if content else ""

    # Skip headers, footers, and unknown types
    return ""


def _elements_to_pages(elements: list[dict], total_pages: int,
                       doc_id: str | None = None) -> list[tuple[int, str]]:
    """Group JSON elements by page number and reconstruct markdown per page.

    Returns a list of (page_num, markdown) for every page up to total_pages,
    using empty string for pages with no extractable content.
    """
    page_elements: dict[int, list[dict]] = defaultdict(list)

    for el in elements:
        page_num = el.get("page number")
        if page_num is None or el.get("type") in ("header", "footer"):
            continue
        page_elements[page_num].append(el)

    pages = []
    for page_num in range(1, total_pages + 1):
        parts = []
        for el in page_elements.get(page_num, []):
            md = _element_to_markdown(el, doc_id)
            if md:
                parts.append(md)
        pages.append((page_num, "\n\n".join(parts)))

    return pages


def _figures(elements: list[dict]) -> list[dict]:
    """The images of the document, in order: page, file and the caption that
    opendataloader-pdf linked to the image (empty when it found none)."""
    captions = {el.get("linked content id"): el.get("content", "").strip()
                for el in elements if el.get("type") == "caption"}
    return [
        {"page": el.get("page number", 0), "file": Path(el["source"]).name,
         "caption": captions.get(el.get("id"), "")}
        for el in elements if el.get("type") == "image" and el.get("source")
    ]


def extract_pdf(pdf_path: str, images_dir: Path | None = None) -> list[tuple[int, str]]:
    """Run opendataloader-pdf and return per-page markdown from structured JSON output.

    `images_dir` is the folder where the images of the PDF are kept,
    `.llmwiki/cache/local/<document id>/images/`. The folder is replaced by the
    images of this conversion, the text links to them, and `figures.json`
    (page, file and caption of each image) is written beside the folder. Without
    `images_dir` the images are not kept and the text has no link to them.

    Raises RuntimeError if extraction fails (Java missing, corrupt PDF, etc.).
    """
    try:
        import opendataloader_pdf  # deferred — only needed when actually processing a PDF
        doc_id = images_dir.parent.name if images_dir else None
        with tempfile.TemporaryDirectory() as extract_dir:
            opendataloader_pdf.convert(
                input_path=pdf_path,
                output_dir=extract_dir,
                format="json",
                quiet=True,
            )

            json_files = list(Path(extract_dir).glob("*.json"))
            if not json_files:
                raise RuntimeError("opendataloader-pdf produced no output")

            with open(json_files[0], encoding="utf-8") as f:
                data = json.load(f)

            elements = data.get("kids", [])
            if images_dir is not None:
                # The conversion names the folder `<stem>_images`; copy it before
                # the temporary folder is deleted.
                found = next((d for d in Path(extract_dir).glob("*_images") if d.is_dir()), None)
                if found is not None:
                    replace_dir(found, images_dir)
                elif images_dir.exists():
                    shutil.rmtree(images_dir)
                figures = _figures(elements) if found is not None else []
                images_dir.parent.mkdir(parents=True, exist_ok=True)
                (images_dir.parent / FIGURES_FILENAME).write_text(
                    json.dumps(figures, ensure_ascii=False, indent=2), encoding="utf-8")

        total_pages = data.get("number of pages", 0)
        return _elements_to_pages(elements, total_pages, doc_id)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"PDF extraction failed: {e}") from e
