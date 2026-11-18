"""Source deletion — removes a source document and cascades to DB children."""

import contextlib
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from domain.datasets.frontmatter import parse_frontmatter, render_frontmatter, split_frontmatter
from domain.ingestion.images import document_dir
from domain.repair.report import RepairResult
from domain.messages import M_

logger = logging.getLogger(__name__)


@dataclass
class SourceDeletion(RepairResult):
    """The outcome of a source deletion, with what it did, step by step, for a
    caller that reports the steps (the progress console of the web interface)."""

    filename: str = ""
    deleted_pages: tuple[str, ...] = ()   # the pages deleted: the summary and the pages citing only this source
    stale_pages: tuple[str, ...] = ()     # the citing pages kept and marked stale by this deletion
    edited_pages: tuple[str, ...] = ()    # the files of wiki/ rewritten: citation or link removed
    config_mentions: tuple[str, ...] = () # deleted page titles or paths that wiki_config.toml still names
    file_removed: bool = False            # the file left sources/


def _chat_origin(content: str) -> bool:
    """Whether the page header names the conversation among its sources
    (`sources: [{resource: chat}]`, a page saved from the chat)."""
    block, _ = split_frontmatter(content)
    if block is None:
        return False
    try:
        raw = parse_frontmatter(block).get("sources")
    except ValueError:
        return False
    for entry in raw if isinstance(raw, list) else []:
        resource = entry.get("resource") if isinstance(entry, dict) else entry
        if resource == "chat":
            return True
    return False


def without_source(content: str, filename: str) -> str:
    """`content` without any citation of the source `filename`.

    The three places a page cites a source (`references.update_references`) lose
    it: the entry of the header field `sources`, the bullet of the sources section
    («## Fuentes», «## Sources»), and the footnote `[^N]: <file>…` with its markers.
    A sources section left with no bullet goes with its heading. The text the
    source contributed to the page stays: nothing records which sentence came
    from which source.
    """
    from domain.tools.references import _CITATION_RE, _SOURCES_SECTION_RE, _parse_citation_filename, base_name

    target = base_name(filename)

    def _cites(raw: str) -> bool:
        return base_name(_parse_citation_filename(re.sub(r"^\[\^\w+\]:\s*", "", raw))[0]) == target

    block, body = split_frontmatter(content)
    if block is not None:
        try:
            fields = parse_frontmatter(block)
        except ValueError:
            fields = None
        sources = fields.get("sources") if fields else None
        if isinstance(sources, list):
            kept = [e for e in sources if base_name(str(e.get("resource") if isinstance(e, dict) else e)) != target]
            if len(kept) != len(sources):
                fields["sources"] = kept
                block = render_frontmatter(fields).rstrip("\n").removeprefix("---\n").removesuffix("\n---")

    # Footnotes: the definition line and every marker of that number.
    numbers = [m.group(0).split("]")[0][2:] for m in _CITATION_RE.finditer(body) if _cites(m.group(0))]
    for number in numbers:
        body = re.sub(rf"^\[\^{re.escape(number)}\]:.*\n?", "", body, flags=re.MULTILINE)
        body = body.replace(f"[^{number}]", "")

    def _section(m: re.Match) -> str:
        lines = m.group(0).split("\n")
        heading, rest = lines[0], lines[1:]
        rest = [line for line in rest
                if not (re.match(r"^\s*[-*]\s+\S", line) and _cites(re.sub(r"^\s*[-*]\s+", "", line).strip()))]
        if not any(re.match(r"^\s*[-*]\s+\S", line) for line in rest):
            return ""
        return "\n".join([heading, *rest])

    body = _SOURCES_SECTION_RE.sub(_section, body).rstrip() + "\n"
    if block is None:
        return body
    return f"---\n{block}\n---\n" + (body if body.startswith("\n") else "\n" + body)


