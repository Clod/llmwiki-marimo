"""Individual lint check functions. Each returns list[LintIssue]."""

import re
from pathlib import Path

from domain.tools.db import get_connection
from domain.tools.references import find_orphan_pages
from domain.tools.search import search_chunks
from .markers import DATA_GAP_BLOCK_RE, fts_safe
from .report import LintIssue

# ── 3.1: Orphan check ─────────────────────────────────────────────────────────

def orphan_check(db_path: str) -> list[LintIssue]:
    """Concept pages that no other page links to or cites."""
    orphans = find_orphan_pages(db_path)
    issues = []
    for page in orphans:
        if "/wiki/concepts/" not in page.get("path", ""):
            continue  # only flag concept pages — summary pages are expected entry points
        path = page["path"] + page["filename"]
        issues.append(LintIssue(
            check="orphan",
            severity="warning",
            page=path,
            description=(
                f"No other pages link to or cite "
                f"'{page['title'] or page['filename']}'"
            ),
            suggestion="Add a link to this page from a related concept or summary page",
        ))
    return issues


def unpaged_source_check(db_path: str) -> list[LintIssue]:
    """Source documents that were stored but produced no wiki page.

    Ingestion commits the source row as ``status='ready'`` at step 6, before the
    model writes anything. If a model call in steps 7-9 then fails, the source
    stays indexed and searchable with no page written from it — the failure mode
    the ingestion walkthrough describes as the worst possible outcome.

    Nothing else reports it. ``needs_ingestion`` compares the file's mtime and
    hash, never whether the document has pages, so every later scan calls the
    source up to date; ``thin_page_check`` measures how well a source's pages
    cover it and skips a source that has none. Recovering means deleting the
    source and ingesting it again, which is a human decision — hence advisory.
    """
    issues = []
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT d.id, d.filename, d.relative_path FROM documents d "
            "WHERE d.source_kind = 'source' AND d.status = 'ready' "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM document_references r "
            "    WHERE r.target_document_id = d.id AND r.reference_type = 'cites'"
            "  ) "
            "ORDER BY d.filename"
        ).fetchall()
    for row in rows:
        issues.append(LintIssue(
            check="unpaged_source",
            severity="warning",
            page=row["relative_path"] or row["filename"],
            description=(
                f"'{row['filename']}' is stored and searchable but no wiki page "
                f"cites it"
            ),
            suggestion=(
                "Ingestion stored the source and then wrote no page, usually "
                "because a model call failed. Re-scanning will not fix it: the "
                "file is unchanged, so it counts as up to date. Delete the "
                "source and ingest it again."
            ),
        ))
    return issues


# ── 3.2: Staleness check ──────────────────────────────────────────────────────

