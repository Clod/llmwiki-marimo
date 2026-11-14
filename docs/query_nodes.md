# Query nodes

This document is the reading diagram of the wiki: the steps that answer a
question in the chat. Its sibling, [`ingestion_nodes.md`](ingestion_nodes.md), is
the writing diagram: the steps that turn a source document into wiki pages and
put each fragment of text into the index.

Each step is a node with an identifier, for example `Q6`. The identifier of a
node appears in three places:

1. In this document, in a node table, and in a diagram of this document or of
   [`workflow_diagrams.md`](workflow_diagrams.md).
2. In the code, as the first argument of the tracing call that marks the node:
   `tracing.node("Q6", R, ...)` or `tracing.mark("Q5b", R, ...)`
   (`base/domain/tracing.py`).
3. In the trace of a chat turn, as the name of the record that the tracing call
   writes. The trace is written only when the environment variable `WIKI_TRACE`
   is set to `1`; the records go to `<workspace>/.llmwiki/traces/spans.jsonl`.

The test `tests/unit/test_flow_nodes.py` compares the node tables of this
document with the tracing calls of the code. A node added, renamed or removed in
the code and not in this document, or in this document and not in the code,
fails the test.

The chat has three modes. The mode decides who retrieves the text that the
answer is built from:

- **pre-retrieval**: the code retrieves the text, before the model is called,
  and the code decides whether the question is answered at all (section 2).
- **strict**: the model retrieves the text, by calling tools, and the code
  replaces an answer that no tool supports (section 3).
- **streaming**: the model retrieves the text, by calling tools, and the answer
  is shown as the model writes the answer. The code changes nothing between the
  user and the agent: no step edits the history, and no check runs on the answer
  (section 4).

The tools that the model calls are described once, in section 5.

The table [`workflow_nodes.md`](workflow_nodes.md) says, for each node of the
catalog of the chat workflows, which of the four workflows runs the node. The
table is generated from the code.

The diagrams of the pre-retrieval and strict workflows are generated from the
code, in [`workflow_diagrams.md`](workflow_diagrams.md). Sections 2 and 3 link
to those diagrams. In the diagram of section 1, a blue node whose label ends in
«🤖 model call» is a node where the code sends a request to the model. Every
other node runs only code.

## The node tables

Every node table of this document has the same columns:

- **ID**: the identifier of the node. The identifier is the same string as the
  first argument of the tracing call that marks the node.
- **What it does**.
- **Code**: the file and the symbol that run the node. A symbol is a function, a
  class or a constant defined in that file. The table never cites a line
  number, because a line number changes when the code above the line changes.
- **Trace**: the attributes of the record that the node writes in the trace,
  without the prefix `llmwiki.`. A node that writes no record says
  `none: <reason>`. A node whose record has no attribute says `no attributes`.
- **Status**: `code` for a node that the code runs; `design` for a node that
  exists only in a design.

## 1. The turn and the mode choice

Every turn, in every mode, starts with the same two nodes. The root record of the
trace of a turn is `turn`, with the attributes `conversation_id`, `turn` (the
position of the question in the conversation), `mode`, `question` and
`language`.

```mermaid
flowchart TD
    Q0["a question arrives in the chat"]
    C1["C1 · the history of the turn, counted<br/>pre-retrieval and strict: C2a to C2d build the history,<br/>nodes of the input workflow<br/>streaming: plain_history, every message as written"]
    C2{"C2 · the mode of the turn"}
    P["Q1 · pre-retrieval, section 2"]
    S["T1 · strict, section 3<br/>🤖 model call"]
    F["F1 · streaming, section 4<br/>🤖 model call"]
    Q0 --> C1 --> C2
    C2 -->|pre-retrieval| P
    C2 -->|strict| S
    C2 -->|streaming| F
    classDef llm fill:#dbeafe,stroke:#1d4ed8,stroke-width:2px,color:#1f2937
    class S,F llm
```