def delete_source(
    db_path: str,
    workspace: Path,
    doc_id: str,
    *,
    also_delete_file: bool = False,
) -> SourceDeletion:
    """Remove a source document from the DB and optionally from disk.

    FK ON DELETE CASCADE handles document_pages, document_chunks, chunks_fts
    (via triggers), and document_references automatically.

    The wiki pages that cite the source are handled by what else they cite:
      - the summary page (source_document_id == doc_id) and every page whose only
        cited source is this one are deleted: nothing is left to support them.
        A page saved from the chat is kept: the conversation is another origin.
      - a page that also cites another source is kept, marked stale, and loses
        every citation of the deleted source (`without_source`). The text the
        source contributed stays; the answer checks are what detect it.

    No link to anything deleted is left in any markdown file of `wiki/`: not to a
    deleted page (`wiki_fs.delete_page`), nor to the source file, nor to the images
    of its ingestion folder (`wiki_fs.strip_links_to`). The generated aliases of the
    deleted pages go too. `wiki_config.toml`, written by hand, is not edited: the
    deleted titles and paths it still names are returned in `config_mentions`.
    No model is called. The whole deletion is one commit of the wiki.

    Returns a `SourceDeletion` (a `RepairResult`) describing the outcome and its steps.
    """
    from domain.tools.db import get_connection
    from domain.tools.git_ops import auto_commit
    from domain.tools.references import update_references
    from domain.tools.wiki_fs import delete_page, strip_links_to, write_page_content

    try:
        with get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT id, filename, relative_path, source_kind FROM documents WHERE id=?",
                (doc_id,),
            ).fetchone()

        if row is None:
            return SourceDeletion(
                check="delete_source",
                page=doc_id,
                action="failed",
                success=False,
                message=f"Document not found: {doc_id}",
            )

        if row["source_kind"] != "source":
            return SourceDeletion(
                check="delete_source",
                page=row["relative_path"],
                action="failed",
                success=False,
                message=f"Document is not a source (kind={row['source_kind']}): {row['filename']}",
            )

        filename = row["filename"]
        relative_path = row["relative_path"]

        # Classify the dependent wiki pages BEFORE the cascade removes document_references.
        with get_connection(db_path) as conn:
            summaries = {
                r["id"] for r in conn.execute(
                    "SELECT id FROM documents WHERE source_document_id=? AND source_kind='wiki'",
                    (doc_id,)).fetchall()
            }
            citing = {
                r["id"]: r for r in conn.execute(
                    "SELECT d.id, d.path, d.filename, d.content, d.title FROM document_references dr "
                    "JOIN documents d ON d.id = dr.source_document_id "
                    "WHERE dr.target_document_id=? AND dr.reference_type='cites' AND d.source_kind='wiki'",
                    (doc_id,)).fetchall()
            }
            only_this = set()
            for page_id, page in citing.items():
                others = conn.execute(
                    "SELECT COUNT(*) FROM document_references dr JOIN documents s "
                    "ON s.id = dr.target_document_id AND s.source_kind='source' "
                    "WHERE dr.source_document_id=? AND dr.reference_type='cites' AND dr.target_document_id != ?",
                    (page_id, doc_id)).fetchone()[0]
                if not others and not _chat_origin(page["content"] or ""):
                    only_this.add(page_id)
            delete_ids = summaries | only_this
            keep_ids = set(citing) - delete_ids
            pages = {
                r["id"]: r for r in conn.execute(
                    f"SELECT id, path, filename, title FROM documents WHERE id IN ({','.join('?' * len(delete_ids | keep_ids))})",
                    list(delete_ids | keep_ids)).fetchall()
            } if delete_ids | keep_ids else {}

        # The kept pages lose every citation of the source, then are marked stale.
        edited: set[str] = set()
        for page_id in sorted(keep_ids, key=lambda i: pages[i]["filename"]):
            page = pages[page_id]
            path = workspace / page["path"].lstrip("/") / page["filename"]
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            new_content = without_source(content, filename)
            if new_content != content:
                slug = page["filename"].removesuffix(".md")
                write_page_content(db_path, workspace, page["path"], slug, new_content)
                update_references(db_path, page_id, new_content, page["path"])
                edited.add(f"{page['path']}{page['filename']}")
        stale_pages: tuple[str, ...] = ()
        if keep_ids:
            placeholders = ",".join("?" * len(keep_ids))
            with get_connection(db_path) as conn:
                stale_pages = tuple(sorted(
                    f"{r['path']}{r['filename']}" for r in conn.execute(
                        f"SELECT path, filename FROM documents WHERE id IN ({placeholders})"
                        f" AND source_kind='wiki' AND stale_since IS NULL", list(keep_ids)).fetchall()))
                with conn:
                    conn.execute(
                        f"UPDATE documents SET stale_since=datetime('now')"
                        f" WHERE id IN ({placeholders}) AND source_kind='wiki' AND stale_since IS NULL",
                        list(keep_ids))
            if stale_pages:
                logger.info("Marked %d citing wiki page(s) stale", len(stale_pages))

        # Delete the pages that only this source supported: each one leaves no link behind.
        deleted_wiki: list[str] = []
        for page_id in sorted(delete_ids, key=lambda i: pages[i]["filename"]):
            page = pages[page_id]
            slug = page["filename"].removesuffix(".md")
            if delete_page(db_path, workspace, page["path"], slug):
                deleted_wiki.append(f"{page['path']}{slug}.md")
                logger.info("Deleted wiki page: %s%s.md", page["path"], slug)

        with get_connection(db_path) as conn:
            with conn:
                conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            # No link to the source file, nor to the images of its ingestion folder.
            ingestion_dir = document_dir(workspace, doc_id)
            targets = {relative_path}
            with contextlib.suppress(ValueError):
                targets.add(ingestion_dir.relative_to(workspace).as_posix() + "/")
            edited.update("/" + p for p in strip_links_to(conn, workspace, targets))

        # The ingestion folder holds the converted PDF and the kept images.
        if ingestion_dir.is_dir():
            shutil.rmtree(ingestion_dir, ignore_errors=True)

        file_removed = False
        if also_delete_file:
            physical = workspace / relative_path
            if physical.exists():
                physical.unlink()
                file_removed = True
                logger.info("Deleted physical file: %s", physical)

        mentions = _config_mentions(workspace, [pages[i] for i in delete_ids])

        notes: list[str] = []
        if deleted_wiki:
            notes.append(f"deleted {len(deleted_wiki)} derived wiki page(s)")
        if stale_pages:
            notes.append(f"marked {len(stale_pages)} citing page(s) stale")
        wiki_note = f"; {'; '.join(notes)}" if notes else ""
        auto_commit(workspace, f"delete source: {filename}")

        return SourceDeletion(
            check="delete_source",
            page=relative_path,
            action="deleted",
            success=True,
            message=f"Deleted source '{filename}'{wiki_note}",
            filename=filename,
            deleted_pages=tuple(deleted_wiki),
            stale_pages=stale_pages,
            edited_pages=tuple(sorted(edited - set(deleted_wiki))),
            config_mentions=mentions,
            file_removed=file_removed,
        )

    except Exception as exc:
        logger.error("delete_source failed for %s: %s", doc_id, exc)
        return SourceDeletion(
            check="delete_source",
            page=doc_id,
            action="failed",
            success=False,
            message=M_("Deletion failed: %(error)s", error=exc),
        )


