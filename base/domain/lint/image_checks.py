"""Lint checks on the images kept from PDF sources (domain/ingestion/images.py)."""

from pathlib import Path

from domain.ingestion.images import (
    DESC_SUFFIX, SKIP_REASONS, desc_name, document_dir, file_sha256, image_path, images_dir, links_in,
    read_description, read_figures,
)
from domain.tools.db import get_connection
from domain.wiki_settings import load_images_config, load_wiki_language
from .report import LintIssue


def _sources(db_path: str) -> list[dict]:
    with get_connection(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, filename, relative_path FROM documents "
            "WHERE source_kind = 'source' ORDER BY filename")]


def _source_pages(db_path: str, doc_id: str) -> list[str]:
    with get_connection(db_path) as conn:
        return [r["content"] or "" for r in conn.execute(
            "SELECT content FROM document_pages WHERE document_id = ? ORDER BY page", (doc_id,))]


def _wiki_pages(db_path: str, workspace: Path) -> list[tuple[str, str]]:
    """(page path as `/wiki/.../x.md`, text read from disk) of every wiki page."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT path, filename, relative_path FROM documents WHERE source_kind = 'wiki'"
        ).fetchall()
    pages = []
    for row in rows:
        file = workspace / row["relative_path"]
        if file.is_file():
            pages.append((row["path"] + row["filename"], file.read_text(encoding="utf-8")))
    return pages


def count_missing_links(text: str, workspace: Path) -> int:
    """The image links of `text` whose file does not exist. A link in the old form
    (`<stem>_images/<file>`) always counts: its folder is gone."""
    return sum(1 for m, old in links_in(text)
               if old or image_path(workspace, m.group("doc"), m.group("file")) is None)


def missing_image_check(db_path: str, workspace: Path) -> list[LintIssue]:
    """Stored image links whose file does not exist.

    Looks at the text of every source (`document_pages`) and at every wiki page.
    Indexes built before images were kept hold links such as
    `![image](<stem>_images/imageFile1.png)` that point to a folder deleted with
    the conversion; they count as missing.
    """
    issues = []
    for src in _sources(db_path):
        n = sum(count_missing_links(text, workspace) for text in _source_pages(db_path, src["id"]))
        if n:
            issues.append(LintIssue(
                check="missing_image", severity="warning",
                page=src["relative_path"],
                description=f"{n} image link(s) in the text of '{src['filename']}' point to files that do not exist",
                suggestion=(
                    "Extract the images of the source again (no model is called), or, when the "
                    "source file is gone, replace each link with a note."
                ),
            ))
    for page, text in _wiki_pages(db_path, workspace):
        n = count_missing_links(text, workspace)
        if n:
            issues.append(LintIssue(
                check="missing_image", severity="warning", page=page,
                description=f"{n} image link(s) in this page point to files that do not exist",
                suggestion=(
                    "Extract the images of the source again (no model is called), or, when the "
                    "source file is gone, replace each link with a note."
                ),
            ))
    return issues


def _wiki_links(db_path: str, workspace: Path) -> dict[str, set[str]]:
    """document id → files that wiki pages link to."""
    used: dict[str, set[str]] = {}
    for _, text in _wiki_pages(db_path, workspace):
        for m, old in links_in(text):
            if not old:
                used.setdefault(m.group("doc"), set()).add(m.group("file"))
    return used


def linked_files(db_path: str, workspace: Path, doc_id: str,
                 wiki_links: dict[str, set[str]] | None = None) -> set[str]:
    """The files of the images folder of `doc_id` that a link uses: a link in the
    text of the document or in any wiki page."""
    if wiki_links is None:
        wiki_links = _wiki_links(db_path, workspace)
    linked = set(wiki_links.get(doc_id, ()))
    for text in _source_pages(db_path, doc_id):
        linked.update(m.group("file") for m, old in links_in(text) if not old)
    return linked


def orphan_image_check(db_path: str, workspace: Path) -> list[LintIssue]:
    """Files in a document's images folder that no link uses.

    A link counts when it is in the text of the document or in any wiki page.
    The usual cause is a changed source that was ingested again with fewer images.
    """
    wiki_links = _wiki_links(db_path, workspace)
    issues = []
    for src in _sources(db_path):
        folder = images_dir(workspace, src["id"])
        if not folder.is_dir():
            continue
        linked = linked_files(db_path, workspace, src["id"], wiki_links)
        for file in sorted(f.name for f in folder.iterdir() if f.is_file()):
            if file.endswith(DESC_SUFFIX):
                # A description file belongs to its image: it is an orphan only when the image is gone.
                if not (folder / file.removesuffix(DESC_SUFFIX)).is_file():
                    issues.append(LintIssue(
                        check="orphan_image", severity="info", page=src["relative_path"],
                        description=f"The description '{file}' of '{src['filename']}' belongs to an image that no longer exists",
                        suggestion="Delete the file.", target=file,
                    ))
            elif file not in linked:
                issues.append(LintIssue(
                    check="orphan_image", severity="info", page=src["relative_path"],
                    description=f"The image '{file}' of '{src['filename']}' is not used by any link",
                    suggestion="Delete the file.", target=file,
                ))
    return issues


def _image_descriptions(workspace: Path, doc_id: str) -> list[tuple[dict, Path]]:
    """(figure entry, image path) for every kept image of a document that exists."""
    folder = document_dir(workspace, doc_id) / "images"
    return [(fig, folder / fig["file"]) for fig in read_figures(document_dir(workspace, doc_id))
            if image_path(workspace, doc_id, fig["file"])]


def image_without_description_check(db_path: str, workspace: Path) -> list[LintIssue]:
    """Kept images with no description, when the wiki describes its images.

    Report only. With `[images] describe = true`, an image counts when it has no
    description file and its entry of `figures.json` gives no reason for skipping it
    (`too_small`, `decorative`, `duplicate`, `over_limit`, `failed`). The usual cause:
    the descriptions were turned on after the sources were ingested. The action
    "Describe the images" of the Maintain screen describes them, after a confirmation
    that states how many images and which model.
    """
    if not load_images_config(workspace).describe:
        return []
    issues = []
    for src in _sources(db_path):
        n = sum(1 for fig, path in _image_descriptions(workspace, src["id"])
                if not path.with_name(desc_name(fig["file"])).is_file()
                and fig.get("description") not in SKIP_REASONS)
        if n:
            issues.append(LintIssue(
                check="image_without_description", severity="info", page=src["relative_path"],
                description=f"{n} image(s) of '{src['filename']}' have no description",
                suggestion="Use the action \"Describe the images\" of the Maintain screen; it asks for confirmation "
                           "because each description is one call to the vision model.",
            ))
    return issues


def stale_image_description_check(db_path: str, workspace: Path) -> list[LintIssue]:
    """Descriptions made with another prompt, or of another image.

    Report only. A description is stale when its `prompt_sha256` differs from the hash of
    the current `prompts/image_description.<lang>.md`, or its `image_sha256` differs from
    the hash of the image. Checked whether or not `describe` is on: the files are there.
    """
    from domain.ingestion.describe import prompt_sha256

    current = prompt_sha256(load_wiki_language(workspace))
    issues = []
    for src in _sources(db_path):
        n = 0
        for fig, path in _image_descriptions(workspace, src["id"]):
            desc = read_description(path.with_name(desc_name(fig["file"])))
            if desc is None:
                continue
            if desc.get("prompt_sha256") != current or desc.get("image_sha256") != file_sha256(path):
                n += 1
        if n:
            issues.append(LintIssue(
                check="stale_image_description", severity="info", page=src["relative_path"],
                description=f"{n} description(s) of images of '{src['filename']}' are stale: the description prompt "
                            "or the image changed since they were made",
                suggestion="Use the action \"Describe the images\" of the Maintain screen to describe them again.",
            ))
    return issues
