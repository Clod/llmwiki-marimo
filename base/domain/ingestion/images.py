"""Images of a PDF source: where they are kept and how the stored text links to them.

opendataloader-pdf writes the images of a PDF while it converts it. Ingestion
keeps them in the ingestion folder of the document, beside `converted.pdf`:

    .llmwiki/cache/local/<document id>/images/<file>

The stored text links to a kept image with a path relative to the workspace root,
so the link does not depend on the URLs of any user interface:

    ![Imagen, página 3](.llmwiki/cache/local/<document id>/images/imageFile4.png)

The alt text keeps the page number. Indexes built before images were kept hold
the old form, `![image](<stem>_images/imageFile4.png)`, which points to a folder
that no longer exists.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
from pathlib import Path

from .formats import IMAGE_EXTENSIONS

IMAGES_DIRNAME = "images"
FIGURES_FILENAME = "figures.json"
# The description of `<file>` is the file `<file>.desc.json`, beside the image.
DESC_SUFFIX = ".desc.json"
# The reasons `figures.json` gives for an image that has no description.
TOO_SMALL, DECORATIVE, DUPLICATE, OVER_LIMIT, FAILED = (
    "too_small", "decorative", "duplicate", "over_limit", "failed")
SKIP_REASONS = (TOO_SMALL, DECORATIVE, DUPLICATE, OVER_LIMIT, FAILED)
# The raster formats ingestion accepts as an image (a Markdown source, an image file).
RASTER_EXTENSIONS = IMAGE_EXTENSIONS
_CACHE_PARTS = (".llmwiki", "cache", "local")

# `![alt](.llmwiki/cache/local/<document id>/images/<file>)`.
IMAGE_LINK_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\("
    r"(?P<path>\.llmwiki/cache/local/(?P<doc>[0-9A-Za-z_-]+)/images/(?P<file>[\w.-]+))\)"
)
# The target of any markdown link or image that points to a kept image: `](<path>)`.
IMAGE_TARGET_RE = re.compile(
    r"\]\(\.llmwiki/cache/local/(?P<doc>[0-9A-Za-z_-]+)/images/(?P<file>[\w.-]+)\)"
)
# `![image](<stem>_images/<file>)`, as stored before images were kept.
OLD_IMAGE_LINK_RE = re.compile(
    r"!\[image\]\((?P<path>(?P<stem>[^)\n]*?)_images/(?P<file>[^)/\n]+))\)"
)
_PAGE_IN_ALT_RE = re.compile(r"(\d+)\s*$")
# `Imagen, página 3. <description>`: the alt text of a thumbnail that carries a description.
_PAGE_AT_START_RE = re.compile(r"^Imagen, página (\d+)\b")


def document_dir(workspace: Path, doc_id: str) -> Path:
    """The ingestion folder of a document."""
    return workspace.joinpath(*_CACHE_PARTS, doc_id)


def images_dir(workspace: Path, doc_id: str) -> Path:
    """The folder that holds the kept images of a document."""
    return document_dir(workspace, doc_id) / IMAGES_DIRNAME


def image_path(workspace: Path, doc_id: str, file: str) -> Path | None:
    """The kept image `file` of a document, or None when `file` is not a plain
    file name or does not exist in the images folder."""
    if not file or file in (".", "..") or "/" in file or "\\" in file or "\0" in file:
        return None
    path = images_dir(workspace, doc_id) / file
    return path if path.is_file() else None


def image_link(doc_id: str, file: str, page: int) -> str:
    """The markdown that embeds the kept image `file` found on `page`."""
    return f"![Imagen, página {page}]({link_base(doc_id)}/{file})"


def link_base(doc_id: str) -> str:
    return "/".join((*_CACHE_PARTS, doc_id, IMAGES_DIRNAME))


def kept_name(name: str) -> str:
    """`name` as a file name of the images folder: letters, digits, `_`, `.` and `-`
    (what `IMAGE_LINK_RE` accepts), with a plain extension."""
    clean = re.sub(r"[^\w.-]", "_", Path(name).name).lstrip(".") or "image"
    return clean


def unavailable_text(page: int | str) -> str:
    """The text that replaces a link to an image that cannot be recovered."""
    return f"[Imagen no disponible, página {page}]"


def alt_page(alt: str) -> int | None:
    """The page number kept at the end of an alt text, or None."""
    match = _PAGE_AT_START_RE.match(alt) or _PAGE_IN_ALT_RE.search(alt)
    return int(match.group(1)) if match else None


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def desc_name(file: str) -> str:
    """The name of the description file of the image `file`."""
    return file + DESC_SUFFIX


def read_description(path: Path) -> dict | None:
    """The content of a `.desc.json` file, or None when it is missing or damaged."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("answer"), dict) else None


def _kept_descriptions(folder: Path) -> dict[str, bytes]:
    """image sha256 → bytes of the description files of `folder`."""
    kept: dict[str, bytes] = {}
    if folder.is_dir():
        for path in folder.glob("*" + DESC_SUFFIX):
            data = read_description(path)
            if data and isinstance(data.get("image_sha256"), str):
                kept[data["image_sha256"]] = path.read_bytes()
    return kept


