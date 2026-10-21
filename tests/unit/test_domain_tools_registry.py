"""Tests for domain/extensions/__init__.py — the registry of the domain subpackages."""

import shutil
from pathlib import Path

import pytest

import domain.extensions as tools
from domain.extensions import DomainTools, ToolRoles, activate_all, domains, roles

ROOT = Path(__file__).resolve().parents[2]


def test_registry_discovers_the_finance_domain():
    assert list(domains()) == ["finance_argentina"]


def test_roles_union_of_generic_and_domain_roles():
    merged = roles()
    assert merged.evidence == {
        "read_wiki_page", "query_dataset", "tasa_plazo_fijo", "tasa_caucion",
        "cotizacion_dolar", "precio_accion", "rendimiento_fci",
    }
    assert merged.verbatim == {"estimar_alternativas"}


def test_activate_all_is_none_without_datasets(tmp_path):
    shutil.copytree(ROOT / "examples" / "fairy-tales", tmp_path / "w")
    assert activate_all(tmp_path / "w") is None


def test_activate_all_merges_the_finance_domain(tmp_path):
    shutil.copytree(ROOT / "examples" / "finanzas-argentinas", tmp_path / "w")
    active = activate_all(tmp_path / "w")
    assert isinstance(active, DomainTools)
    assert {t.__name__ if hasattr(t, "__name__") else t.name for t in active.tools} == {
        "estimar_alternativas", "tasa_plazo_fijo", "tasa_caucion",
        "cotizacion_dolar", "precio_accion", "rendimiento_fci",
    }
    assert active.replaces_dataset_tool is True
    assert active.prompt


def test_duplicate_tool_name_across_domains_raises():
    class Fake:
        ROLES = ToolRoles(verbatim=frozenset({"estimar_alternativas"}))

    found = dict(domains())
    found["other"] = Fake
    with pytest.raises(ValueError, match="estimar_alternativas"):
        tools._check_unique_tool_names(found)


def test_every_domain_exposes_the_interface():
    for module in domains().values():
        assert isinstance(module.NAME, str) and isinstance(module.ROLES, ToolRoles)
        assert callable(module.activate) and callable(module.describe)
