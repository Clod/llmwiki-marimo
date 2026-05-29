"""Deterministic wiki/index.md maintenance.

No LLM calls — purely text manipulation on a structured markdown file.
"""

import re
from pathlib import Path


_ENTRY_RE = re.compile(r"^- \[([^\]]+)\]\(([^)]+)\)")


def update_index(
    workspace: Path,
    page_path: str,
    one_line_summary: str,
    category: str,
) -> None:
    """Add or update an entry in wiki/index.md under the appropriate section.

    category: "summaries" | "concepts"
    page_path: relative path from wiki/ e.g. "summaries/my-doc.md"
    one_line_summary: short description appended after the link
    """
    index_file = workspace / "wiki" / "index.md"
    if not index_file.exists():
        index_file.write_text("# Wiki Index\n\n## Summaries\n\n## Concepts\n", encoding="utf-8")

    text = index_file.read_text(encoding="utf-8")
    section = "## Summaries" if category == "summaries" else "## Concepts"

    filename = Path(page_path).name
    title = Path(page_path).stem.replace("-", " ").title()
    link = f"({page_path})"
    new_entry = f"- [{title}]{link} — {one_line_summary}"

    text = _upsert_entry(text, section, filename, new_entry)
    index_file.write_text(text, encoding="utf-8")


def remove_index_entry(workspace: Path, page_path: str, category: str) -> None:
    """Remove the entry for page_path from wiki/index.md, if present.

    Inverse of update_index — used to roll back index changes when an ingest
    fails partway through. No-op if the file or entry is absent.
    """
    index_file = workspace / "wiki" / "index.md"
    if not index_file.exists():
        return
    section = "## Summaries" if category == "summaries" else "## Concepts"
    filename = Path(page_path).name

    lines = index_file.read_text(encoding="utf-8").splitlines(keepends=True)
    out: list[str] = []
    in_section = False
    removed = False
    for line in lines:
        if line.strip() == section:
            in_section = True
            out.append(line)
            continue
        if in_section:
            if line.startswith("## "):
                in_section = False
            else:
                m = _ENTRY_RE.match(line)
                if m and Path(m.group(2)).name == filename:
                    removed = True
                    continue  # drop this entry
        out.append(line)

    if removed:
        index_file.write_text("".join(out), encoding="utf-8")


def _upsert_entry(text: str, section: str, filename: str, new_entry: str) -> str:
    """Insert or replace a link entry within the given section."""
    lines = text.splitlines(keepends=True)
    in_section = False
    section_end = None
    existing_line = None

    for i, line in enumerate(lines):
        if line.strip() == section:
            in_section = True
            continue
        if in_section:
            if line.startswith("## "):
                section_end = i
                break
            m = _ENTRY_RE.match(line)
            if m and Path(m.group(2)).name == filename:
                existing_line = i

    if existing_line is not None:
        lines[existing_line] = new_entry + "\n"
    else:
        insert_at = section_end if section_end is not None else len(lines)
        # Insert before the next section (or at end), leaving a blank line
        lines.insert(insert_at, new_entry + "\n")
        # Ensure a blank line before the next section heading. After the insert
        # above, the heading that was at insert_at is now at insert_at+1 — check
        # there (the old code checked insert_at, i.e. the entry we just added, so
        # the blank was never inserted and entries butted against the heading).
        if insert_at + 1 < len(lines) and lines[insert_at + 1].startswith("## "):
            lines.insert(insert_at + 1, "\n")

    return "".join(lines)
