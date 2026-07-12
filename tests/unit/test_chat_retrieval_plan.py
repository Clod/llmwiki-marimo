"""Tests for domain/chat/preretrieval.py::plan_retrieval — the pure decision core.

Given the deterministic gate inputs and what the code retrieved (curated wiki
hits, then raw-doc hits), decide: refuse without the model, or invoke it with
Tier-1 (curated, trusted) context, Tier-2 (raw doc, verify + warn) context, or
no injected context (a data/advisory question, tools only).
"""

from domain.chat.preretrieval import plan_retrieval


def _plan(question="q", off_limits=(), wiki_hits=(), doc_hits=(), has_data=False,
          in_roster=True):
    return plan_retrieval(
        question,
        off_limits=off_limits,
        wiki_hits=list(wiki_hits),
        doc_hits=list(doc_hits),
        has_data=has_data,
        in_roster=in_roster,
    )


def test_off_limits_refuses_even_with_hits():
    # Blacklist wins over anything the search happened to return.
    plan = _plan("¿qué son los cedears?", off_limits={"cedear", "cedears"},
                 wiki_hits=["algo"], has_data=True)
    assert plan.action == "refuse"


def test_curated_wiki_hit_is_tier1_no_verify():
    plan = _plan(wiki_hits=["# Caución\ntexto", "# Aforo\notro"])
    assert plan.action == "invoke"
    assert plan.tier == "curado"
    assert "# Caución" in plan.context and "# Aforo" in plan.context
    assert plan.verify is False


def test_raw_doc_hit_is_tier2_with_verify():
    # Covered topic (in_roster) with no curated page -> Tier 2 with verification.
    plan = _plan(wiki_hits=[], doc_hits=["chunk crudo"], in_roster=True)
    assert plan.action == "invoke"
    assert plan.tier == "crudo"
    assert plan.context == "chunk crudo"
    assert plan.verify is True


def test_raw_doc_hit_not_in_roster_refuses():
    # Uncovered topic: a tangential raw-doc chunk must NOT trigger Tier 2 (leak fix).
    plan = _plan(wiki_hits=[], doc_hits=["chunk crudo"], in_roster=False)
    assert plan.action == "refuse"


def test_curated_wins_over_raw_docs():
    plan = _plan(wiki_hits=["pagina curada"], doc_hits=["chunk crudo"])
    assert plan.tier == "curado"


def test_data_question_invokes_with_no_context():
    # Nothing in wiki/docs, but it mentions data we have -> tools only.
    plan = _plan(has_data=True)
    assert plan.action == "invoke"
    assert plan.tier is None
    assert plan.context is None
    assert plan.verify is False


def test_nothing_found_refuses_without_model():
    plan = _plan(wiki_hits=[], doc_hits=[], has_data=False)
    assert plan.action == "refuse"
