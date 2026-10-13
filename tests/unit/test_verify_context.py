from pydantic_ai.messages import ModelRequest, ToolReturnPart

from domain.verify.context import context_from_messages


def message(tool, content):
    return ModelRequest(parts=[ToolReturnPart(tool_name=tool, content=content, tool_call_id="c")])


def test_a_read_page_becomes_one_passage_named_after_the_page():
    passages = context_from_messages([message("read_wiki_page", "[wiki page: wiki/concepts/x.md]\n\n# X\nTexto.")])
    assert [(p.page, p.tool) for p in passages] == [("/wiki/concepts/x.md", "read_wiki_page")]
    assert passages[0].text == "# X\nTexto."


def test_a_search_return_becomes_one_passage_per_hit():
    content = ("**2 wiki result(s)** for 'q':\n\n**/wiki/concepts/a.md** p.1 › Uno\n```\nTexto de a.\n```\n\n"
               "**/wiki/concepts/b.md**\n```\nTexto de b.\n```\n")
    passages = context_from_messages([message("search_wiki_fts", content)])
    assert [(p.page, p.text) for p in passages] == [
        ("/wiki/concepts/a.md", "Texto de a."), ("/wiki/concepts/b.md", "Texto de b.")]


def test_a_source_search_names_the_document():
    content = "**1 result(s)** for 'q':\n\n**Doc.docx** p.3\n```\nUn fragmento.\n```\n"
    assert context_from_messages([message("search_source_chunks", content)])[0].page == "Doc.docx"


def test_a_dataset_return_names_the_dataset_file():
    content = "**2 dataset row(s)** for categoria='dolar':\n\n| clave | valor |\n|---|---|\n| oficial | 1000 |"
    passage = context_from_messages([message("query_dataset", content)])[0]
    assert (passage.page, passage.tool) == ("datasets/dolar.md", "query_dataset")


def test_an_empty_or_failed_return_is_not_context():
    assert context_from_messages([message("read_wiki_page", "Page not found: x"),
                                  message("search_wiki_fts", "No wiki pages found for 'q'.")]) == []


def test_an_unrecognised_return_is_named_after_its_tool():
    passage = context_from_messages([message("estimar_alternativas", "| opción | tasa |\n|---|---|\n| a | 1 |")])[0]
    assert passage.page == "estimar_alternativas"


def test_messages_with_no_tool_returns_have_no_context():
    assert context_from_messages([]) == []
    assert context_from_messages(None) == []


def test_an_injected_block_keeps_its_wiki_label_and_a_source_block_takes_the_document_name():
    from domain.chat.preretrieval import context_passage

    assert context_passage("[/wiki/concepts/x.md]\nTexto.").page == "/wiki/concepts/x.md"
    assert context_passage("[datasets/dolar.md]\n| a | b |").page == "datasets/dolar.md"
    source = context_passage("[/raw/Doc.docx]\nFragmento.")
    assert (source.page, source.text) == ("Doc.docx", "Fragmento.")
    assert context_passage("sin etiqueta").page == ""
