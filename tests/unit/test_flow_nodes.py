"""The node tables of the two flow documents match the tracing calls of the code.

``docs/query_nodes.md`` (reading) and ``docs/ingestion_nodes.md`` (writing) each
list their nodes in a table: ``| ID | What it does | Code | Trace | Status |``.
This test reads those tables and the Python code under ``base/`` (with ``ast``)
and compares them:

1. Every node that writes a record (its Trace cell does not start with ``none:``)
   has a tracing call in the code, and every tracing call has a row.
2. A node identifier appears in the code only in the files its row cites.
3. Every symbol that a Code cell cites is defined in the cited file.
4. A node identifier is unique within its document.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "base"
PRERETRIEVAL = "base/domain/chat/preretrieval.py"
# The files whose `refuse("R…", ...)` and `preretrieval.refuse("R…", ...)` calls
# record a refusal node: the pre-retrieval module and the two workflows of the
# chat checks.
REFUSING = {
    PRERETRIEVAL,
    "base/domain/chat/workflow/input_checks.py",
    "base/domain/chat/workflow/output_checks.py",
}

DOCS = {
    "reading": ROOT / "docs" / "query_nodes.md",
    "writing": ROOT / "docs" / "ingestion_nodes.md",
}
STRUCTURE = {"turn", "ingest", "document"}
_ID = re.compile(r"^[A-Z]{1,2}\d*[a-z]?$")


# ── The documents ─────────────────────────────────────────────────────────────

def _parse_doc(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5 or cells[0] == "ID" or not _ID.match(cells[0]):
            continue
        groups = []
        for group in cells[2].split(";"):
            tokens = re.findall(r"`([^`]+)`", group)
            if tokens:
                groups.append((tokens[0], tokens[1:]))
        rows.append({"id": cells[0], "code": groups, "trace": cells[3]})
    return rows


def _doc_rows(diagram: str) -> list[dict]:
    return _parse_doc(DOCS[diagram])


# ── The code ──────────────────────────────────────────────────────────────────

def _diagram_of(node: ast.expr, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        if node.id in ("READING", "WRITING"):
            return node.id.lower()
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute) and node.attr in ("READING", "WRITING"):
        return node.attr.lower()
    return None


def _str_literal(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _code_calls() -> list[tuple[str, str, str]]:
    """Every (node id, diagram, repo-relative file) found in the code of ``base/``."""
    found = []
    for path in sorted(BASE.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases: dict[str, str] = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                d = _diagram_of(n.value, {})
                if d:
                    aliases[n.targets[0].id] = d
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            func = n.func
            if (isinstance(func, ast.Attribute) and func.attr in ("node", "mark")
                    and isinstance(func.value, ast.Name) and func.value.id == "tracing"
                    and len(n.args) >= 2):
                node_id = _str_literal(n.args[0])
                diagram = _diagram_of(n.args[1], aliases)
                if node_id and node_id not in STRUCTURE and diagram:
                    found.append((node_id, diagram, rel))
            elif rel in REFUSING and n.args and (
                (isinstance(func, ast.Name) and func.id == "refuse")
                or (isinstance(func, ast.Attribute) and func.attr == "refuse"
                    and isinstance(func.value, ast.Name) and func.value.id == "preretrieval")
            ):
                node_id = _str_literal(n.args[0])
                if node_id:
                    found.append((node_id, "reading", rel))
        if rel == PRERETRIEVAL:
            for fn in ast.walk(tree):
                if isinstance(fn, ast.FunctionDef) and fn.name == "_answer_node":
                    for r in ast.walk(fn):
                        if isinstance(r, ast.Return) and r.value is not None:
                            # A return value may be a conditional: "Q13" if ... else "Q14".
                            for leaf in ast.walk(r.value):
                                node_id = _str_literal(leaf)
                                if node_id:
                                    found.append((node_id, "reading", rel))
    return found


def _defined_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()

    def visit(body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(stmt.name)
                if isinstance(stmt, ast.ClassDef):
                    visit(stmt.body)
            elif isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                names.add(stmt.target.id)

    visit(tree.body)
    return names


# ── The tests ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("diagram", ["reading", "writing"])
def test_node_ids_are_unique_in_the_document(diagram):
    ids = [r["id"] for r in _doc_rows(diagram)]
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    assert ids, f"no node rows found in {DOCS[diagram].name}"
    assert not duplicated, f"{DOCS[diagram].name}: node ids that appear in more than one row: {duplicated}"


@pytest.mark.parametrize("diagram", ["reading", "writing"])
def test_traced_nodes_match_tracing_calls(diagram):
    doc_ids = {r["id"] for r in _doc_rows(diagram) if not r["trace"].startswith("none:")}
    code_ids = {i for i, d, _ in _code_calls() if d == diagram}
    missing_in_code = sorted(doc_ids - code_ids)
    missing_in_doc = sorted(code_ids - doc_ids)
    assert not (missing_in_code or missing_in_doc), (
        f"{diagram} diagram ({DOCS[diagram].name}):\n"
        f"  in the document with a record, with no tracing call in base/: {missing_in_code}\n"
        f"  with a tracing call in base/, not in the document (or marked 'none:'): {missing_in_doc}"
    )


@pytest.mark.parametrize("diagram", ["reading", "writing"])
def test_node_ids_occur_only_in_cited_files(diagram):
    cited = {r["id"]: {f for f, _ in r["code"]} for r in _doc_rows(diagram)}
    problems = []
    for node_id, d, rel in _code_calls():
        if d != diagram or node_id not in cited:
            continue
        if rel not in cited[node_id]:
            problems.append(f"{node_id} is traced in {rel}, which its Code cell does not cite "
                            f"(cites: {sorted(cited[node_id])})")
    assert not problems, f"{DOCS[diagram].name}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("diagram", ["reading", "writing"])
def test_cited_symbols_are_defined_in_cited_files(diagram):
    problems = []
    for row in _doc_rows(diagram):
        for rel, symbols in row["code"]:
            path = ROOT / rel
            if not path.exists():
                problems.append(f"{row['id']}: file {rel} does not exist")
                continue
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".py":
                defined = _defined_names(ast.parse(text))
                for s in symbols:
                    if s not in defined:
                        problems.append(f"{row['id']}: {rel} does not define `{s}`")
            else:
                for s in symbols:
                    if not re.search(rf"\b{re.escape(s)}\b", text):
                        problems.append(f"{row['id']}: `{s}` does not appear in {rel}")
    assert not problems, f"{DOCS[diagram].name}:\n  " + "\n  ".join(problems)
