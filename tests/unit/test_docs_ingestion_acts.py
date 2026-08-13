"""The act figures in the ingestion walkthrough must match its generated appendix.

`scripts/capture_ingestion_walkthrough.py` regenerates
`docs/ingestion_walkthrough_appendix.md` by really running the pipeline — which
means really calling an LLM. The model picks different concept names and produces
different link counts on every run, so **every regeneration invalidates the
hand-written prose that quotes those numbers**, and it does so silently.

That happened three times in one session before this file existed: each pass
found stale figures by hand, in a different place, after the previous pass had
been declared finished.

So this reads the numbers back out of both files and compares them. A failure
here is not a bug in the pipeline — it means the appendix was regenerated and the
prose has not caught up yet. The failure message names the figure to fix.

Its sibling `test_docs_walkthrough_figures.py` checks the *other* source of
truth: the FTS examples, measured against the shipped fairy-tale demo database.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "ingestion_walkthrough.md"
APPENDIX = REPO / "docs" / "ingestion_walkthrough_appendix.md"

pytestmark = pytest.mark.skipif(
    not APPENDIX.exists(), reason="generated ingestion appendix not present"
)


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def acts() -> dict[str, dict]:
    """What each act of the appendix actually reports.

    Returns {"Act 1": {"wiki": 6, "chunks": 16, "cites": 6, "links_to": 15,
                       "lint": (45, 40, 5, 0), "stale_marked": 5,
                       "commits": {...}}, ...}
    """
    text = APPENDIX.read_text(encoding="utf-8")
    out: dict[str, dict] = {}
    for match in re.finditer(r"^## (Act [\w]+)", text, re.M):
        name = match.group(1)
        start = match.end()
        nxt = text.find("\n## ", start)
        body = text[start: nxt if nxt > 0 else len(text)]

        rows = dict(re.findall(r"\|\s*`([\w() ]+)`\s*\|\s*(\d+)\s*\|", body))
        act: dict = {
            "wiki": _int(rows.get("documents (wiki)")),
            "chunks": _int(rows.get("document_chunks")),
            "cites": _int(rows.get("cites")),
            "links_to": _int(rows.get("links_to")),
            "commits": set(re.findall(r"^([0-9a-f]{7}) ingest", body, re.M)),
        }
        lint = re.search(
            r"🏁 (\d+) issue\(s\): (\d+) fixed, (\d+) skipped, (\d+) failed", body
        )
        act["lint"] = tuple(int(g) for g in lint.groups()) if lint else None
        stale = re.search(r"marked (\d+) citing page", body)
        act["stale_marked"] = int(stale.group(1)) if stale else None
        out[name] = act
    assert out, "no acts parsed from the appendix — did its format change?"
    return out


def _int(value: str | None) -> int | None:
    return int(value) if value is not None else None


def _node(doc: str, act: str) -> str:
    """One node of the walkthrough's story diagram, e.g. `Act 3b · …`."""
    match = re.search(rf'\["({re.escape(act)} · [^"]+)"\]', doc)
    assert match, f"the story diagram no longer has a node for {act}"
    return match.group(1)


# ── the story diagram — the figures forgotten most often, because they are
#    duplicated from the prose and sit inside a code fence ────────────────────

@pytest.mark.parametrize("act", ["Act 1", "Act 2", "Act 3b", "Act 3c"])
def test_story_diagram_page_and_fragment_counts_match(doc, acts, act):
    node = _node(doc, act)
    pages = re.search(r"(\d+) wiki pages", node)
    frags = re.search(r"(\d+) fragments", node)
    assert pages and frags, f"{act}: the diagram node no longer states both counts"
    assert int(pages.group(1)) == acts[act]["wiki"], (
        f"{act}: diagram says {pages.group(1)} wiki pages, "
        f"appendix says {acts[act]['wiki']}"
    )
    assert int(frags.group(1)) == acts[act]["chunks"], (
        f"{act}: diagram says {frags.group(1)} fragments, "
        f"appendix says {acts[act]['chunks']}"
    )


