"""Tests for vocabulary_check — the linter guard over the alias vocabulary.

Crosses the effective alias map (generated ⊕ hand overrides) against the wiki's
live coverage (concept pages + dataset vocab), catching drift the ingest-time
generator can't: a hand-added or roster-shifted collision, a stale entry, an
ambiguous alias, and an off-limits term that is now actually covered.
"""

from domain.lint.checks import vocabulary_check
from domain.tools.wiki_fs import create_page
from tests.helpers.workspace import WorkspaceFixture


def _write_config(workspace, body: str) -> None:
    (workspace / "wiki_config.toml").write_text(body, encoding="utf-8")


def _concept(fx: WorkspaceFixture, slug: str, title: str) -> None:
    create_page(fx.db_path, fx.workspace, "/wiki/concepts/", slug, title,
                f"# {title}\n\ncontent\n", [])


def test_vocab_collision_is_error(tmp_workspace: WorkspaceFixture) -> None:
    _concept(tmp_workspace, "acciones", "Acciones")
    _concept(tmp_workspace, "cedear", "CEDEAR")
    _write_config(tmp_workspace.workspace,
                  '[alias_datos]\n"CEDEAR" = ["acciones", "certificado"]\n')
    issues = vocabulary_check(tmp_workspace.db_path, tmp_workspace.workspace)
    collisions = [i for i in issues if i.check == "vocab_collision"]
    assert len(collisions) == 1
    assert collisions[0].severity == "error"
    assert "acciones" in collisions[0].description.lower()
    assert collisions[0].alias == "acciones"  # structured field for the auto-repair


def test_vocab_stale_when_canonical_uncovered(tmp_workspace: WorkspaceFixture) -> None:
    _write_config(tmp_workspace.workspace,
                  '[alias_datos]\n"Bono" = ["titulo publico"]\n')
    issues = vocabulary_check(tmp_workspace.db_path, tmp_workspace.workspace)
    assert any(i.check == "vocab_stale" and i.severity == "warning" for i in issues)


def test_vocab_ambiguous_alias_two_canonicals(tmp_workspace: WorkspaceFixture) -> None:
    _concept(tmp_workspace, "dolar", "Dólar")
    _concept(tmp_workspace, "euro", "Euro")
    _write_config(tmp_workspace.workspace,
                  '[alias_datos]\n"Dólar" = ["divisa"]\n"Euro" = ["divisa"]\n')
    issues = vocabulary_check(tmp_workspace.db_path, tmp_workspace.workspace)
    amb = [i for i in issues if i.check == "vocab_ambiguous"]
    assert len(amb) == 1
    assert "divisa" in amb[0].description.lower()


def test_vocab_covered_offlimits_now_has_page(tmp_workspace: WorkspaceFixture) -> None:
    _concept(tmp_workspace, "cedear", "CEDEAR")
    _write_config(tmp_workspace.workspace,
                  '[fuera_de_alcance]\nterminos = ["cedear"]\n')
    issues = vocabulary_check(tmp_workspace.db_path, tmp_workspace.workspace)
    assert any(i.check == "vocab_covered" and i.severity == "info" for i in issues)


def test_vocab_clean_config_no_issues(tmp_workspace: WorkspaceFixture) -> None:
    _concept(tmp_workspace, "dolar", "Dólar")
    _write_config(tmp_workspace.workspace,
                  '[alias_datos]\n"Dólar" = ["blue", "billete verde"]\n')
    assert vocabulary_check(tmp_workspace.db_path, tmp_workspace.workspace) == []
