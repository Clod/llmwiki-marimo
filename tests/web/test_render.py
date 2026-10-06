"""Unit tests of web/render.py: the rewriting of references into application URLs."""

from web.render import page_title, render_markdown

PAGES = {"concepts/cinderella", "concepts/prince", "summaries/tale", "overview"}
URL = "/w/tales/pages/"


def render(text, page=None, sources=frozenset()):
    return render_markdown("tales", text, page, PAGES, sources)


def test_a_markdown_link_in_a_page_is_relative_to_the_page_directory():
    html = render("See [the prince](prince.md) and [the tale](../summaries/tale.md).", page="concepts/cinderella")
    assert f'<a href="{URL}concepts/prince">the prince</a>' in html
    assert f'<a href="{URL}summaries/tale">the tale</a>' in html


def test_a_bare_reference_becomes_a_link_in_its_three_forms():
    for reference in ("/wiki/concepts/prince.md", "wiki/concepts/prince.md"):
        assert f'<a href="{URL}concepts/prince">{reference}</a>' in render(f"Fuente: {reference}")
    # A bare file name links only when exactly one page has that name.
    assert f'<a href="{URL}concepts/prince">prince.md</a>' in render("Fuente: prince.md")


def test_a_bare_file_name_shared_by_two_pages_is_not_linked():
    html = render_markdown("tales", "Fuente: x.md", None, {"concepts/x", "summaries/x"})
    assert "<a " not in html


def test_a_reference_inside_parentheses_is_linked():
    html = render("Cinderella lost it (Referencia: wiki/concepts/cinderella.md).")
    assert f'<a href="{URL}concepts/cinderella">wiki/concepts/cinderella.md</a>)' in html


def test_a_source_document_name_links_to_the_document_and_opens_in_a_new_tab():
    html = render("Ver 10 Plazos Fijos.docx para el detalle.", sources={"10 Plazos Fijos.docx"})
    assert 'href="/w/tales/view/10%20Plazos%20Fijos.docx"' in html
    assert 'target="_blank"' in html and 'rel="noopener"' in html


def test_links_that_must_stay_untouched():
    html = render("[site](https://example.com/a.md) [mail](mailto:a@b.c) [top](#top) "
                  "[gone](missing.md) ![pic](pic.md)", page="concepts/cinderella")
    assert 'href="https://example.com/a.md"' in html
    assert 'href="mailto:a@b.c"' in html and 'href="#top"' in html
    assert 'href="missing.md"' in html
    assert URL not in html


def test_a_bare_reference_to_a_missing_page_stays_plain_text():
    html = render("Fuente: wiki/concepts/missing.md")
    assert "<a " not in html and "wiki/concepts/missing.md" in html


def test_the_front_matter_is_not_rendered():
    html = render("---\ntitle: Prince\n---\n\nHello.")
    assert "title: Prince" not in html and "Hello." in html


def test_page_title_prefers_front_matter_then_h1_then_the_id():
    assert page_title("---\ntitle: The Prince\n---\n\n# Other\n", "concepts/prince") == "The Prince"
    assert page_title("# Other\n\ntext", "concepts/prince") == "Other"
    assert page_title("text only", "concepts/prince-charming") == "prince charming"


# ── Sanitizing ───────────────────────────────────────────────────────────────

def test_a_script_tag_is_removed_with_its_content():
    html = render("Hola\n\n<script>alert('xss')</script>\n\nChau")
    assert "<script" not in html and "alert" not in html
    assert "Hola" in html and "Chau" in html


def test_an_event_handler_attribute_is_removed():
    html = render('<img src="x.png" onerror="alert(1)" alt="a"> <b onclick="alert(2)">negrita</b>')
    assert "onerror" not in html and "onclick" not in html and "alert" not in html
    assert '<img src="x.png" alt="a">' in html and "<b>negrita</b>" in html


def test_a_javascript_link_loses_its_href():
    html = render("[clic](javascript:alert(1)) <a href=\"JaVaScRiPt:alert(2)\">dos</a> "
                  "[datos](data:text/html;base64,PHNjcmlwdD4=)")
    assert "javascript" not in html.lower() and "alert" not in html and "data:" not in html
    assert "clic" in html and "dos" in html


def test_active_elements_are_removed():
    html = render('<iframe src="https://evil.example"></iframe><form action="/x"><input name="a"></form>'
                  "<style>body{display:none}</style><object data=\"x\"></object>")
    for tag in ("<iframe", "<form", "<input", "<style", "<object"):
        assert tag not in html
    assert "display:none" not in html


