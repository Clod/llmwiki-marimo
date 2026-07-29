"""Every relative link in the maintained docs must resolve to a real file.

This exists because documentation rot is not caught by discipline. Renaming or
deleting a file leaves its references behind in places nobody thinks to grep —
one retired test file left six dead references across both READMEs, CONTRIBUTING,
the Trellis spec and the manual, each of them an instruction a reader would have
copy-pasted into a command that no longer resolves.

A checklist item asking people to remember does not work; a red build does. This
runs in CI with the rest of the unit suite (no LLM, no network, milliseconds), so
a rename that orphans a link fails immediately instead of rotting quietly.

Scope is deliberate: `docs/archive/` is frozen history whose links may point at
things that are gone on purpose, and generated wikis under `examples/` are
fixtures, not maintained prose.
"""

from __future__ import annotations

import re
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Markdown that we promise to keep honest.
_ROOT_DOCS = ("README.md", "README_ES.md", "CONTRIBUTING.md", "SECURITY.md")
_DOC_TREES = ("docs", ".trellis/spec")
_EXTRA_DOCS = (".trellis/workflow.md",)
_EXCLUDED_DIRS = {"archive", "node_modules", ".venv"}

# ``` or ~~~ fenced blocks: a doc that *shows* markdown must not be linted for it.
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
# Same reasoning for inline code spans. The manual is full of prose like
# "`[text](concepts/foo.md)` links to a non-existent file" — that is a link being
# QUOTED, not followed, and its target is a deliberate placeholder.
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
# Inline and image links: [text](target) / ![alt](target).
_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_SKIP_PREFIXES = ("http://", "https://", "mailto:", "tel:", "#")


def _maintained_markdown() -> list[Path]:
    files = [_PROJECT_ROOT / name for name in _ROOT_DOCS + _EXTRA_DOCS]
    for tree in _DOC_TREES:
        for path in (_PROJECT_ROOT / tree).rglob("*.md"):
            if _EXCLUDED_DIRS.isdisjoint(path.parts):
                files.append(path)
    return sorted({f for f in files if f.is_file()})


def _links_outside_code(md: Path) -> list[str]:
    """Relative link targets in `md`, ignoring anything inside a fenced block."""
    targets: list[str] = []
    in_fence = False
    for line in md.read_text(encoding="utf-8").splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for raw in _LINK_RE.findall(_INLINE_CODE_RE.sub("", line)):
            target = raw.strip().strip("<>")
            if not target or target.startswith(_SKIP_PREFIXES):
                continue
            targets.append(target)
    return targets


def test_relative_doc_links_resolve() -> None:
    """A relative link in maintained docs must point at a file that exists."""
    broken: list[str] = []
    for md in _maintained_markdown():
        for target in _links_outside_code(md):
            # Anchors are not verified (that needs heading-slug rules); only the
            # file half of `page.md#section` has to exist.
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            resolved = (md.parent / path_part).resolve()
            if not resolved.exists():
                broken.append(f"{md.relative_to(_PROJECT_ROOT)} -> {target}")

    assert not broken, (
        "Dead links in maintained docs (a rename or deletion left these behind):\n  "
        + "\n  ".join(broken)
    )


def test_extractor_finds_real_links_and_ignores_quoted_ones(tmp_path: Path) -> None:
    """The checker must still bite after the code-stripping rules above.

    A checker that can never fail is worse than none, and both strip rules
    (fenced blocks, inline spans) are exactly the kind of thing that silently
    over-matches and swallows every link.
    """
    md = tmp_path / "sample.md"
    md.write_text(
        "See [the manual](manual.md) and [an image](assets/x.png).\n"
        "Quoted, not followed: `[text](concepts/foo.md)`.\n"
        "```\n[fenced](never-checked.md)\n```\n"
        "[external](https://example.com) and [anchor](#section) are skipped.\n",
        encoding="utf-8",
    )
    found = _links_outside_code(md)
    assert found == ["manual.md", "assets/x.png"], found


def test_the_checker_actually_scans_the_docs() -> None:
    """Guard against the scan silently matching nothing (a glob typo would make
    the test above pass vacuously, which is worse than no test at all)."""
    files = _maintained_markdown()
    assert len(files) > 10, f"suspiciously few docs scanned: {[str(f) for f in files]}"
    assert any(f.name == "README.md" for f in files)
    assert any("manual" in f.parts for f in files)