def test_story_diagram_link_transitions_match(doc, acts):
    """`cites 19 → 13` and `links_to 80 → 75` must land on the appendix totals."""
    for act, field in (("Act 2", "cites"), ("Act 2", "links_to"),
                       ("Act 3b", "links_to"), ("Act 3c", "cites"),
                       ("Act 3c", "links_to")):
        node = _node(doc, act)
        match = re.search(rf"{field} (\d+) → (\d+)", node)
        assert match, f"{act}: no `{field} a → b` in the diagram node"
        assert int(match.group(2)) == acts[act][field], (
            f"{act}: diagram ends {field} at {match.group(2)}, "
            f"appendix says {acts[act][field]}"
        )


def test_story_diagram_lint_summary_matches(doc, acts):
    node = _node(doc, "Act 3b")
    match = re.search(
        r"(\d+) issues · (\d+) fixed · (\d+) skipped · (\d+) failed", node
    )
    assert match, "Act 3b's diagram node no longer carries the lint summary"
    assert tuple(int(g) for g in match.groups()) == acts["Act 3b"]["lint"], (
        f"diagram says {match.groups()}, appendix says {acts['Act 3b']['lint']}"
    )


# ── the prose ───────────────────────────────────────────────────────────────

def test_act1_quotes_a_commit_the_appendix_records(doc, acts):
    """The hash in Act 1 is the single most invisible stale figure: it looks
    plausible forever and nothing else in the document contradicts it."""
    match = re.search(r"and one git commit \(`([0-9a-f]{7})`\)", doc)
    assert match, "Act 1 no longer quotes a commit hash"
    assert match.group(1) in acts["Act 1"]["commits"], (
        f"Act 1 quotes commit {match.group(1)}, which the appendix does not "
        f"record; it has {sorted(acts['Act 1']['commits'])}"
    )


def test_act3b_prose_lint_summary_matches(doc, acts):
    match = re.search(
        r"\*\*(\d+) issues,\s*(\d+)\s*fixed,\s*(\d+) skipped,\s*(\d+) failed\*\*", doc
    )
    assert match, "Act 3b's prose no longer states the lint summary"
    assert tuple(int(g) for g in match.groups()) == acts["Act 3b"]["lint"], (
        f"prose says {match.groups()}, appendix says {acts['Act 3b']['lint']}"
    )


def test_act3c_quotes_the_deletion_log_line_verbatim(doc, acts):
    """The log line is quoted inside a fenced block, so it reads as captured
    output even when it is not. It must match the count the appendix reports."""
    match = re.search(r"marked (\d+) citing page\(s\) stale", doc)
    assert match, "Act 3c no longer quotes the deletion log line"
    assert int(match.group(1)) == acts["Act 3c"]["stale_marked"], (
        f"the quoted log line says {match.group(1)} stale pages, the appendix "
        f"reports {acts['Act 3c']['stale_marked']}"
    )


def test_act3c_names_pages_that_exist_in_the_appendix(doc):
    """The concept pages listed as kept-and-stale are named individually, and the
    model renames concepts on every regeneration."""
    listed = re.search(
        r"\(`little-red-riding-hood`,(.+?)\) are \*kept\*", doc, re.S
    )
    assert listed, "Act 3c no longer lists the pages it keeps"
    slugs = re.findall(r"`([a-z0-9-]+)`", listed.group(1))
    assert slugs, "no page slugs parsed out of the Act 3c list"

    appendix = APPENDIX.read_text(encoding="utf-8")
    missing = [s for s in slugs if f"{s}.md" not in appendix]
    assert not missing, (
        f"Act 3c names pages the appendix never created: {missing}. The model "
        f"renames concepts on every capture — re-read the Act 2 page list."
    )
