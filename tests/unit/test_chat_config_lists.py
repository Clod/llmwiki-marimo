"""Tests for the per-wiki scope lists loaded by domain/chat/config.py.

The hybrid pre-retrieval's gate is driven by three hand-editable lists in
wiki_config.toml (one line per case, per wiki):
  [fuera_de_alcance] terminos = [...]   -> blacklist (never covered)
  [alias_datos] <canonical> = [...]     -> whitelist of aliases for data we have
  [falsos_sinonimos] <term> = [...]     -> pairs that are NOT synonyms
"""

from domain.chat.config import load_config


def test_loads_scope_lists(tmp_path):
    (tmp_path / "wiki_config.toml").write_text(
        "[fuera_de_alcance]\n"
        'terminos = ["cripto", "bitcoin"]\n\n'
        "[alias_datos]\n"
        'dolar = ["billete verde", "blue"]\n\n'
        "[falsos_sinonimos]\n"
        'cedear = ["accion", "acciones"]\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.off_limits == ["cripto", "bitcoin"]
    assert cfg.data_aliases == {"dolar": ["billete verde", "blue"]}
    assert cfg.false_synonyms == {"cedear": ["accion", "acciones"]}


def test_scope_lists_default_empty_when_sections_absent(tmp_path):
    (tmp_path / "wiki_config.toml").write_text(
        '[assistant]\nsystem_prompt = "hola"\n', encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.off_limits == []
    assert cfg.data_aliases == {}
    assert cfg.false_synonyms == {}


def test_scope_lists_default_empty_when_no_config_file(tmp_path):
    cfg = load_config(tmp_path)  # no wiki_config.toml at all
    assert cfg.off_limits == []
    assert cfg.data_aliases == {}
    assert cfg.false_synonyms == {}