def test_the_text_of_a_page_cannot_set_the_target_or_rel_of_a_link():
    html = render('<a href="https://example.com" target="_blank" rel="opener">x</a>')
    assert "target" not in html and "opener" not in html


def test_what_the_pages_and_answers_use_survives():
    text = ("# Titulo\n\n- uno\n- dos\n\nentre listas\n\n1. a\n2. b\n\n> cita\n\n"
            "| a | b |\n|:--|--:|\n| 1 | 2 |\n\n```python\nx = 1 < 2\n```\n\n"
            "`en linea` **negrita** *cursiva*\n\n![pic](pic.png \"t\")\n\n---\n\n"
            "[sitio](https://example.com \"Ejemplo\") [mail](mailto:a@b.c) [ancla](#top)\n")
    html = render(text, page="concepts/cinderella")
    for fragment in ("<h1>Titulo</h1>", "<ul>", "<ol>", "<blockquote>", "<table>", "<thead>", "<tbody>",
                     '<th style="text-align:left">a</th>', '<td style="text-align:right">2</td>',
                     '<code class="language-python">x = 1 &lt; 2', "<code>en linea</code>",
                     "<strong>negrita</strong>", "<em>cursiva</em>", "<hr>",
                     '<a href="https://example.com" title="Ejemplo">sitio</a>',
                     '<a href="mailto:a@b.c">mail</a>', '<a href="#top">ancla</a>'):
        assert fragment in html, fragment
    assert 'src="pic.png"' in html and 'alt="pic"' in html and 'title="t"' in html


def test_style_other_than_the_alignment_of_a_table_is_dropped():
    html = render('| a |\n|:-:|\n| 1 |\n\n<table><tr><td style="position:fixed;text-align:left">x</td></tr></table>')
    assert "position" not in html and "text-align" in html


def _events(html: str) -> list:
    """The tags, attributes and text of `html`, so that two serializations of the
    same document compare equal (`<hr />` and `<hr>`, `&quot;` and `"`)."""
    from html.parser import HTMLParser

    out: list = []

    class Collect(HTMLParser):
        def handle_starttag(self, tag, attrs):
            out.append((tag, tuple(sorted(attrs))))

        def handle_endtag(self, tag):
            out.append(("/" + tag,))

        def handle_data(self, data):
            if data.strip():
                out.append(" ".join(data.split()))

    parser = Collect(convert_charrefs=True)
    parser.feed(html)
    parser.close()
    return out


def test_every_page_of_the_finance_demo_renders_as_before(monkeypatch):
    from pathlib import Path

    import web.render as render_module

    root = Path(__file__).resolve().parents[2] / "examples" / "finanzas-argentinas"
    paths = sorted((root / "wiki").rglob("*.md"))
    assert len(paths) > 30
    pages = {p.relative_to(root / "wiki").with_suffix("").as_posix() for p in paths}
    sources = {p.name for p in (root / "sources").iterdir()}
    checked_links = 0
    for path in paths:
        page = path.relative_to(root / "wiki").with_suffix("").as_posix()
        text = path.read_text(encoding="utf-8")
        after = render_markdown("finanzas", text, page, pages, sources)
        with monkeypatch.context() as m:
            m.setattr(render_module, "sanitize_html", lambda html: html)
            before = render_markdown("finanzas", text, page, pages, sources)
        assert _events(after) == _events(before), page
        checked_links += after.count("<a ")
    assert checked_links > 100


def test_a_source_named_like_a_page_links_to_the_source_not_to_the_page():
    """`notas.md` is a file of sources/ and also the file name of the page
    `notas` (and of the summary of that source): the name is the source."""
    pages = PAGES | {"notas", "summaries/notas"}
    text = "## Fuentes\n\n- notas.md\n"
    for page in ("concepts/cinderella", None, "notas"):
        html = render_markdown("tales", text, page, pages, {"notas.md"})
        assert 'href="/w/tales/view/notas.md"' in html, page
        assert "/pages/" not in html, page


def test_without_a_source_of_that_name_a_bare_name_still_links_to_the_page():
    html = render_markdown("tales", "Fuente: notas.md", None, PAGES | {"notas"}, frozenset())
    assert f'<a href="{URL}notas">notas.md</a>' in html