def replace_dir(source: Path, dest: Path) -> None:
    """Replace the folder `dest` with a copy of `source`.

    A description file (D4 of the image design) of the old folder stays with its
    image: when an image of the new folder has the sha256 a description holds,
    the description is written beside it, so extracting a source again costs no
    model call.
    """
    kept = _kept_descriptions(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest)
    if kept:
        for image in sorted(p for p in dest.iterdir() if p.is_file()):
            data = kept.get(file_sha256(image))
            if data is not None:
                (dest / desc_name(image.name)).write_bytes(data)


def image_size(path: Path) -> tuple[int, int]:
    """(width, height) in pixels of a PNG, JPEG, GIF or WebP file, read from its
    header; (0, 0) when the format is not recognised."""
    try:
        with path.open("rb") as fh:
            head = fh.read(32)
            if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
                return struct.unpack(">II", head[16:24])
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return struct.unpack("<HH", head[6:10])
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                kind = head[12:16]
                if kind == b"VP8X":
                    return (int.from_bytes(head[24:27], "little") + 1,
                            int.from_bytes(head[27:30], "little") + 1)
                if kind == b"VP8L":
                    bits = int.from_bytes(head[21:25], "little")
                    return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
                if kind == b"VP8 ":
                    return (struct.unpack("<H", head[26:28])[0] & 0x3FFF,
                            struct.unpack("<H", head[28:30])[0] & 0x3FFF)
            if head.startswith(b"\xff\xd8"):
                fh.seek(2)
                while True:
                    marker = fh.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        break
                    if marker[1] in (0xD8, 0x01) or 0xD0 <= marker[1] <= 0xD7:
                        continue
                    size = fh.read(2)
                    if len(size) < 2:
                        break
                    length = struct.unpack(">H", size)[0]
                    if marker[1] in range(0xC0, 0xD0) and marker[1] not in (0xC4, 0xC8, 0xCC):
                        data = fh.read(5)
                        height, width = struct.unpack(">HH", data[1:5])
                        return width, height
                    fh.seek(length - 2, 1)
    except (OSError, struct.error):
        pass
    return 0, 0


def drop_kept(doc_dir: Path) -> None:
    """Delete the images folder and `figures.json` of the document folder `doc_dir`, when
    they exist. An extraction that keeps no image calls it, so no image of an earlier
    extraction stays."""
    folder = doc_dir / IMAGES_DIRNAME
    if folder.is_dir():
        shutil.rmtree(folder)
    (doc_dir / FIGURES_FILENAME).unlink(missing_ok=True)


def read_figures(doc_dir: Path) -> list[dict]:
    """The entries of `figures.json` of the document folder `doc_dir`, as written,
    whether or not the files exist."""
    try:
        figures = json.loads((doc_dir / FIGURES_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [f for f in figures if isinstance(f, dict) and f.get("file")] if isinstance(figures, list) else []


def write_figures(doc_dir: Path, figures: list[dict]) -> None:
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / FIGURES_FILENAME).write_text(json.dumps(figures, ensure_ascii=False, indent=2), encoding="utf-8")


def enrich_figures(doc_dir: Path) -> list[dict]:
    """Complete `figures.json` of the document folder `doc_dir` with `sha256`,
    `width`, `height` and `description`.

    `description` is the name of the description file when the image has one, and
    empty otherwise (the describing step writes a reason there). The entries keep
    their order; an entry whose image file is gone gets empty values. Returns the
    figures; nothing is written for a document with no `figures.json`.
    """
    figures = read_figures(doc_dir)
    if not figures:
        return []
    folder = doc_dir / IMAGES_DIRNAME
    out = []
    for fig in figures:
        fig = dict(fig)
        path = folder / fig["file"]
        if path.is_file() and "/" not in fig["file"] and "\\" not in fig["file"]:
            fig["sha256"] = file_sha256(path)
            fig["width"], fig["height"] = image_size(path)
            fig["description"] = desc_name(fig["file"]) if (folder / desc_name(fig["file"])).is_file() else ""
        else:
            fig["sha256"], fig["width"], fig["height"], fig["description"] = "", 0, 0, ""
        out.append(fig)
    write_figures(doc_dir, out)
    return out


def links_in(text: str) -> list[tuple[re.Match[str], bool]]:
    """Every image link of `text` in either form, with True for the old form."""
    found = [(m, False) for m in IMAGE_LINK_RE.finditer(text or "")]
    found += [(m, True) for m in OLD_IMAGE_LINK_RE.finditer(text or "")]
    return sorted(found, key=lambda item: item[0].start())


def load_figures(workspace: Path, doc_id: str, *, with_descriptions: bool = False) -> list[dict]:
    """The kept images of a document, in document order: page, file and caption.

    Read from `figures.json`; an image whose file is not in the images folder
    is left out. Empty when the document has no kept images. With `with_descriptions`,
    an image that has a description file also carries its `answer` and `model`.
    """
    figures = [f for f in read_figures(document_dir(workspace, doc_id))
               if image_path(workspace, doc_id, f.get("file", ""))]
    if with_descriptions:
        for fig in figures:
            desc = read_description(images_dir(workspace, doc_id) / desc_name(fig["file"]))
            if desc:
                fig["answer"], fig["model"] = desc["answer"], str(desc.get("model", ""))
    return figures