def staleness_check(db_path: str) -> list[LintIssue]:
    """Wiki pages whose cited sources were updated more recently than the page itself."""
    issues = []
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT
                d_wiki.id,
                d_wiki.path || d_wiki.filename AS page_path,
                d_wiki.title,
                d_wiki.updated_at AS wiki_updated,
                MAX(d_src.updated_at) AS src_updated
            FROM documents d_wiki
            JOIN document_references dr
                ON dr.source_document_id = d_wiki.id
                AND dr.reference_type = 'cites'
            JOIN documents d_src
                ON dr.target_document_id = d_src.id
            WHERE d_wiki.source_kind = 'wiki'
              AND d_wiki.status != 'failed'
            GROUP BY d_wiki.id
            HAVING src_updated > wiki_updated
        """).fetchall()
    for row in rows:
        issues.append(LintIssue(
            check="stale",
            severity="warning",
            page=row["page_path"],
            description=(
                f"A cited source was updated after this page was last written "
                f"(page: {row['wiki_updated']}, source: {row['src_updated']})"
            ),
            suggestion="Review and update this page to incorporate the latest source information",
        ))
    return issues


# ── 3.3: Missing cross-references ─────────────────────────────────────────────

def missing_xref_check(db_path: str) -> list[LintIssue]:
    """Concept page pairs that share cited sources but don't link to each other."""
    issues = []
    with get_connection(db_path) as conn:
        pairs = conn.execute("""
            SELECT DISTINCT
                d1.id AS id_a,
                d1.path || d1.filename AS path_a,
                d1.title AS title_a,
                d2.id AS id_b,
                d2.path || d2.filename AS path_b,
                d2.title AS title_b
            FROM document_references dr1
            JOIN document_references dr2
                ON  dr1.target_document_id = dr2.target_document_id
                AND dr1.source_document_id < dr2.source_document_id
                AND dr1.reference_type = 'cites'
                AND dr2.reference_type = 'cites'
            JOIN documents d1
                ON dr1.source_document_id = d1.id AND d1.source_kind = 'wiki'
            JOIN documents d2
                ON dr2.source_document_id = d2.id AND d2.source_kind = 'wiki'
        """).fetchall()

        for pair in pairs:
            linked = conn.execute("""
                SELECT COUNT(*) FROM document_references
                WHERE reference_type = 'links_to'
                  AND (
                      (source_document_id = ? AND target_document_id = ?)
                   OR (source_document_id = ? AND target_document_id = ?)
                  )
            """, (pair["id_a"], pair["id_b"], pair["id_b"], pair["id_a"])).fetchone()[0]

            if linked == 0:
                a = pair["title_a"] or pair["path_a"]
                b = pair["title_b"] or pair["path_b"]
                issues.append(LintIssue(
                    check="missing_xref",
                    severity="info",
                    page=pair["path_a"],
                    description=f"'{a}' and '{b}' share cited sources but don't link to each other",
                    suggestion="Add a cross-reference link between these related concept pages",
                    related_page=pair["path_b"],
                ))
    return issues


# ── 3.4: Mentioned-but-missing concepts ───────────────────────────────────────

_CONCEPT_LINK_RE = re.compile(
    r"\[([^\]]+)\]\((?:\.\.\/)?concepts\/([^)]+\.md)\)"
)


def missing_concept_check(db_path: str, workspace: Path) -> list[LintIssue]:
    """Wiki pages that link to concept pages that don't exist on disk."""
    issues = []
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT path, filename, content FROM documents "
            "WHERE source_kind='wiki' AND status!='failed' AND content IS NOT NULL"
        ).fetchall()

    for row in rows:
        content = row["content"] or ""
        page_path = row["path"] + row["filename"]
        for match in _CONCEPT_LINK_RE.finditer(content):
            concept_file = match.group(2)
            on_disk = workspace / "wiki" / "concepts" / concept_file
            if not on_disk.exists():
                issues.append(LintIssue(
                    check="missing_concept",
                    severity="warning",
                    page=page_path,
                    description=f"Links to 'concepts/{concept_file}' which doesn't exist on disk",
                    suggestion="Create the missing concept page or remove the broken link",
                ))
    return issues


# ── 3.5: Contradiction sweep ───────────────────────────────────────────────────

_CONTRADICTION_SYSTEM = (
    "You are a fact-checker reviewing a knowledge base for internal consistency."
)

_CONTRADICTION_TEMPLATE = """\
Compare these two wiki pages and identify any factual contradictions.

Page 1 ({path_a}):
{content_a}

Page 2 ({path_b}):
{content_b}

Reply with "CONTRADICTION: <description>" if you find contradictions, \
or "NO CONTRADICTION" if none."""


