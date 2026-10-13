"""The reading page carries two separators that resize the index and the chat."""

from __future__ import annotations

import re

from tests.web.conftest import WIKI_ID

PAGE = f"/w/{WIKI_ID}/pages/concepts/prince"


def separator(html: str, name: str) -> str:
    match = re.search(rf'<div class="separator separator-{name}"[^>]*>', html)
    assert match, f"no {name} separator in the page"
    return match.group(0)


def test_the_reading_page_has_a_separator_for_the_index_and_one_for_the_chat(client):
    html = client.get(PAGE).text
    for name in ("index", "chat"):
        tag = separator(html, name)
        for attribute in ('role="separator"', 'aria-orientation="vertical"', 'tabindex="0"', "aria-valuenow=",
                          "aria-valuemin=", "aria-valuemax=", "aria-label="):
            assert attribute in tag, f"{name}: {attribute}"
    assert 'aria-controls="index"' in separator(html, "index") and 'aria-controls="chat"' in separator(html, "chat")
    assert 'src="/static/js/panels.js"' in html


def test_the_labels_of_the_separators_are_translated(client):
    html = client.get(PAGE, cookies={"ui_lang": "es"}).text
    assert 'aria-label="Cambiar el ancho del índice"' in separator(html, "index")
    assert 'aria-label="Cambiar el ancho de la conversación"' in separator(html, "chat")


def test_the_widths_are_applied_before_the_first_paint(client):
    html = client.get(PAGE).text
    head = html.split("</head>")[0]
    assert "localStorage" in head and "panel-widths" in head


def test_the_page_text_has_no_width_cap(client):
    css = client.get("/static/css/reader.css").text
    rule = re.search(r"\.page-inner\s*\{[^}]*\}", css).group(0)
    assert "max-width" not in rule
    assert re.search(r"\.page-inner \.prose\s*\{[^}]*max-width:\s*none", css)


def test_a_separator_is_hidden_with_its_column_and_under_1100px(client):
    css = client.get("/static/css/reader.css").text
    assert re.search(r"\.separator\s*\{[^}]*display:\s*none", css)
    assert "body.index-open .separator-index" in css
    assert "body:not(.chat-hidden) .separator-chat" in css
    assert "cursor: col-resize" in css
    narrow = css.split("@media (max-width: 1100px)")[1]
    assert re.search(r"\.separator\s*\{[^}]*display:\s*none\s*!important", narrow)
