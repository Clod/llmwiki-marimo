"""The Home screen: the logo and the name of the wiki beside the chat, with no page open, and
the favicon of every screen."""

from __future__ import annotations

import re

from tests.web.conftest import WIKI_ID

URL = f"/w/{WIKI_ID}/home"


def test_the_home_screen_shows_the_logo_and_the_name_of_the_wiki(client):
    response = client.get(URL)
    assert response.status_code == 200
    html = response.text
    assert '<img class="home-logo" src="/static/img/logo.svg"' in html
    assert f'<h1 class="home-name">{WIKI_ID}</h1>' in html
    assert "es sobre la wiki entera" in html


def test_the_chat_of_the_home_screen_sends_no_open_page(client):
    html = client.get(URL).text
    composer = html[html.index('id="composer"'):]
    assert re.search(r'name="open_page" value=""', composer.split("</form>")[0])


def test_the_home_screen_has_no_page_actions(client):
    html = client.get(URL).text
    assert f'href="/w/{WIKI_ID}/edit/' not in html
    assert f'href="/w/{WIKI_ID}/history/page/' not in html


def test_a_page_links_to_the_home_screen_beside_edit_and_history(client):
    html = client.get(f"/w/{WIKI_ID}/pages/overview").text
    actions = html[html.index('<div class="actions">'):].split("</div>")[0]
    assert f'href="/w/{WIKI_ID}/home"' in actions and ">Inicio</a>" in actions
    assert actions.index("/home") < actions.index("/edit/") < actions.index("/history/page/")


def test_every_screen_declares_the_favicon(client):
    html = client.get(URL).text
    for href in ("/static/img/favicon.ico", "/static/img/favicon.svg", "/static/img/apple-touch-icon.png"):
        assert f'href="{href}"' in html
        assert client.get(href).status_code == 200


def test_the_home_screen_of_an_unknown_wiki_is_a_404(client):
    assert client.get("/w/nope/home").status_code == 404