def contradiction_check(
    db_path: str, workspace: Path, client, model: str, progress_cb=None
) -> list[LintIssue]:
    """LLM-powered contradiction detection between related concept pages.

    This is the slowest lint check — one LLM call per concept pair that shares a
    cited source. progress_cb (if given) is called before each pair so the caller
    can report movement during what is otherwise a long silent stretch.
    """
    issues = []
    with get_connection(db_path) as conn:
        pairs = conn.execute("""
            SELECT DISTINCT
                d1.id AS id_a,
                d1.path || d1.filename AS path_a,
                d1.content AS content_a,
                d2.id AS id_b,
                d2.path || d2.filename AS path_b,
                d2.content AS content_b
            FROM document_references dr1
            JOIN document_references dr2
                ON  dr1.target_document_id = dr2.target_document_id
                AND dr1.source_document_id < dr2.source_document_id
                AND dr1.reference_type = 'cites'
                AND dr2.reference_type = 'cites'
            JOIN documents d1
                ON dr1.source_document_id = d1.id
                AND d1.source_kind = 'wiki' AND d1.content IS NOT NULL
            JOIN documents d2
                ON dr2.source_document_id = d2.id
                AND d2.source_kind = 'wiki' AND d2.content IS NOT NULL
        """).fetchall()

    total = len(pairs)
    for _i, pair in enumerate(pairs, 1):
        if progress_cb:
            progress_cb(
                f"⚖️ contradiction {_i}/{total}: "
                f"{pair['path_a'].rsplit('/', 1)[-1]} ⇄ {pair['path_b'].rsplit('/', 1)[-1]}"
            )
        prompt = _CONTRADICTION_TEMPLATE.format(
            path_a=pair["path_a"],
            content_a=(pair["content_a"] or "")[:2000],
            path_b=pair["path_b"],
            content_b=(pair["content_b"] or "")[:2000],
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _CONTRADICTION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        text = (response.choices[0].message.content or "").strip()
        if text.upper().startswith("CONTRADICTION"):
            detail = text[text.find(":") + 1:].strip() if ":" in text else text
            issues.append(LintIssue(
                check="contradiction",
                severity="error",
                page=pair["path_a"],
                description=(
                    f"Possible contradiction with '{pair['path_b']}': {detail}"
                ),
                suggestion="Review both pages and resolve the conflicting claims",
                related_page=pair["path_b"],
            ))
    return issues


# ── 3.5: Data gap check ───────────────────────────────────────────────────────

_GAP_TEMPLATE = """\
Review these wiki concept pages and identify important topics that are missing or underdeveloped.

Concepts covered:
{concept_list}

Reply with one "GAP: <topic> — <suggestion>" per line for gaps you find, \
or "NO GAPS" if coverage looks comprehensive."""


def data_gap_check(
    db_path: str, workspace: Path, client, model: str, progress_cb=None
) -> list[LintIssue]:
    """LLM-powered check for missing or underdeveloped topics."""
    issues = []
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT title, path, filename FROM documents "
            "WHERE source_kind='wiki' AND path LIKE '/wiki/concepts/%' AND status!='failed'"
        ).fetchall()

    if not rows:
        return []

    if progress_cb:
        progress_cb(f"🔍 data-gap scan: reviewing {len(rows)} concept title(s)")

    concept_list = "\n".join(
        f"- {r['title'] or r['filename']} ({r['path']}{r['filename']})"
        for r in rows
    )
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": _GAP_TEMPLATE.format(concept_list=concept_list)}],
        temperature=0.3,
    )
    text = (response.choices[0].message.content or "").strip()

    if text.upper().startswith("NO GAPS"):
        return []

    for line in text.splitlines():
        line = line.strip()
        if not line.upper().startswith("GAP:"):
            continue
        detail = line[4:].strip()
        topic, _, suggestion = detail.partition(" — ")
        topic = topic.strip()
        if not topic:
            continue

        from domain.ingestion.wiki_generator import make_wiki_slug  # deferred to avoid import cycle
        slug = make_wiki_slug(topic)
        query = fts_safe(topic)
        hits = search_chunks(db_path, query, limit=1, scope="wiki") if query else []
        if not hits:
            continue  # no related page to host the note
        host_path = hits[0]["path"] + hits[0]["filename"]

        issues.append(LintIssue(
            check="data_gap",
            severity="info",
            page=host_path,
            description=f"Missing or underdeveloped topic: {topic}",
            suggestion=suggestion.strip() or "Consider adding this concept page",
            topic=slug,
        ))
    return issues


# ── 3.6: Gap-filled check ─────────────────────────────────────────────────────

