"""Configuración, block Models, in a real browser: the help button of a model test opens
its dialog, and the dialog closes with Escape and with its "Close" button."""

from __future__ import annotations

import dataclasses

from playwright.sync_api import expect

from tests.web.e2e.server import WIKI_ID


def test_the_help_button_opens_the_dialog_and_escape_closes_it(page, live, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_BASE_URL=http://llm.invalid/v1\nLLM_MODEL=model\n", encoding="utf-8")
    state = live.app.state.web
    state.settings = dataclasses.replace(state.settings, env_file=env_file)
    page.goto(f"{live.url}/w/{WIKI_ID}/settings")

    dialog = page.locator("#help-llm")
    expect(dialog).to_be_hidden()
    page.locator('[data-help="help-llm"]').click()
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("heading", name="Prueba del modelo de lectura")).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()

    page.locator('[data-help="help-vision"]').click()
    expect(page.locator("#help-vision")).to_be_visible()
    page.locator("#help-vision").get_by_role("button", name="Cerrar").click()
    expect(page.locator("#help-vision")).to_be_hidden()
    assert env_file.read_text(encoding="utf-8").startswith("LLM_BASE_URL=")
