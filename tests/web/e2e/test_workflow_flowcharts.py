"""The generated diagrams of the audit page render with the bundled Mermaid.

Each flowchart of the four workflow graphs, in each interface language, goes to
`mermaid.render` with the settings of `web/static/js/audit-diagram.js`. The test
fails when Mermaid reports an error or draws no SVG. No server runs: the page
loads `web/static/js/mermaid.min.js` from disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from web.audit_diagram import WORKFLOW_GRAPHS, workflow_diagram
from web.i18n import LANGUAGES, use_language

MERMAID = Path(__file__).resolve().parents[3] / "web" / "static" / "js" / "mermaid.min.js"

RENDER = """async (text) => {
  mermaid.initialize({
    startOnLoad: false, securityLevel: "strict", htmlLabels: false,
    flowchart: { useMaxWidth: false, htmlLabels: false }, themeVariables: { fontSize: "12px" },
  });
  try {
    const { svg } = await mermaid.render("diagram" + Math.random().toString(36).slice(2), text);
    return svg.includes("<svg") ? "" : "no svg";
  } catch (error) {
    return String(error && error.message || error);
  }
}"""

CASES = [(mode, side, lang) for mode in WORKFLOW_GRAPHS for side in (0, 1) for lang in LANGUAGES]


@pytest.fixture(scope="module")
def mermaid_page(browser):
    page = browser.new_page()
    page.set_content("<!doctype html><html><body></body></html>")
    page.add_script_tag(path=str(MERMAID))
    yield page
    page.close()


@pytest.mark.parametrize(("mode", "side", "lang"), CASES)
def test_each_generated_flowchart_renders_without_error(mermaid_page, mode, side, lang):
    with use_language(lang):
        text = workflow_diagram(WORKFLOW_GRAPHS[mode][side], [])
    assert mermaid_page.evaluate(RENDER, text) == ""