| ID | What it does | Code | Trace | Status |
|---|---|---|---|---|
| C1 | Counts the history of the turn: the prior messages, the refused ones that C2a removes, and the messages that the model receives. In the pre-retrieval and strict modes, C1 is the first node of the input workflow and counts the history as `model_history` builds the history; C2a to C2d build the history, one step per node. In the streaming mode, the model receives every prior message as written (`plain_history`), with no message removed and no line added, and `refused_dropped` is 0 | `base/domain/chat/workflow/catalog.py`, `history_counts`; `base/domain/chat/history.py`, `history_counts`, `model_history`; `base/services/chat.py`, `chat_turn_stream_with_context`, `plain_history` | `prior_messages`, `refused_dropped`, `history_messages` | code |
| C2 | Records the mode of the turn. In the pre-retrieval and strict modes, C2 is the second node of the input workflow; in the streaming mode, `chat_turn_stream_with_context` records C2 | `base/domain/chat/workflow/catalog.py`, `mode`; `base/services/chat.py`, `chat_turn_with_context`, `chat_turn_stream_with_context` | `mode` | code |
| C2a | Removes from the history every refused answer, with the question that the refused answer answered. A refused first turn leaves the second turn with an empty history. Runs in the input workflows of the pre-retrieval and strict modes | `base/domain/chat/guardrail.py`, `strip_refused_exchanges`; `base/domain/chat/workflow/catalog.py`, `strip_refused` | `refused_dropped` | code |
| C2b | Replaces the advisory table of each answer of the history with a placeholder line; the model can call the advisory tool again. Runs in the input workflows of the pre-retrieval and strict modes | `base/domain/chat/history.py`, `compact_tables`; `base/domain/chat/workflow/catalog.py`, `compact_tables` | `compacted` | code |
| C2c | Keeps the last `max_turns` turns of the history, 3 by default. Runs in the input workflows of the pre-retrieval and strict modes | `base/domain/chat/history.py`, `cap_turns`, `DEFAULT_MAX_TURNS`; `base/domain/chat/workflow/catalog.py`, `trim_turns` | `trimmed`, `max_turns` | code |
| C2d | Builds the history that the model receives; an answer that read data ends with its origin line, which names the dataset or the tool that the figures came from. Runs in the input workflows of the pre-retrieval and strict modes | `base/domain/chat/history.py`, `with_origin_lines`, `data_origin`; `base/domain/chat/workflow/catalog.py`, `origin_lines` | `origin_lines`, `history_messages` | code |
| C3 | The figure check: classifies every figure of the prose as exact, rounded or unsupported, against the numbers of the passages of the turn, the advisory table, the messages of the user and the passages of the earlier answers. The figures of the advisory table, of the note above it, of the citation lines and of the Tier-2 warning are not checked. When the prose has rounded figures, a fixed note is appended. The text of the model is not edited. C3 runs in the pre-retrieval mode, before Q21, and in the strict mode, before T5. C3 does not run in the streaming mode. A refusal does not reach C3 | `base/domain/chat/figure_check.py`, `check_answer`, `appended_spans`; `base/domain/verify/figures.py`, `check_figures`; `base/domain/chat/workflow/catalog.py`, `figures`; `base/services/chat.py`, `check_turn_figures` | `figures_checked`, `figures_unsupported`, `figures_rounded`, `note_added` | code |
| C4 | Precedes the question with a note that names the page the user is reading, so that a question that names no topic is read as a question about that page. In the pre-retrieval mode, the note names the page only when Q5a found the page; in the strict mode, the note names the open page without checking that the page exists. Runs in the input workflows of the pre-retrieval and strict modes; the streaming mode adds the same note with no node | `base/domain/chat/preretrieval.py`, `with_open_page`; `base/domain/chat/workflow/catalog.py`, `open_page_note` | `answer`, `page` | code |

## 2. Pre-retrieval

