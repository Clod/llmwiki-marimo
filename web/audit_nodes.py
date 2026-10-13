"""The short text of each node of the reading diagram, for the audit page of an answer.

The texts are the ones of `docs/query_nodes.md`, shortened. English is the source
language; the Spanish text is in `locale/es/LC_MESSAGES/messages.po`. A node that is not
here is shown by its identifier alone (`text`).
"""

from __future__ import annotations

from web.i18n import N_, _

NODE_TEXTS: dict[str, str] = {
    "C1": N_("History of the turn: refused exchanges removed, last turns kept"),
    "C2": N_("Mode of the turn"),
    "Q1": N_("The question enters pre-retrieval"),
    "Q2": N_("The question normalised (lowercase, no accents)"),
    "Q3": N_("Does the question name a blacklisted term?"),
    "Q4": N_("The question reduced to stems"),
    "Q5": N_("Does the question name a topic of the wiki (coverage roster)?"),
    "Q5a": N_("Is the user reading a wiki page?"),
    "Q5b": N_("Follow-up rewritten so that it stands alone"),
    "Q6": N_("Search in the wiki pages"),
    "Q7": N_("Did the search find a page?"),
    "Q7a": N_("Does the question name a dataset category?"),
    "Q7b": N_("Does the question name a key of that category?"),
    "Q7c": N_("Rows of the key read from the dataset"),
    "Q7d": N_("Key names of the category read from the dataset"),
    "Q8": N_("Search in the source documents"),
    "Q9": N_("Is the question about the whole wiki?"),
    "Q10": N_("Is it a data question?"),
    "Q11": N_("Did the source search find a fragment?"),
    "Q12": N_("Refusal chosen by the coverage roster"),
    "Q13": N_("Wiki pages given to the model, which writes the answer"),
    "Q14": N_("Overview and index given to the model"),
    "Q15": N_("No page given: the model calls the data tools"),
    "Q16": N_("Source fragments given to the model"),
    "Q17": N_("Is the answer grounded in the fragments?"),
    "Q18": N_("Warning appended: the answer comes from a source document"),
    "Q19": N_("Did a data tool return evidence?"),
    "Q20": N_("Does the answer open with the fixed refusal sentence?"),
    "Q21": N_("The answer is shown, with its citation"),
    "R1": N_("Refusal: blacklisted term"),
    "R2a": N_("Refusal: no information received from the wiki"),
    "R2b": N_("Refusal: a topic of the wiki, no text found"),
    "R3": N_("Refusal: the answer is not grounded in the source document"),
    "R4": N_("Refusal: no evidence behind the answer"),
    "R5": N_("Refusal: the model did not find the answer"),
    "T1": N_("The model answers, calling the wiki and data tools"),
    "T2": N_("Did a tool return evidence?"),
    "T3": N_("Refusal of the guardrail: the answer could not be backed"),
    "T4": N_("Advisory table and citation added"),
    "T5": N_("The answer is shown"),
    "F1": N_("The model answers, calling the tools; the text is shown as it arrives"),
    "F2": N_("The answer is complete, with no check"),
    "K1": N_("Tool: read a wiki page"),
    "K2": N_("Tool: search the wiki pages"),
    "K3": N_("Tool: search the source documents"),
    "K4": N_("Tool: read a dataset"),
    "K5": N_("Tool: estimate investment alternatives"),
    "K6": N_("Tool: plazo fijo rate"),
    "K7": N_("Tool: caución rate"),
    "K8": N_("Tool: dollar quote"),
    "K9": N_("Tool: share price"),
    "K10": N_("Tool: money market fund return"),
}


def text(node_id: str) -> str:
    """The short text of a node in the interface language; '' when the node has none."""
    return _(NODE_TEXTS[node_id]) if node_id in NODE_TEXTS else ""