def gap_filled_check(db_path: str) -> list[LintIssue]:
    """Find DATA_GAP TODO markers whose topic is now covered by a source."""
    issues = []
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT path, filename, content FROM documents "
            "WHERE source_kind='wiki' AND status!='failed' AND content IS NOT NULL"
        ).fetchall()
    for row in rows:
        for m in DATA_GAP_BLOCK_RE.finditer(row["content"] or ""):
            slug = m.group("slug")
            query = fts_safe(slug.replace("-", " "))
            covered = search_chunks(db_path, query, limit=1, scope="sources") if query else []
            if covered:
                issues.append(LintIssue(
                    check="gap_filled",
                    severity="info",
                    page=row["path"] + row["filename"],
                    description=f"TODO topic '{slug}' is now covered by a source",
                    suggestion="Replace the TODO note with a link to the new page",
                    topic=slug,
                ))
    return issues


# ── 3.7: Vocabulary check (alias map vs live coverage) ────────────────────────

def vocabulary_check(db_path: str, workspace: Path) -> list[LintIssue]:
    """Cross the effective alias vocabulary against the wiki's live coverage.

    Pure (no LLM). The ingest-time generator drops collisions when it writes, so
    this catches the drift it can't: a collision introduced by a hand override or
    a newly-added dataset; an alias entry for a canonical the wiki no longer
    covers; an alias mapping to two canonicals; and an off-limits term now covered.
    """
    from domain.chat.config import load_config
    from domain.chat.vocabulary import build_roster, normalize, validate_aliases
    from domain.ingestion.alias_generation import dataset_vocabulary
    from domain.tools.wiki_fs import concept_page_names

    config = load_config(Path(workspace))
    aliases = config.data_aliases
    if not aliases and not config.off_limits:
        return []

    roster = set(build_roster(dataset_vocabulary(Path(workspace)), concept_page_names(db_path)))
    issues: list[LintIssue] = []
    artifact = ".llmwiki/aliases.generated.toml"

    # 1. Collisions — an alias that is really another covered canonical's name.
    for c in validate_aliases(aliases, roster).collisions:
        issues.append(LintIssue(
            check="vocab_collision", severity="error", page=artifact,
            description=(
                f"Alias '{c.alias}' of '{c.canonical}' is really the name of '{c.collides_with}'"
            ),
            suggestion=f"List '{c.alias}' under '{c.canonical}' in [falsos_sinonimos], or remove it",
            alias=c.alias,
        ))

    # 2. Stale — an alias entry for a canonical the wiki no longer covers.
    for canonical in aliases:
        if normalize(canonical) not in roster:
            issues.append(LintIssue(
                check="vocab_stale", severity="warning", page=artifact,
                description=f"Aliases for '{canonical}', which has no page or dataset",
                suggestion="Remove the entry, or restore the page/dataset it names",
            ))

    # 3. Ambiguous — one alias mapping to two different canonicals.
    owners: dict[str, list[str]] = {}
    for canonical, alias_list in aliases.items():
        for alias in alias_list:
            owners.setdefault(normalize(alias), []).append(canonical)
    for alias_norm, canonicals in owners.items():
        if len({normalize(c) for c in canonicals}) > 1:
            issues.append(LintIssue(
                check="vocab_ambiguous", severity="warning", page=artifact,
                description=(
                    f"Alias '{alias_norm}' maps to several canonicals: "
                    f"{', '.join(sorted(set(canonicals)))}"
                ),
                suggestion="Keep the alias under a single canonical",
            ))

    # 4. Covered — an off-limits term the wiki now actually covers.
    for term in config.off_limits:
        if normalize(term) in roster:
            issues.append(LintIssue(
                check="vocab_covered", severity="info", page="wiki_config.toml",
                description=f"'{term}' is in [fuera_de_alcance] but now has a page or dataset",
                suggestion="Remove it from the blacklist — it's covered now",
            ))

    return issues


# ── 3.8: Thin-page detector (source chunks left uncovered by the wiki) ─────────