def _config_mentions(workspace: Path, pages) -> tuple[str, ...]:
    """The titles and paths of the deleted `pages` that `wiki_config.toml` names.

    The file is written by hand (aliases, the assistant prompt), so it is not
    edited; the user is told what it still names."""
    config = workspace / "wiki_config.toml"
    if not config.exists() or not pages:
        return ()
    text = config.read_text(encoding="utf-8")
    found: list[str] = []
    for page in pages:
        relative = f"{page['path'].lstrip('/')}{page['filename']}"
        title = page["title"] or ""
        if relative in text:
            found.append(relative)
        if title and re.search(rf'(?<![\w])"{re.escape(title)}"\s*=', text):
            found.append(title)
    return tuple(dict.fromkeys(found))


def remove_missing_sources(db_path: str, workspace: Path) -> list[SourceDeletion]:
    """Delete, as `delete_source` does, every source of the index whose file is no
    longer in `sources/`: a file the user removed by hand.

    Runs when the wiki is opened, before a scan and before a rebuild of the index.
    With nothing missing it writes nothing and makes no commit: one existence test
    per source row.
    """
    from domain.tools.db import get_connection

    if not Path(db_path).exists():
        return []
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, relative_path FROM documents WHERE source_kind='source' ORDER BY filename"
        ).fetchall()
    missing = [r["id"] for r in rows if r["relative_path"] and not (workspace / r["relative_path"]).exists()]
    return [delete_source(db_path, workspace, doc_id) for doc_id in missing]
