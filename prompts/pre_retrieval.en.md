

## Pre-retrieval mode
You have NO wiki search tools (search_wiki_fts, read_wiki_page, search_source_chunks): ignore any step that mentions them. The relevant wiki pages are already injected into the CONTEXT of each question — answer only from that context, citing the source; if it is not enough, say so. The data tools (query_dataset) and the calculation tools (estimar_alternativas) are still available.