The diagrams of the two workflows of this mode are generated from the graphs,
in [`workflow_diagrams.md`](workflow_diagrams.md): the input workflow in
[Pre-retrieval, input](workflow_diagrams.md#pre-retrieval-input) and the output
workflow in [Pre-retrieval, output](workflow_diagrams.md#pre-retrieval-output).
A node inserted after another node carries the identifier of that node and a
letter, for example Q5a to Q5g after Q5, so that the other identifiers stay the
same. C1, C2, C2a to C2d, C4 and C3 are nodes of section 1. Each refusal node is
named by the cause of the refusal (R1 to R5, `RefusalCause` in
`base/domain/chat/refusal.py`).

The generated diagrams show the steps, the decisions and the branches. The
generated diagrams do not show the values of a turn, the calls to the model, the
index or the suggestions node S. The paragraphs below describe the values, the
calls, the index and S.

The values below come from one real question, «¿Cuánto rinden los plazos
fijos?», asked in the example wiki `examples/finanzas-argentinas`. Every value
of Q2, Q4 and Q6 was computed with the code: `_normalize` for the normalised question, the Spanish Snowball stemmer
for the stems, `_fts_query` and `stem_fts_query` for the query.

- **Q2**: the normalised question is «¿cuanto rinden los plazos fijos?».
- **Q3**: the question names no blacklist term. The blacklist is the
  `[fuera_de_alcance]` list of `wiki_config.toml`: `cedear`, `cedears`,
  `cripto` and `bitcoin` in the example wiki. `is_off_limits` compares whole
  words of the normalised question, not stemmed.
- **Q4**: the stems of the question are «cuant rind los plaz fij».
- **Q5**: `mentions_known_data` compares the stemmed question with the stemmed
  dataset categories, concept-page titles and aliases, and the normalised
  question with the dataset keys, by exact match. The question is in the
  coverage roster: the category `plazo_fijo` stems to «plaz fij».
- **Q6**: the wiki search removes the stop words and stems the other words. The
  FTS query is `"rind" OR "plaz" OR "fij"`. The search finds pages such as
  «Plazo fijo tradicional» and «Plazo fijo UVA».
- **Q7a and Q7b**: the question names the category `plazo_fijo` and no key of
  the category. Q7d reads the 8 key names of the `entidad` column, from «Banco
  Nación» to «Brubank».

The index is the table `chunks_fts`, an inverted index from each stem to the
rows that contain the stem (see [`ingestion_nodes.md`](ingestion_nodes.md)).
The wiki search Q6 reads the wiki rows of the index. The source search Q8 reads
the source rows of the same index, and returns at most 4 fragments.

Two kinds of node send a request to the model:

- **Q5c** rewrites a follow-up into a question that stands alone, for example
  «¿Y qué riesgos tiene?» into «¿Qué riesgos tiene la caución bursátil?».
- **Q13 to Q16** are the agent call. Each node injects a different text: the
  wiki pages (Tier 1, with the dataset rows of Q7c or the key names of Q7d),
  the overview and the index, nothing, or the source fragments (Tier 2). With
  nothing injected (Q15), the model calls the data tools of section 5.

Three nodes of the output workflow use a fixed text or a threshold:

- **Q17** stems the content words of the answer and counts the fraction that
  the fragments contain. The answer is grounded when the fraction is at least
  `min_coverage`, 0.2 by default.
- **Q20** looks for the fixed refusal sentence at the start of the answer:
  «No encontré la respuesta en el texto que recibí de la wiki.» in Spanish.
- **Q18** appends the Tier-2 warning: «⚠️ Esta respuesta proviene de un
  documento fuente sin página curada del wiki; verificá.» in Spanish.

The suggestions node S runs for every refusal but R1. R1 names the matched
term and lists no suggestion. S takes the concept pages of the coverage roster
for a question in the roster, and the pages of a wiki search otherwise. S then
removes the repeated pages and orders the rest in three groups: the concept
pages whose title shares a stem with the question, the other concept pages, and
the summary pages. S keeps the first five. A refusal with no suggestion points
to the index of the wiki, and R2a always points to the index.

Example questions of two refusals: «¿Qué son los CEDEARs y conviene
comprarlos?» ends in R1, and «¿Va a llover mañana en Rosario?» ends in R2a. R3
names the source document. R5 keeps the text of the model and adds the cause
line and the suggestions.

Two workflows of pydantic-graph run the nodes of this section, one before the
model and one after the model. Each node is a function of the catalog, `CATALOG`
in `base/domain/chat/workflow/catalog.py`. Each function is written once, and
each workflow registers the functions that the workflow uses (`register`). A
node that two workflows use is the same function in both workflows.

- **The input workflow**, `graph` in `base/domain/chat/workflow/input_checks.py`,
  runs C1, C2 and C2a to C2d (section 1), the checks of the question from Q1 to
  Q12, and C4, Q12a and Q12b, which build the prompt. The input workflow ends in
  a refusal (R1, R2a, R2b) or in the node that calls the agent (Q13 to Q16). The
  input workflow reads and writes the `PreRetrievalTurn` of the turn
  (`base/domain/chat/preretrieval.py`).
- **The agent call**: `_pre_retrieval_turn` (`base/domain/chat/preretrieval.py`)
  calls the agent between the two workflows, outside both workflows.
- **The output workflow**, `graph` in
  `base/domain/chat/workflow/output_checks.py`, runs the checks of the answer
  Q17, Q19 and Q20, the additions Q20a to Q20d and Q18, the figure check C3
  (section 1) and Q21. The output workflow ends in the answer or in a refusal
  (R3, R4, R5). Its state, `AnswerChecks`
  (`base/domain/chat/workflow/catalog.py`), holds the answer of the agent and the
  `PreRetrievalTurn` of the input workflow.

Each workflow declares the list of the catalog nodes that the workflow runs
(`PRE_RETRIEVAL_NODES`, `STRICT_NODES`). The table
[`workflow_nodes.md`](workflow_nodes.md) is generated from those lists.

| ID | What it does | Code | Trace | Status |
|---|---|---|---|---|
| Q1 | The question enters the pre-retrieval flow | `base/domain/chat/preretrieval.py`, `pre_retrieval_answer`, `PreRetrievalTurn`; `base/domain/chat/workflow/catalog.py`, `question` | `question` | code |
| Q2 | Lowercases the question and removes the accents of the question; punctuation stays | `base/domain/chat/scope.py`, `_normalize`; `base/domain/chat/workflow/catalog.py`, `normalize` | `normalized` | code |
| Q3 | Checks the normalised question against the blacklist, by whole word | `base/domain/chat/scope.py`, `is_off_limits`; `base/domain/chat/preretrieval.py`, `blacklist`; `base/domain/chat/workflow/catalog.py`, `blacklist` | `answer` | code |
| Q4 | Stems the question | `base/domain/text/stemming.py`, `stem_text`; `base/domain/chat/workflow/catalog.py`, `stem` | `stemmed` | code |
| Q4a | Checks whether the question names data: a dataset category, stemmed; a dataset key, exact; a data alias; or a request for investment advice. Builds the coverage roster first, once per turn | `base/domain/chat/scope.py`, `mentions_known_data`, `advisory_intent`; `base/domain/chat/preretrieval.py`, `ensure_roster`, `detect_data`; `base/domain/chat/workflow/catalog.py`, `data_detection` | `answer` | code |
| Q5 | Checks the question against the coverage roster: stemmed against the dataset categories, the concept-page titles and the aliases; exact against the dataset keys | `base/domain/chat/scope.py`, `mentions_known_data`; `base/domain/chat/preretrieval.py`, `build_vocabulary`, `ensure_roster`, `match_terms`; `base/domain/chat/workflow/catalog.py`, `roster` | `answer`, `roster_size`, `aliases`, `matched` | code |
| Q5a | Checks whether the user is reading a wiki page; the page that the user is reading is injected first, and makes the question covered | `base/domain/chat/preretrieval.py`, `_open_page_block`; `base/domain/chat/workflow/catalog.py`, `open_page` | `answer`, `page` | code |
| Q5c | Rewrites a follow-up that names no topic into a question that stands alone, from the conversation. Runs only with a condenser and a history, for a question that is not in the coverage roster, names no data and has no open page | `base/domain/chat/condense.py`, `prompt`; `base/services/chat.py`, `_condenser`; `base/domain/chat/workflow/catalog.py`, `rewrite` | `rewrite` | code |
| Q5d | Accepts the rewrite when the rewrite is one line, not empty and not too long; otherwise the search keeps the question. An accepted rewrite that differs from the question is what the gate and the search examine; the model answers the original question | `base/domain/chat/condense.py`, `accepted`; `base/domain/chat/workflow/catalog.py`, `accept_rewrite` | `answer`, `rewritten` | code |
| Q5e | Checks the rewrite against the blacklist, by whole word, as Q3 checks the question | `base/domain/chat/preretrieval.py`, `blacklist`; `base/domain/chat/workflow/catalog.py`, `blacklist_rewrite` | `answer` | code |
| Q5f | Checks whether the rewrite names data, as Q4a checks the question | `base/domain/chat/preretrieval.py`, `detect_data`; `base/domain/chat/workflow/catalog.py`, `data_detection_rewrite` | `answer` | code |
| Q5g | Checks the rewrite against the coverage roster, as Q5 checks the question | `base/domain/chat/preretrieval.py`, `match_terms`; `base/domain/chat/workflow/catalog.py`, `roster_rewrite` | `answer`, `matched` | code |
| Q5b | Records the result of the condensation (Q5c to Q5g): the text that the gate and the search examine, the rewrite or the question, and what the roster check found on that text. A rewrite that names a blacklist term does not reach Q5b: Q5e refuses it with R1. The search uses the rewrite, the model answers the original question | `base/domain/chat/workflow/catalog.py`, `condense` | `rewritten`, `answer`, `off_limits`, `matched` | code |
| Q6 | Searches the wiki pages: removes the stop words, stems the terms, and runs the FTS query. A topic of Q5 with no page among the hits gets the chunk of its own page in place of the last hit. An alias is searched by the name it stands for (`alias_titles`) | `base/domain/chat/preretrieval.py`, `retrieve_wiki`, `_fts_query`; `base/domain/chat/workflow/catalog.py`, `wiki_search` | `fts_query`, `pages` | code |
| Q7 | Asks whether the wiki search returned a page | `base/domain/chat/preretrieval.py`, `plan_retrieval`, `tier1_plan`; `base/domain/chat/workflow/catalog.py`, `wiki_pages` | `answer`, `wiki_fragments` | code |
| Q7a | Asks whether the question names a dataset category, by stemmed match | `base/domain/chat/preretrieval.py`, `named_categories`, `dataset_injection`; `base/domain/chat/workflow/catalog.py`, `dataset_categories` | `answer`, `categories` | code |
| Q7b | Asks whether the question names a key of that category, by exact match | `base/domain/chat/preretrieval.py`, `named_categories`, `dataset_injection`; `base/domain/chat/workflow/catalog.py`, `dataset_keys` | `category`, `answer`, `keys` | code |
| Q7c | Reads the rows of the key and renders the rows as `query_dataset` renders the rows: `valor`, `unidad`, `as_of`, `fuente` | `base/domain/datasets/source.py`, `LocalMarkdownSource`; `base/domain/chat/dataset_tools.py`, `format_rows_as_table`; `base/domain/chat/preretrieval.py`, `rows_block`, `dataset_injection`; `base/domain/chat/workflow/catalog.py`, `dataset_rows` | `category`, `keys`, `rows` | code |
| Q7d | Reads only the key names of the category, without values | `base/domain/datasets/source.py`, `LocalMarkdownSource`; `base/domain/chat/preretrieval.py`, `key_names_block`, `dataset_injection`; `base/domain/chat/workflow/catalog.py`, `dataset_key_names` | `category`, `key_names` | code |
| Q8 | Searches the source documents, at most four fragments | `base/domain/chat/preretrieval.py`, `retrieve_source_chunks`; `base/domain/chat/workflow/catalog.py`, `source_search` | `fts_query`, `fragments` | code |
| Q8a | Asks whether the question is about the whole wiki and, for such a question, reads the overview and the index of the wiki from disk; no page is read when the wiki has neither page. The expressions are the `[intent] collection` list of `wiki_config.toml`, or the base list of the wiki's language | `base/domain/chat/scope.py`, `collection_intent`; `base/domain/chat/intent.py`; `base/domain/chat/preretrieval.py`, `retrieve_collection_pages`, `intent_lists`; `base/domain/chat/workflow/catalog.py`, `collection_pages` | `answer`, `pages` | code |
| Q8b | Adds the pages that Q8a read to the wiki pages, when the user reads a wiki page and the question names no topic of the wiki. Such a question is in the coverage roster only because of the open page, and Q7 answers it from the wiki pages before Q9 runs: with the overview open, «what is in this wiki?» still gets the index | `base/domain/chat/workflow/catalog.py`, `collection_with_open_page` | `answer`, `pages` | code |
| Q9 | Asks whether Q8a read an overview or an index for a question about the whole wiki; with those pages, the plan injects them | `base/domain/chat/preretrieval.py`, `plan_retrieval`, `tier1_plan`; `base/domain/chat/workflow/catalog.py`, `collection` | `answer`, `collection_pages` | code |
| Q10 | Asks whether the question is a data question: the question names a dataset category or key, or a data alias, or asks for investment advice (the `[intent] advisory` list, or the base list of the active domains, with an amount or a horizon) | `base/domain/chat/scope.py`, `advisory_intent`; `base/domain/chat/intent.py`; `base/domain/chat/preretrieval.py`, `plan_retrieval`, `data_plan`; `base/domain/chat/workflow/catalog.py`, `data_question` | `answer` | code |
| Q11 | Asks whether the source search returned a fragment, for a question in the coverage roster | `base/domain/chat/preretrieval.py`, `plan_retrieval`, `tier2_plan`; `base/domain/chat/workflow/catalog.py`, `source_fragments` | `answer`, `source_fragments`, `in_roster` | code |
| Q12 | Chooses between R2a and R2b by the coverage roster | `base/domain/chat/preretrieval.py`, `plan_retrieval`; `base/domain/chat/workflow/catalog.py`, `roster_refusal` | `answer` | code |
| Q12a | Builds the line that names each alias of the question with the name the alias stands for. Without the line, «¿qué es el billete verde?» gets the pages of the dollar and no text that says «billete verde» is the dollar. The line is empty when the question names no alias | `base/domain/chat/preretrieval.py`, `synonyms_line`; `base/domain/chat/workflow/catalog.py`, `synonyms` | `answer`, `line` | code |
| Q12b | Builds the prompt that the model receives: the synonyms line and the injected text of the plan in the inject template, followed by the question with the note of C4; with no injected text, the synonyms line and the question | `base/domain/chat/preretrieval.py`, `build_prompt`; `base/domain/chat/workflow/catalog.py`, `prompt`; the template: `prompts/inject_template.en.md` | `injected`, `prompt_chars` | code |
| Q13 | Injects the wiki pages, with the dataset rows of Q7c or the key names of Q7d; the model answers from the injected text | `base/domain/chat/preretrieval.py`, `_answer_node`, `_pre_retrieval_turn`; the template: `prompts/inject_template.en.md` | `tier`, `context_chars`, `prompt` (a payload file), `raw_output` | code |
| Q14 | Injects `overview.md` and `index.md`; the model answers from the injected text | `base/domain/chat/preretrieval.py`, `_answer_node`, `_pre_retrieval_turn` | `tier`, `context_chars`, `prompt` (a payload file), `raw_output` | code |
| Q15 | Injects nothing; the model calls the data tools of section 5 | `base/domain/chat/preretrieval.py`, `_answer_node`, `_pre_retrieval_turn` | `tier`, `context_chars`, `prompt` (a payload file), `raw_output` | code |
| Q16 | Injects the source fragments; the model answers from the injected text | `base/domain/chat/preretrieval.py`, `_answer_node`, `_pre_retrieval_turn` | `tier`, `context_chars`, `prompt` (a payload file), `raw_output` | code |
| Q17 | Checks that the answer is grounded in the fragments, with the stop words of the wiki's language, stemming and the configured threshold. Skipped when the sentence verifier checks the answer (the checkbox «Verificar las oraciones» of the web interface, with the verifier installed): the verifier is then the only comparison of the answer against the fragments | `base/domain/chat/overlap.py`, `coverage`; `base/domain/chat/workflow/catalog.py`, `fragment_coverage` | `answer`, `coverage`, `min_coverage` | code |
| Q18 | Appends the fixed Tier-2 warning to the answer | `base/domain/i18n.py`, `get_locale`; `base/domain/chat/workflow/catalog.py`, `tier2_warning` | no attributes | code |
| Q19 | Asks whether a data tool returned evidence | `base/domain/chat/guardrail.py`, `has_grounding`; `base/domain/chat/workflow/catalog.py`, `tool_evidence` | `answer` | code |
| Q20 | Asks whether the answer opens with the fixed refusal sentence of the model | `base/domain/chat/refusal.py`, `fixed_sentence`; `base/domain/chat/workflow/catalog.py`, `model_refusal` | `answer` | code |
| Q20a | Appends the advisory table when the advisory tool returned one | `base/domain/chat/postprocess.py`, `answer_with_table`; `base/domain/chat/workflow/catalog.py`, `table` | `table_added` | code |
| Q20b | Offers the keys with data when the model asked for a key without data | `base/domain/chat/preretrieval.py`, `offer_available_keys`; `base/domain/chat/workflow/catalog.py`, `keys` | `keys_offered` | code |
| Q20c | `ensure_citation` adds the citation of the tools the model used; for injected dataset rows the code adds the dataset citation | `base/domain/chat/postprocess.py`, `ensure_citation`; `base/domain/chat/workflow/catalog.py`, `citation` | `citation_added` | code |
| Q20d | When the answer names none of the injected wiki pages, `mark_injected` adds a `Referencia:` line that lists the pages the model received and says that which of them the answer used cannot be determined. When the model wrote its own reference line with injected pages, `mark_injected` labels that line `Referencia según el modelo` and adds below it a line that starts with `Además` and lists every page the model received | `base/domain/chat/postprocess.py`, `mark_injected`, `injected_pages`; `base/domain/chat/workflow/catalog.py`, `injected_reference` | `injected_marked` | code |
| Q21 | Shows the answer, with the additions of Q20a to Q20d; `citation_added` says whether Q20c or Q20d added a line. When the sentence verifier runs, the web interface adds the line `Referencia según el verificador de oraciones`, with the injected pages split into apparently used (they support at least one sentence) and apparently not used. That line replaces the line of `mark_injected`; when the answer has only a reference line of the model, the line goes below it, and the model's line is labelled `Referencia según el modelo` | `base/domain/chat/postprocess.py`, `resolve_injected`; `web/routes/chat.py`, `chat_stream`; `base/domain/chat/workflow/catalog.py`, `shown` | `final_answer`, `citation_added`, `keys_offered` | code |
| R1 | Refusal of cause 1: the question (Q3) or the rewrite (Q5e) names a blacklist term; the refusal names the matched term | `base/domain/chat/refusal.py`, `render`; `base/domain/chat/preretrieval.py`, `refuse`; `base/domain/chat/workflow/catalog.py`, `refuse_blacklist` | `cause`, `final_answer`, `suggestions` | code |
| R2a | Refusal of cause 2a: the question names no topic of the wiki | `base/domain/chat/refusal.py`, `render`; `base/domain/chat/preretrieval.py`, `refuse`, `suggestions`; `base/domain/chat/workflow/catalog.py`, `refuse_not_in_roster` | `cause`, `final_answer`, `suggestions` | code |
| R2b | Refusal of cause 2b: the question names a topic of the wiki, and the searches found no text | `base/domain/chat/refusal.py`, `render`; `base/domain/chat/preretrieval.py`, `refuse`, `suggestions`; `base/domain/chat/workflow/catalog.py`, `refuse_nothing_found` | `cause`, `final_answer`, `suggestions` | code |
| R3 | Refusal of cause 3: the Tier-2 answer is not grounded in the fragments; the refusal names the source document | `base/domain/chat/refusal.py`, `render`; `base/domain/chat/preretrieval.py`, `refuse`, `suggestions`; `base/domain/chat/workflow/catalog.py`, `refuse_unsupported` | `cause`, `final_answer`, `suggestions` | code |
| R4 | Refusal of cause 4: no tool returned evidence behind the answer | `base/domain/chat/refusal.py`, `render`; `base/domain/chat/preretrieval.py`, `refuse`, `suggestions`; `base/domain/chat/workflow/catalog.py`, `refuse_ungrounded` | `cause`, `final_answer`, `suggestions` | code |
| R5 | Refusal of cause 5: the model refused with the fixed sentence; the refusal keeps the text of the model, which states the cause, and adds the suggestions | `base/domain/chat/refusal.py`, `render`; `base/domain/chat/preretrieval.py`, `refuse`, `suggestions`; `base/domain/chat/workflow/catalog.py`, `refuse_model` | `cause`, `final_answer`, `suggestions` | code |
| S | Builds the suggestions of a refusal from the coverage roster or from the wiki search | `base/domain/chat/refusal.py`, `roster_suggestions`, `search_suggestions` | none: the result of S is the `suggestions` attribute of the R record | code |

## 3. Strict

In the strict mode the model receives the question and the history, and decides
which tools to call (section 5). After the model answers, the code checks that a
tool returned evidence.

The strict mode has two workflows too, with the call to the model, T1, between
them. `chat_turn_with_context` (`base/services/chat.py`) runs both workflows.

- **The input workflow**, `strict_graph` in
  `base/domain/chat/workflow/input_checks.py`, runs C1, C2, C2a to C2d and C4
  (section 1). The input workflow builds the history and the question that the
  model receives, and checks nothing in the question. Its state is a
  `StrictTurn`.
- **The output workflow**, `strict_graph` in
  `base/domain/chat/workflow/output_checks.py`, runs T2, T3, the additions T4a
  to T4c, the figure check C3 and T5. Its state is the `AnswerChecks` of the
  answer, with no `PreRetrievalTurn`.

C1, C2, C2a to C2d, C4 and C3 are the same functions of the catalog as in the
pre-retrieval mode.

The diagrams of the two workflows of this mode are generated from the graphs:
the input workflow in [Strict, input](workflow_diagrams.md#strict-input) and
the output workflow in [Strict, output](workflow_diagrams.md#strict-output).
The generated diagrams do not show T1, the call to the model, which runs
between the two workflows. In T1 the model runs with the wiki tools and the data
tools of section 5. Two nodes of the output workflow write a fixed text:

- **T3** replaces the answer by the guardrail refusal: «No pude respaldar esta
  respuesta con una consulta a la wiki en este turno.» in Spanish.
- **T4c** adds the line «Referencia: ninguna (…)» to an answer that cites
  nothing (`mark_uncited`). A refusal, of T3 or of the model, is left as it is.

| ID | What it does | Code | Trace | Status |
|---|---|---|---|---|
| T1 | The model runs with the question, the history and the tools, and writes the answer | `base/services/chat.py`, `chat_turn_with_context`; `base/domain/chat/agent.py`, `create_agent` | `tools_called`, `raw_output` | code |
| T2 | Asks whether a tool return of the run carries evidence: a return that is not empty and is not one of the "nothing found" sentences of the tools | `base/domain/chat/guardrail.py`, `has_grounding`, `_is_substantive`; `base/domain/chat/workflow/catalog.py`, `evidence` | `answer` | code |
| T3 | Replaces the answer by the guardrail refusal of the wiki's language | `base/domain/chat/guardrail.py`, `enforce_grounding`, `refusal_for`; `base/domain/chat/workflow/catalog.py`, `guardrail` | `final_answer` | code |
| T4a | Appends the advisory table when the advisory tool returned one; no change on a refusal | `base/domain/chat/postprocess.py`, `answer_with_table`; `base/domain/chat/workflow/catalog.py`, `strict_table` | `table_added` | code |
| T4b | Adds the citation of the tools that the model used. The index and the overview of the wiki are not cited: the model reads them to find the page that answers | `base/domain/chat/postprocess.py`, `ensure_citation`; `base/domain/chat/workflow/catalog.py`, `strict_citation` | `citation_added` | code |
| T4c | An answer that still cites nothing (no page read, no data queried, no source named) gets the line `Referencia: ninguna (…)`. A refusal, of T3 or of the model, is left as it is | `base/domain/chat/postprocess.py`, `mark_uncited`; `base/domain/chat/refusal.py`, `is_refusal`; `base/domain/chat/workflow/catalog.py`, `uncited_mark` | `uncited_marked` | code |
| T5 | Shows the answer | `base/services/chat.py`, `chat_turn_with_context`; `base/domain/chat/workflow/catalog.py`, `strict_answer` | `final_answer`, `refusal_substituted` | code |

## 4. Streaming

In the streaming mode the model receives the question and the history, decides
which tools to call (section 5), and the answer is shown as the model writes the
answer. The code changes nothing between the user and the agent:

- **The history**: the model receives every prior message as written
  (`plain_history`, `base/services/chat.py`). Refused exchanges stay, the history
  is not trimmed, and no origin line is added. A long conversation can exceed the
  token limit of the model.
- **The question**: the model receives the question as written. When the user
  reads a wiki page, the question is preceded by the note that names the page
  (`with_open_page`), as in the strict mode.
- **The answer**: the user sees the text of the model. No check runs on the
  answer: the figure check C3 does not run, and no line is appended.

```mermaid
flowchart TD
    F1["F1 · the model runs with the wiki tools and the data tools<br/>the tools of section 5<br/>the text is shown as the text arrives"]
    F2["F2 · the answer is complete<br/>no check"]
    F1 --> F2
```

| ID | What it does | Code | Trace | Status |
|---|---|---|---|---|
| F1 | The model runs with the question, the history and the tools; each chunk of text is shown as the chunk arrives | `base/services/chat.py`, `chat_turn_stream_with_context`; `base/domain/chat/agent.py`, `create_agent` | `tools_called` | code |
| F2 | The answer is complete. The answer is shown as the model wrote the answer, with no check and no appended line | `base/services/chat.py`, `chat_turn_stream_with_context` | `final_answer` | code |

## 5. Tools

The tools that the model calls. The strict mode (T1) and the streaming mode (F1)
register the wiki tools (K1 to K3) and the data tools; the pre-retrieval mode
(Q15) registers only the data tools, because in the pre-retrieval mode the code
searches the wiki.

The data tools depend on the wiki:

- A wiki with the finance overlay (`base/domain/extensions/finance_argentina/agent_tool.py`,
  `activate`) registers K5 and the finance data tools K6 to K10, one per dataset
  category that passes the finance validator. A wiki with the finance overlay
  does not register K4.
- Any other wiki with a `datasets/` directory registers K4.

The arguments of a finance data tool accept only the values of the dataset of
the tool: the keys of the category and, for `plazo_dias`, the terms of the
dataset. The allowed values are read from the dataset files when the wiki
opens. A call with a value outside the allowed values raises `ModelRetry`, and
the model receives the allowed values and corrects the call. Each tool call writes one record, with the arguments of the
call and whether the return of the call is substantive (`_is_substantive`,
`base/domain/chat/guardrail.py`): not empty, and not one of the "nothing found"
sentences of the tools.

| ID | What it does | Code | Trace | Status |
|---|---|---|---|---|
| K1 | Reads one wiki page by its path | `base/domain/chat/wiki_tools.py`, `read_wiki_page` | `path`, `substantive` | code |
| K2 | Searches the wiki pages with an FTS query: every word first; when no page has every word, any word (`any_word_query`) | `base/domain/chat/wiki_tools.py`, `search_wiki_fts`; `base/domain/tools/search.py`, `any_word_query` | `query`, `limit`, `widened`, `substantive` | code |
| K3 | Searches the fragments of the source documents with an FTS query: every word first; when no fragment has every word, any word (`any_word_query`). Each result carries the whole fragment | `base/domain/chat/tools.py`, `search_source_chunks`; `base/domain/tools/search.py`, `any_word_query` | `query`, `limit`, `widened`, `substantive` | code |
| K4 | Reads the rows of a dataset category, optionally of one key and one metric; not registered in a wiki with the finance overlay | `base/domain/chat/dataset_tools.py`, `query_dataset` | `categoria`, `clave`, `metrica`, `substantive` | code |
| K5 | Estimates and compares the investment alternatives for an amount and a horizon (finance overlay) | `base/domain/extensions/finance_argentina/agent_tool.py`, `estimar_alternativas` | `monto`, `horizonte_meses`, `moneda`, `substantive` | code |
| K6 | Reads the TNA of a plazo fijo, by entity and by term in days | `base/domain/extensions/finance_argentina/data_tools.py`, `tasa_plazo_fijo` | `entidad`, `plazo_dias`, `substantive`, `rejected` | code |
| K7 | Reads the TNA of a caución, by market and by term in days | `base/domain/extensions/finance_argentina/data_tools.py`, `tasa_caucion` | `mercado`, `plazo_dias`, `substantive`, `rejected` | code |
| K8 | Reads the buy and sell quotes of a type of dollar | `base/domain/extensions/finance_argentina/data_tools.py`, `cotizacion_dolar` | `tipo`, `substantive`, `rejected` | code |
| K9 | Reads the price of a share, by ticker | `base/domain/extensions/finance_argentina/data_tools.py`, `precio_accion` | `ticker`, `substantive`, `rejected` | code |
| K10 | Reads the return of a money market fund, by fund | `base/domain/extensions/finance_argentina/data_tools.py`, `rendimiento_fci` | `fondo`, `substantive`, `rejected` | code |
