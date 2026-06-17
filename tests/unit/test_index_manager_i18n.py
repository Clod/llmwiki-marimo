"""Phase-3 i18n tests for index_manager.py.

Verifies that:
- update_index(language="es") seeds a missing index.md with the Spanish title
  and section headers, and places entries under the Spanish section.
- remove_index_entry(language="es") removes entries from the Spanish section.
- The English default (no language arg) is unchanged — regression guard.

Assertions are built from domain.i18n.get_locale() rather than hardcoded
Spanish strings, so this test stays correct even if the Locale wording changes.
"""

from pathlib import Path

from domain.i18n import get_locale
from domain.ingestion.index_manager import remove_index_entry, update_index

_LOC_ES = get_locale("es")
_LOC_EN = get_locale("en")


# ── update_index — Spanish ────────────────────────────────────────────────────


class TestUpdateIndexSpanishSeed:
    """update_index(language='es') must seed a missing index.md in Spanish."""

    def test_seeds_spanish_title(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        (workspace / "wiki").mkdir(parents=True)
        update_index(workspace, "summaries/x.md", "Resumen de prueba", "summaries", language="es")
        text = (workspace / "wiki" / "index.md").read_text(encoding="utf-8")
        assert f"# {_LOC_ES.index_title}" in text

    def test_seeds_spanish_summaries_section(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        (workspace / "wiki").mkdir(parents=True)
        update_index(workspace, "summaries/x.md", "Resumen de prueba", "summaries", language="es")
        text = (workspace / "wiki" / "index.md").read_text(encoding="utf-8")
        assert f"## {_LOC_ES.index_section_summaries}" in text

    def test_seeds_spanish_concepts_section(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        (workspace / "wiki").mkdir(parents=True)
        update_index(workspace, "summaries/x.md", "Resumen de prueba", "summaries", language="es")
        text = (workspace / "wiki" / "index.md").read_text(encoding="utf-8")
        assert f"## {_LOC_ES.index_section_concepts}" in text

    def test_no_english_title_in_spanish_seed(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        (workspace / "wiki").mkdir(parents=True)
        update_index(workspace, "summaries/x.md", "Resumen de prueba", "summaries", language="es")
        text = (workspace / "wiki" / "index.md").read_text(encoding="utf-8")
        assert f"# {_LOC_EN.index_title}" not in text


class TestUpdateIndexSpanishEntryPlacement:
    """Entries must be placed under the localized section matching their category."""

    def test_summary_entry_under_spanish_summaries_section(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        (workspace / "wiki").mkdir(parents=True)
        update_index(workspace, "summaries/mi-doc.md", "Sobre finanzas", "summaries", language="es")
        text = (workspace / "wiki" / "index.md").read_text(encoding="utf-8")
        summaries_pos = text.index(f"## {_LOC_ES.index_section_summaries}")
        concepts_pos = text.index(f"## {_LOC_ES.index_section_concepts}")
        entry_pos = text.index("[Mi Doc]")
        assert summaries_pos < entry_pos < concepts_pos

    def test_concept_entry_under_spanish_concepts_section(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        (workspace / "wiki").mkdir(parents=True)
        update_index(workspace, "concepts/banco-central.md", "Regulador", "concepts", language="es")
        text = (workspace / "wiki" / "index.md").read_text(encoding="utf-8")
        concepts_pos = text.index(f"## {_LOC_ES.index_section_concepts}")
        entry_pos = text.index("[Banco Central]")
        assert entry_pos > concepts_pos

    def test_update_existing_spanish_entry_no_duplicate(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        (workspace / "wiki").mkdir(parents=True)
        update_index(workspace, "summaries/doc.md", "Primer resumen", "summaries", language="es")
        update_index(workspace, "summaries/doc.md", "Resumen actualizado", "summaries", language="es")
        text = (workspace / "wiki" / "index.md").read_text(encoding="utf-8")
        assert text.count("[Doc](summaries/doc.md)") == 1
        assert "Resumen actualizado" in text
        assert "Primer resumen" not in text


# ── update_index — English regression guard ───────────────────────────────────


class TestUpdateIndexEnglishRegression:
    """update_index() with no language arg (default 'en') must be unchanged."""

    def test_seeds_english_title(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        (workspace / "wiki").mkdir(parents=True)
        update_index(workspace, "summaries/doc.md", "A doc", "summaries")
        text = (workspace / "wiki" / "index.md").read_text(encoding="utf-8")
        assert f"# {_LOC_EN.index_title}" in text

    def test_uses_english_summaries_section(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        (workspace / "wiki").mkdir(parents=True)
        update_index(workspace, "summaries/doc.md", "A doc", "summaries")
        text = (workspace / "wiki" / "index.md").read_text(encoding="utf-8")
        assert f"## {_LOC_EN.index_section_summaries}" in text

    def test_no_spanish_title_in_english_seed(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        (workspace / "wiki").mkdir(parents=True)
        update_index(workspace, "summaries/doc.md", "A doc", "summaries")
        text = (workspace / "wiki" / "index.md").read_text(encoding="utf-8")
        assert f"# {_LOC_ES.index_title}" not in text


# ── remove_index_entry — Spanish ──────────────────────────────────────────────


class TestRemoveIndexEntrySpanish:
    """remove_index_entry(language='es') must operate on the Spanish section."""

    def test_removes_entry_from_spanish_summaries_section(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        (workspace / "wiki").mkdir(parents=True)
        update_index(workspace, "summaries/doc.md", "Resumen", "summaries", language="es")
        remove_index_entry(workspace, "summaries/doc.md", "summaries", language="es")
        text = (workspace / "wiki" / "index.md").read_text(encoding="utf-8")
        assert "[Doc](summaries/doc.md)" not in text
        # Spanish section headers remain intact.
        assert f"## {_LOC_ES.index_section_summaries}" in text

    def test_removes_entry_from_spanish_concepts_section(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        (workspace / "wiki").mkdir(parents=True)
        update_index(workspace, "concepts/inflacion.md", "Indicador", "concepts", language="es")
        remove_index_entry(workspace, "concepts/inflacion.md", "concepts", language="es")
        text = (workspace / "wiki" / "index.md").read_text(encoding="utf-8")
        assert "[Inflacion]" not in text

    def test_noop_when_index_missing(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        (workspace / "wiki").mkdir(parents=True)
        # Should not raise even though wiki/index.md doesn't exist yet.
        remove_index_entry(workspace, "summaries/doc.md", "summaries", language="es")
        assert not (workspace / "wiki" / "index.md").exists()


# ── remove_index_entry — English regression guard ─────────────────────────────


class TestRemoveIndexEntryEnglishRegression:
    """remove_index_entry() with no language arg (default 'en') must be unchanged."""

    def test_removes_entry_from_english_summaries_section(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        (workspace / "wiki").mkdir(parents=True)
        update_index(workspace, "summaries/doc.md", "A summary", "summaries")
        remove_index_entry(workspace, "summaries/doc.md", "summaries")
        text = (workspace / "wiki" / "index.md").read_text(encoding="utf-8")
        assert "[Doc](summaries/doc.md)" not in text
        assert f"## {_LOC_EN.index_section_summaries}" in text
