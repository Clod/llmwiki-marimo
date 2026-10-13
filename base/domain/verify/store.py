"""Storage of the last verification of a wiki page (`index.db`).

`page_verifications` holds one row per page and `verification_sentences` one row
per checked sentence (database/sqlite_schema.sql, section 9). A sentence has no
stored verdict: it is supported when its probability reaches the threshold in
force, so changing `[verify] threshold` reclassifies the sentences without
verifying again.

A stored verification is valid while the hash of the page body and the hashes of
its context sources equal the ones stored (`state`).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from enum import Enum

from domain.datasets.frontmatter import split_frontmatter
from domain.tools.db import get_connection
from domain.verify.models import SentenceResult, VerificationReport

EXCERPT_CHARS = 300


def page_body(text: str) -> str:
    """The body of a page file: the text after its front-matter. The offsets of
    a verification index this text."""
    return split_frontmatter(text)[1]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class State(str, Enum):
    NONE = "none"           # the page was never verified, or its verification was deleted
    OUTDATED = "outdated"   # the page or one of its sources changed since
    VALID = "valid"


@dataclass(frozen=True)
class StoredSentence:
    """One stored sentence. `supported` depends on the threshold given."""

    start: int
    end: int
    text: str
    probability: float
    source: str | None
    excerpt: str | None

    def supported(self, threshold: float) -> bool:
        return self.probability >= threshold


@dataclass(frozen=True)
class StoredVerification:
    document_id: str
    body_hash: str
    source_hashes: dict[str, str]
    verifier: str
    model: str
    verified_at: str
    pairs_compared: int
    pairs_possible: int
    sentences: tuple[StoredSentence, ...]

    @property
    def checked(self) -> int:
        return len(self.sentences)

    def unsupported(self, threshold: float) -> list[StoredSentence]:
        return [s for s in self.sentences if not s.supported(threshold)]


def _excerpt(text: str | None) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS].rstrip() + "…"


def save_verification(
    conn: sqlite3.Connection, document_id: str, report: VerificationReport, *,
    body_hash: str, source_hashes: dict[str, str], model: str = "",
) -> None:
    """Replace the verification of `document_id` with `report`, in one transaction."""
    with conn:
        conn.execute("DELETE FROM page_verifications WHERE document_id = ?", (document_id,))
        cursor = conn.execute(
            "INSERT INTO page_verifications "
            "(document_id, body_hash, source_hashes, verifier, model, "
            " sentences_checked, pairs_compared, pairs_possible) VALUES (?,?,?,?,?,?,?,?) "
            "RETURNING id",
            (document_id, body_hash, json.dumps(source_hashes, sort_keys=True), report.verifier,
             model, report.checked, report.pairs, report.pairs_possible),
        )
        verification_id = cursor.fetchone()[0]
        conn.executemany(
            "INSERT INTO verification_sentences "
            "(verification_id, position, start_char, end_char, sentence, probability, "
            " best_source, best_excerpt) VALUES (?,?,?,?,?,?,?,?)",
            [(verification_id, i, s.start, s.end, s.text, s.probability, s.page, _excerpt(s.passage))
             for i, s in enumerate(report.sentences)],
        )


def delete_verification(conn: sqlite3.Connection, document_id: str) -> None:
    """Delete the verification of `document_id`; the caller owns the transaction."""
    conn.execute("DELETE FROM page_verifications WHERE document_id = ?", (document_id,))


def load_verification(conn: sqlite3.Connection, document_id: str) -> StoredVerification | None:
    row = conn.execute(
        "SELECT * FROM page_verifications WHERE document_id = ?", (document_id,)
    ).fetchone()
    if row is None:
        return None
    sentences = conn.execute(
        "SELECT start_char, end_char, sentence, probability, best_source, best_excerpt "
        "FROM verification_sentences WHERE verification_id = ? ORDER BY position", (row["id"],)
    ).fetchall()
    return StoredVerification(
        document_id=document_id, body_hash=row["body_hash"],
        source_hashes=json.loads(row["source_hashes"] or "{}"),
        verifier=row["verifier"], model=row["model"], verified_at=row["verified_at"],
        pairs_compared=row["pairs_compared"], pairs_possible=row["pairs_possible"],
        sentences=tuple(StoredSentence(s["start_char"], s["end_char"], s["sentence"],
                                       s["probability"], s["best_source"], s["best_excerpt"])
                        for s in sentences),
    )


def state(stored: StoredVerification | None, body: str, source_hashes: dict[str, str]) -> State:
    """Whether `stored` still describes a page with this body and these sources."""
    if stored is None:
        return State.NONE
    if stored.body_hash != text_hash(body) or stored.source_hashes != source_hashes:
        return State.OUTDATED
    return State.VALID


def report_from_stored(stored: StoredVerification, threshold: float) -> VerificationReport:
    """The stored sentences as a report, classified at `threshold`."""
    return VerificationReport(
        tuple(SentenceResult(s.start, s.end, s.text, s.supported(threshold), s.probability,
                             s.source, s.excerpt) for s in stored.sentences),
        stored.verifier, threshold, stored.pairs_compared, stored.pairs_possible,
    )


def has_verification(db_path: str, document_id: str) -> bool:
    with get_connection(db_path) as conn:
        return conn.execute(
            "SELECT 1 FROM page_verifications WHERE document_id = ?", (document_id,)
        ).fetchone() is not None


def restore_verification(conn: sqlite3.Connection, stored: StoredVerification) -> None:
    """Write `stored` back, as a rollback of an overwritten page does: the page
    returns to the text this verification describes."""
    with conn:
        conn.execute("DELETE FROM page_verifications WHERE document_id = ?", (stored.document_id,))
        verification_id = conn.execute(
            "INSERT INTO page_verifications "
            "(document_id, body_hash, source_hashes, verifier, model, verified_at, "
            " sentences_checked, pairs_compared, pairs_possible) VALUES (?,?,?,?,?,?,?,?,?) "
            "RETURNING id",
            (stored.document_id, stored.body_hash, json.dumps(stored.source_hashes, sort_keys=True),
             stored.verifier, stored.model, stored.verified_at, stored.checked,
             stored.pairs_compared, stored.pairs_possible),
        ).fetchone()[0]
        conn.executemany(
            "INSERT INTO verification_sentences "
            "(verification_id, position, start_char, end_char, sentence, probability, "
            " best_source, best_excerpt) VALUES (?,?,?,?,?,?,?,?)",
            [(verification_id, i, s.start, s.end, s.text, s.probability, s.source, s.excerpt)
             for i, s in enumerate(stored.sentences)],
        )