_THIN_MIN_CHUNKS = 3         # too few chunks → a ratio is meaningless; skip tiny docs
_THIN_ORPHAN_COVERAGE = 0.2  # a chunk is "covered" if ≥20% of its words appear in the pages
_THIN_ORPHAN_RATIO = 0.5     # flag only when at least half the chunks are orphaned


def thin_page_check(db_path: str) -> list[LintIssue]:
    """Source docs whose wiki page(s) leave many of their chunks uncovered.

    Pure, no LLM — the ingest-side pata of the coverage story. NOT measured by
    size (a good summary is deliberately short; size would false-alarm on every
    good summary). The ruler is orphan chunks: per source doc, a page-chunk whose
    content words barely appear in the wiki pages that carry that source. When most
    of a source's chunks are orphaned, the wiki under-covers it, so a legit
    question about the missing part would have to fall to Tier-2 — flag it
    (warning) so the page can be expanded. Reuses the same lexical `coverage` that
    verifies Tier-2.

    Coverage = the pages that CITE the source (the summaries) PLUS the concept
    pages those summaries link to. Only summaries carry a `cites` edge back to the
    source; the detail lives in the concept pages they spawned, which don't. Miss
    those and the check measures the source against the short summary alone and
    false-alarms on every doc whose detail was split into concepts.
    """
    from domain.chat.overlap import is_supported

    issues: list[LintIssue] = []
    with get_connection(db_path) as conn:
        sources = conn.execute(
            "SELECT id, filename FROM documents "
            "WHERE source_kind = 'source' AND status != 'failed'"
        ).fetchall()
        for src in sources:
            chunks = [
                r["content"] for r in conn.execute(
                    "SELECT content FROM document_pages WHERE document_id = ? ORDER BY page",
                    (src["id"],),
                ).fetchall()
                if (r["content"] or "").strip()
            ]
            if len(chunks) < _THIN_MIN_CHUNKS:
                continue

            citing = conn.execute(
                "SELECT d.id AS wiki_id, d.path || d.filename AS page_path, "
                "       d.content AS content, d.source_document_id AS sdid, d.path AS dir "
                "FROM document_references dr "
                "JOIN documents d ON dr.source_document_id = d.id AND d.source_kind = 'wiki' "
                "WHERE dr.target_document_id = ? AND dr.reference_type = 'cites'",
                (src["id"],),
            ).fetchall()
            if not citing:
                continue

            # Coverage = citing pages (summaries) + the concept pages they link to.
            texts = [c["content"] or "" for c in citing]
            citing_ids = [c["wiki_id"] for c in citing]
            placeholders = ",".join("?" * len(citing_ids))
            linked = conn.execute(
                "SELECT t.content FROM document_references l "
                "JOIN documents t ON l.target_document_id = t.id AND t.source_kind = 'wiki' "
                f"WHERE l.reference_type = 'links_to' AND l.source_document_id IN ({placeholders})",
                citing_ids,
            ).fetchall()
            texts.extend(r["content"] or "" for r in linked)

            pages_text = " ".join(texts)
            orphans = sum(
                1 for ch in chunks
                if not is_supported(ch, pages_text, min_coverage=_THIN_ORPHAN_COVERAGE)
            )
            if orphans / len(chunks) < _THIN_ORPHAN_RATIO:
                continue

            # Attach the finding to the source's own summary page when present
            # (the natural thing to expand), else any page citing the source.
            target = (
                next((c for c in citing
                      if c["sdid"] == src["id"] and c["dir"] == "/wiki/summaries/"), None)
                or next((c for c in citing if c["sdid"] == src["id"]), None)
                or citing[0]
            )
            issues.append(LintIssue(
                check="thin_page", severity="warning", page=target["page_path"],
                description=(
                    f"{orphans} of {len(chunks)} source chunks in '{src['filename']}' "
                    "aren't reflected in the wiki page(s) citing it"
                ),
                suggestion=(
                    "Expand or regenerate the page to cover the source, or accept the "
                    "Tier-2 fallback for the uncovered part"
                ),
            ))
    return issues
