"""The Vocabulario screen: what it shows and the changes it makes, on the `tales` wiki."""

from __future__ import annotations

import subprocess

import pytest

from domain.chat.config import load_config
from domain.chat.vocabulary import write_generated_aliases
from tests.web.conftest import WIKI_ID

URL = f"/w/{WIKI_ID}/vocabulary"


@pytest.fixture
def vocab_wiki(wiki):
    (wiki.path / "wiki_config.toml").write_text(
        "# Hand-written comment that must survive.\n"
        "[fuera_de_alcance]\nterminos = [\"cripto\"]\n\n"
        "[alias_datos]\nCinderella = [\"Cenicienta\"]\n", encoding="utf-8")
    write_generated_aliases(wiki.path, {"Prince": ["Principe", "Royal"]})
    return wiki


def post(client, op, **data):
    return client.post(f"{URL}/{op}", data=data)


def last_commit(wiki) -> str:
    return subprocess.run(["git", "log", "-1", "--format=%s"], cwd=wiki.path, capture_output=True, text=True).stdout.strip()


def test_the_tab_and_the_four_panels(client, vocab_wiki):
    html = client.get(URL).text
    assert f'href="{URL}" aria-current="page"' in html and ">Vocabulario</a>" in html
    for heading in ("Padrón · ", "Alias · ", "Lista negra · 1", "Alias descartados · 0"):
        assert heading in html
    assert 'data-name="Cinderella"' in html and f'href="/w/{WIKI_ID}/pages/concepts/cinderella"' in html
    assert ">Cenicienta</span>" in html and ">manual</span>" in html and ">generado</span>" in html


def test_the_screen_in_english(en_client, vocab_wiki):
    html = en_client.get(URL).text
    for text in ("Roster · ", "Aliases · ", "Blacklist · 1", "Rejected aliases · 0", ">Vocabulary</a>"):
        assert text in html


def test_adding_a_blacklist_term_commits_and_keeps_the_comment(client, vocab_wiki):
    html = post(client, "blacklist-add", term="bitcoin").text
    assert html.lstrip().startswith('<div class="vocab-body" id="vocab-body">') and "Lista negra · 2" in html
    assert load_config(vocab_wiki.path).off_limits == ["cripto", "bitcoin"]
    assert "# Hand-written comment that must survive." in (vocab_wiki.path / "wiki_config.toml").read_text()
    assert last_commit(vocab_wiki) == 'vocabulary: blacklist "bitcoin"'


def test_a_refused_change_says_why_in_its_panel(client, vocab_wiki):
    html = post(client, "blacklist-add", term="cinderella").text
    assert "«cinderella» es un tema que la wiki cubre" in html
    html = post(client, "alias-add", canonical="Dragon", term="Wyrm").text
    assert "«Dragon» no está en el padrón" in html
    html = post(client, "blacklist-add", term="CRIPTO").text
    assert "«CRIPTO» ya está en la lista." in html


def test_rejecting_a_generated_alias_moves_it_to_the_rejected_aliases(client, vocab_wiki):
    html = post(client, "alias-reject", canonical="Prince", term="Royal").text
    assert "Alias descartados · 1" in html and "<b>Royal</b>" in html
    assert load_config(vocab_wiki.path).data_aliases["Prince"] == ["Principe"]
    html = post(client, "rejected-remove", canonical="Prince", term="Royal").text
    assert "Alias descartados · 0" in html
    assert load_config(vocab_wiki.path).data_aliases["Prince"] == ["Principe", "Royal"]


def test_a_hand_alias_is_added_and_removed(client, vocab_wiki):
    post(client, "alias-add", canonical="prince", term="El príncipe")
    assert "El príncipe" in load_config(vocab_wiki.path).data_aliases["Prince"]
    post(client, "alias-remove", canonical="Prince", term="El príncipe")
    assert "El príncipe" not in load_config(vocab_wiki.path).data_aliases["Prince"]


def test_a_change_waits_for_no_running_operation(client, app, vocab_wiki):
    entry = app.state.web.get_wiki(WIKI_ID)
    assert entry.busy.acquire(blocking=False)
    try:
        html = post(client, "blacklist-add", term="bitcoin").text
    finally:
        entry.busy.release()
    assert "Hay otra operación en curso sobre esta wiki." in html
    assert load_config(vocab_wiki.path).off_limits == ["cripto"]


def test_an_unknown_change_is_a_404(client, vocab_wiki):
    assert post(client, "everything-delete", term="x").status_code == 404


def test_a_dataset_name_opens_the_dataset_dialog(client, vocab_wiki):
    ds = vocab_wiki.path / "datasets"
    ds.mkdir()
    (ds / "zapatos.md").write_text(
        "---\ntype: dataset\ncategoria: zapatos\nformato: largo\nclave: material\nmetricas: { precio: \"ARS\" }\n"
        "as_of: 2026-06-25\nfuente: feria\n---\n\n| material | precio |\n|---|---|\n| Cristal | 10 |\n| Cuero | 20 |\n",
        encoding="utf-8")
    html = client.get(URL).text
    assert 'class="roster-data"' in html and '"key": "Cristal"' in html and '"category": "zapatos"' in html
    dialog = client.get(f"{URL}/dataset", params={"key": "cristal"}).text
    assert '<dialog class="dataset"' in dialog and "fuente: feria" in dialog
    assert '<h2 id="dataset-title" class="mono">datasets/zapatos.md</h2>' in dialog
    assert '<tr class="marked" aria-current="true"><th scope="row">Cristal</th>' in dialog
    assert client.get(f"{URL}/dataset", params={"key": "Madera"}).status_code == 404
