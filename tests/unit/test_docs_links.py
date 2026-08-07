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
_ROOT_DOCS = ("README.md", "README_ES.md", "CONTRIBUTING.md", "SECURITY.md", "ROADMAP.md")
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


# ── workflows.md names real symbols ─────────────────────────────────────────

def test_workflows_prompt_constants_exist() -> None:
    """§6's prompt tables name the constants each workflow builds its prompt from.

    They used to carry line numbers too — `_CONCEPT_SYSTEM (L109)` — and every one
    of the eleven checked had rotted, by 3 to 163 lines, because a line number
    breaks the moment anyone edits above it. The numbers are gone; the names stay,
    and this test is what keeps them honest.
    """
    import re

    doc = (_PROJECT_ROOT / "docs" / "manual" / "workflows.md").read_text()
    # Every module, so the test does not need to know which file a constant
    # lives in — moving one between modules is a refactor, not a doc defect.
    haystack = "\n".join(
        f.read_text() for f in (_PROJECT_ROOT / "base" / "domain").rglob("*.py")
    )

    named = {m for m in re.findall(r"`(_[A-Z][A-Z0-9_]{4,})`", doc)}
    assert named, "no prompt constants found in workflows.md — did the tables change shape?"

    missing = sorted(n for n in named if f"{n} " not in haystack and f"{n}:" not in haystack)
    assert not missing, (
        f"workflows.md names constants that no longer exist: {missing}. "
        "Either they were renamed and the doc was not updated, or the doc invented them."
    )


# ── the manual's global section numbering ───────────────────────────────────

_MANUAL_FILES = (
    "docs/programmer_manual.md",
    "docs/manual/workflows.md",
    "docs/manual/internals.md",
    "docs/manual/apps.md",
)


def test_every_cited_manual_section_exists_somewhere() -> None:
    """The manual is four files with one shared numbering, and ~190 `§N`
    references cross between them and the rest of the docs.

    That only works while every cited number is actually defined by one of the
    four. A `§N` is a bare reference — no link to resolve, so the link checker
    above cannot see it, and a section that is renumbered or dropped leaves its
    citations pointing at nothing, silently. That already happened once: folding
    §11 and §12 into ROADMAP.md orphaned 27 references.
    """
    import re

    defined: dict[int, str] = {}
    for rel in _MANUAL_FILES:
        text = (_PROJECT_ROOT / rel).read_text(encoding="utf-8")
        for n in re.findall(r"^## (\d+)\.", text, re.M):
            defined[int(n)] = rel
    assert defined, "no numbered sections found — did the heading format change?"

    cited: dict[int, set[str]] = {}
    for md in _maintained_markdown():
        if "archive" in md.parts:
            continue
        for n in re.findall(r"§(\d+)", md.read_text(encoding="utf-8")):
            cited.setdefault(int(n), set()).add(str(md.relative_to(_PROJECT_ROOT)))

    orphaned = {n: sorted(where) for n, where in cited.items() if n not in defined}
    assert not orphaned, (
        "Docs cite manual sections that no longer exist:\n  "
        + "\n  ".join(f"§{n} — cited in {', '.join(w)}" for n, w in sorted(orphaned.items()))
        + f"\nDefined sections: {sorted(defined)}"
    )


def test_manual_sections_are_not_defined_twice() -> None:
    """Two files claiming the same §N would make every citation ambiguous."""
    import re
    from collections import defaultdict

    owners: dict[int, list[str]] = defaultdict(list)
    for rel in _MANUAL_FILES:
        for n in re.findall(r"^## (\d+)\.", (_PROJECT_ROOT / rel).read_text(encoding="utf-8"), re.M):
            owners[int(n)].append(rel)
    clashes = {n: f for n, f in owners.items() if len(f) > 1}
    assert not clashes, f"section numbers defined in more than one file: {clashes}"
