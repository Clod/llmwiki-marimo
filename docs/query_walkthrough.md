# Query Walkthrough

[`ingestion_walkthrough.md`](ingestion_walkthrough.md) follows a document from the
folder `sources/` to the wiki pages and the search index. This document starts
where that one ends: the wiki exists, and someone asks it a question.

**Audience.** A programmer who reads Python and SQL and has never seen this
project. A term from the field of language models is defined where it first
appears.

**Reference.** Each step of a chat turn is a node of the reading diagram,
[`query_nodes.md`](query_nodes.md). A node has an identifier, for example
`Q_IN_ROSTER`, and the node table names the file and the function that run the
step. This document names the nodes and does not repeat the tables.

**The answers are real.** Every route and every answer quoted below comes from a
run with a real model, on the example wikis of `examples/`. The run is in the
[appendix](query_walkthrough_appendix.md), which a script writes:

```bash
uv run python scripts/capture_query_walkthrough.py
```

The appendix of this version was captured on 2026-11-23 with the model
`anthropic/claude-haiku-5.5`. The words of an answer change from one run to the
next. The route up to the call to the model depends only on the code and on the
wiki. The same question takes the same route on every run.

## The question this document answers

A user asks the wiki something that no page answers. Asked a question outside the
text it received, a language model tends to answer anyway. The answer comes from
the training of the model, in a fluent and confident sentence. The user cannot
tell that answer from an answer that came from the wiki.

So the statement «the model says when it does not know» cannot be the
mechanism. Code has to check. The design question is when the code checks:

- **Never.** The model searches the wiki with tools, and its answer reaches the
  user as written.
- **After the model.** The model searches the wiki with tools, and the code
  examines the finished answer before the user reads it.
- **Before the model.** The code searches the wiki, decides whether the wiki
  covers the question, and calls the model only when it does.

A **tool** is a function the model is allowed to call. The model asks for a
search, the code runs the search, and the result returns to the model. An
arrangement where the model holds the tools and decides which to call, and when to
stop, is called **agentic**.

Each of the three positions is a value of the **Mode** selector of the chat. None
of the three is a broken version of another. Each one answers a different range
of questions and gives a different guarantee.

## The Mode selector

The **Mode** selector sits under the question field of the **Read** tab. It has
three values, and the chat opens on the first one:

| Mode | Position | Who searches the wiki | What the code guarantees |
|---|---|---|---|
| **Pre-retrieval** | before the model | the code | a question that names nothing the wiki covers is refused without a call to the model |
| **Agent with evidence check** | after the model | the model, with tools | an answer that no tool result backs carries a warning |
| **No verification** | never | the model, with tools | nothing: the answer appears as the model writes it |

Two more controls sit under the selector:

- **Verify the sentences.** After the answer, a local model checks each sentence
  against the pages the model read, and marks the sentences no page supports. The
  answer does not change. The box needs the verifier installed from the
  **Maintain** tab, and works in the first two modes.
- **Check that the pages answer the question.** In the pre-retrieval mode, a
  second check asks whether the pages the code found answer the question. The
  options are **No check**, **Evidence quote** and **Judge**. [Part 2](#the-check-of-the-pages)
  shows both checks.

Part 1 of this document runs on `examples/fairy-tales`, a wiki of three English
tales and no data. Part 2 runs mainly on `examples/finanzas-argentinas`, a Spanish
wiki of Argentine investments with a folder of data files, `datasets/`.

## Part 1 — The model searches

In the agent mode and in the mode with no verification, the model receives the
question and four kinds of tool (section 5 of [`query_nodes.md`](query_nodes.md)).
Two of the tools search the **full-text index**, the SQLite table `chunks_fts` that
the ingestion fills with the fragments of the pages and of the source documents:

| Tool | Node | What it does |
|---|---|---|
| `read_wiki_page` | `K_READ_PAGE` | returns the text of one wiki page |
| `search_wiki_fts` | `K_SEARCH_WIKI` | searches the wiki pages in the full-text index |
| `search_source_chunks` | `K_SEARCH_SOURCES` | searches the fragments of the source documents |
| the data tools | `K_READ_DATASET`, `K_DOLLAR`, … | read the rows of the data files, in a wiki with `datasets/` |

The model also receives the **system prompt**: standing instructions that precede
every conversation, which the user does not see. The fairy-tale wiki writes its
system prompt in `[assistant] system_prompt` of its `wiki_config.toml`. The prompt
asks the model to read `wiki/index.md` first and to search the wiki pages second.
The model searches the source fragments only when the pages fall short. It
also asks for a citation on every fact. A prompt is a request: nothing in the code
makes the model follow it.

### What the model does with nothing checking it

The three questions below were asked in the mode with no verification. The
appendix records every tool call of each turn.

**«What tales are in this wiki?»** The model made one call, `K_READ_PAGE`, on the
index, and cited it:

> The wiki contains three tales (wiki/index.md):

**«What characters and themes do the tales share?»** No single page answers this
question. The model made five calls, `K_READ_PAGE` four times and `K_SEARCH_WIKI`
once, and built the comparison from several pages.

**«Compare how each story ends.»** The model made fourteen calls. Thirteen read
or searched the wiki pages, and the last one searched the source fragments. The
answer gives three endings, one row per tale, and closes with a statement of what
it did not check:

> Two points rest on the summary pages alone. I did not check the source PDFs for Cinderella's wedding or the Queen's death at Snow White's wedding.

In this mode the user also reads the text the model writes between tool calls,
because the answer appears as the model writes it:

> The summary pages give the endings briefly. I'll check the concept pages for more detail on each ending.

The three answers of this run are correct and cited. That result belongs to this
run and this model. In this mode nothing in the code makes the next run cite its
sources, or stop the model from answering from its training.

### What the evidence check adds

The same three questions were asked in the agent mode. The output workflow of this
mode runs after the model (section 3 of [`query_nodes.md`](query_nodes.md)):

1. **`T_TOOL_EVIDENCE`** asks whether a tool returned evidence: a result that is
   not empty and is not one of the «nothing found» sentences of the tools.
2. When no tool returned evidence, **`T_RETRY`** calls the model once more, with
   the instruction to consult the wiki.
3. When the second answer has no evidence either, **`T_NO_EVIDENCE_WARNING`**
   appends a warning, and the answer is never replaced. In a Spanish wiki the
   warning reads «Ninguna consulta a la wiki respalda esta respuesta: puede venir
   del conocimiento general del modelo.»
4. **`T_CITE_TOOLS`** appends the pages and the data files the model read, when the
   answer names none of them. **`T_MARK_UNCITED`** appends «Referencia: ninguna
   (…)» to an answer that still cites nothing.
5. **`C_FIGURES`**, the figure check, marks in red each number of the answer that
   no passage, table or question supports.

In the three turns on the fairy tales, `T_TOOL_EVIDENCE` found evidence, and the
model cited the pages itself. The answers reached the user as the model wrote
them.

The fourth question of the agent mode shows what the evidence check measures.
«¿Cuál es la capital de Francia?» (*what is the capital of France?*) was asked in
the finance wiki. The model read the index, searched the wiki and searched the
source fragments, and declined:

> No encontré la respuesta en el texto que recibí de la wiki. Además, la pregunta es de geografía y está fuera del alcance de esta wiki de finanzas personales e inversiones en Argentina, así que no la voy a responder.

`T_TOOL_EVIDENCE` answered `True` for this turn: the index the model read is a
non-empty result. The check asks whether a tool returned text. The check does not
ask whether that text answers the question. Here the model declined, so the
answer is correct. A model that had answered «París» after the same three tool
calls would have passed the same check, with no warning.

### What the checks after the model cannot see

The evidence check and the citation lines examine the **procedure** of the turn:
which tools ran, and which pages the answer names. Neither one compares a sentence
of the answer with the text the tools returned. An answer that cites a real page
for a statement the page does not make passes both.

Two other mechanisms compare the answer with the text:

- **Verify the sentences** compares each sentence with the passages the model
  read. The comparison uses a local model, and its threshold is not calibrated
  (`[verify] threshold`, 0.5 by default).
- **`C_FIGURES`** compares each number written with digits with the numbers of the
  passages, of the advisory table and of the questions. A number in words, a date
  or a year alone is not checked.

For a wiki of fairy tales, a misremembered ending harms nobody, and the range of
questions the agent answers is worth having. Part 2 is about a wiki where a wrong
answer costs money.

## Part 2 — The code searches

In the pre-retrieval mode the code searches the wiki before the model is called,
and the model receives the text the code found. The model of this mode has no
tool that searches the wiki: it writes the answer, and the code decided what it
reads.

### The route of a question

The input workflow (section 2 of [`query_nodes.md`](query_nodes.md)) asks a fixed
sequence of questions. The order is a design decision:

1. **`Q_IN_BLACKLIST`**: does the question name a term of the blacklist, the
   `[fuera_de_alcance]` list of `wiki_config.toml`? A match ends in the refusal
   `R_BLACKLIST`.
2. **`Q_IN_ROSTER`**: does the question name a topic of the **coverage roster**?
   The roster is the list of names the wiki covers. The names are the titles of
   the concept pages, the topics the pages declare, the aliases, and the
   categories and keys of the data files. The **Vocabulary** tab lists the roster.
3. **`Q_PAGE_OPEN`**: is the user reading a wiki page? An open page makes the
   question covered, and goes first in the text the model receives.
4. **`Q_SEARCH_WIKI`**: when the question names a topic, or a page is open, the
   code builds the wiki context. The whole pages the question names come first: the
   summary pages of the topic, then the concept page by its title or alias, then the
   other pages that declare the topic. The full-text index fills the remaining places.
5. **`Q_IS_COLLECTION`**: is the question about the wiki as a whole? Such a
   question receives `overview.md` and `index.md`.
6. **`Q_IS_DATA`**: does the question name data, or ask for investment advice?
   Such a question goes to the model with the data tools.
7. **`Q_FRAGMENT_FOUND`**: for a covered question with no wiki page, did the
   search of the source fragments find text?
8. **`Q_ROSTER_REFUSAL`**: otherwise, the question is refused, with
   `R_NOT_IN_ROSTER` when it names no topic and `R_NOTHING_FOUND` when it names a
   topic and the searches found nothing.

The search uses the stems of the words: the Spanish Snowball stemmer turns
«cauciones» into «caucion». A refusal of the input workflow costs no call to the
model, and gives the same text on every run.

### Three cases every pre-retrieval wiki has

#### 1. A wiki page answers

«¿Qué es una caución bursátil y por qué se la considera de bajo riesgo?»
(*what is a* caución bursátil *and why is it considered low-risk?*). A
**caución bursátil** is a short loan made through the stock exchange, backed by
securities the borrower deposits.

`Q_IN_ROSTER` matched six names, among them `Cauciones bursátiles` and
`caución bursátil`. `Q_SEARCH_WIKI` ran the query `"caución" OR "bursátil" OR
"considera" OR "bajo" OR "riesgo"` and found the pages. The turn went to
`Q_AGENT_PAGES`: the model received six wiki pages and called no tool. The first
two are whole pages that the question names. The summary `12-cauciones-bursatiles.md`
came first: its title «12 Cauciones Bursatiles» contains the topic. The concept page
`cauciones-bursatiles.md` came second, by its title. The search filled the other four
places.

The answer ends with two reference lines. The model wrote the first line, and the
code labelled it. The code wrote the second line (`Q_CITE_PAGES`):

> Además, el modelo recibió estas páginas de la wiki: 12-cauciones-bursatiles.md, cauciones-bursatiles.md, fci-money-market.md, aforo.md, comunicacion-a3500.md, byma.md. No se puede determinar si la respuesta las usó.

The second line says what the code knows: the pages the model received. Which of
them the answer used cannot be read from the text. **Verify the sentences** splits
that list into the pages that support a sentence and the pages that support none.

#### 2. A finance question the wiki does not cover

«¿Qué es un ETF?» (*what is an ETF?*) is a reasonable finance question about an
instrument with no page in this wiki. `Q_IN_ROSTER` matched nothing, so the code
did not search the wiki. The route ended in `R_NOT_IN_ROSTER`, with no call to
the model:

> No recibí de la wiki información relacionada con esta pregunta.

The refusal comes before any search. A search of the source fragments would match
words of the question in documents about other instruments. A model that received
those fragments could answer from its training and cite a document that does not
support the answer. The roster check removes that route before the model runs.

#### 3. A question outside the subject

«¿Cuál es la capital de Francia?» took the route of case 2 and ended in the same
refusal, `R_NOT_IN_ROSTER`, with 0 tokens. In the agent mode, the same question
cost three tool calls and 37,810 input tokens, and the answer depended on the
model declining.

### The blacklist

«¿Qué son los CEDEARs y conviene comprarlos?» names `cedears`, a term of the
blacklist of the finance wiki. The route ended in the third node of the
workflow, `R_BLACKLIST`:

> No puedo responder esta pregunta: menciona «cedears», que está en la lista de temas sobre los que esta wiki no tiene permitido responder.

The owner of the wiki writes the blacklist. The blacklist names subjects the wiki
must not answer, even when a page mentions them. The agent mode checks the
blacklist too, in its input workflow. The mode with no verification does not.

### Questions about the whole wiki

A question about the collection names no topic, so the coverage roster cannot
recognise it. The three questions of Part 1 were asked again on the fairy tales,
in the pre-retrieval mode. `Q_IN_ROSTER` matched nothing for each of them, and
`Q_IS_COLLECTION` recognised the shape of the question. Each turn went to
`Q_AGENT_COLLECTION`, and the model received `overview.md` and `index.md`.

Expressions such as «each» or «in common» mark a question about the collection.
The expressions are the `[intent] collection` list of `wiki_config.toml`, or the
base list of the language of the wiki. The answer about the endings came from the
overview alone, and said so:

> These endings come only from the overview. I did not open the individual summary pages, so the wiki pages for each tale could add detail that I have not verified.

The model in the agent mode read thirteen pages for the same question. The model
in the pre-retrieval mode read two, and gave the same three endings.

### A page open on the screen

«¿Cuál es el plazo mínimo?» (*what is the minimum term?*) names no topic. The user
asked it while reading the page «Plazo fijo UVA». `Q_PAGE_OPEN` found the page,
the code searched the wiki, and the model received the open page first:

> El plazo mínimo del plazo fijo UVA es de **180 días**. Además, permite precancelar a partir del día 30, con penalización sobre la tasa pactada.

### A follow-up question

«¿Y qué riesgos tiene?» (*and what risks does it have?*) was the second turn of a
conversation about the caución bursátil. The question names no topic.
**`Q_REWRITE`** asked the model to rewrite the question so that it stands alone,
and the rewrite was «¿Qué riesgos tiene la caución bursátil?». The roster check
and the search ran on the rewrite (`Q_CONDENSED`), and the model answered the
original question with the pages of the caución.

### A datum that only a source document holds

«¿En qué se convertían los lacayos de Cenicienta a la medianoche?» (*what did
Cinderella's footmen turn into at midnight?*) was asked in the Spanish tale wiki,
`examples/cuentos-de-hadas`. The pages of Cinderella say that the fairy godmother
turns lizards into footmen. No page says what the footmen turn back into.

The model received the pages and declined with the fixed sentence of the prompt:
«No encontré la respuesta en el texto que recibí de la wiki.» **`Q_RETRY_SOURCES`**
then searched the source documents, found four fragments of `La Cenicienta.pdf`,
and called the model again with them (`Q_AGENT_FRAGMENTS`). The answer from a source
fragment goes through two checks:

1. **`Q_IS_GROUNDED`** counts the fraction of the content words of the answer that
   the fragments contain. The answer is grounded when the fraction is at least
   `[pre_retrieval] min_coverage`, 0.2 by default. This answer had 0.78.
2. **`Q_SOURCE_WARNING`** appends a warning, because no wiki page backs the
   answer:

> ⚠️ Esta respuesta proviene de un documento fuente sin página curada del wiki; verificá.

The default threshold 0.2 is a starting value, not a measured one.

### The check of the pages

The pages the search finds share words with the question. Sharing words does not
mean that the pages answer the question. The control **Check that the pages answer
the question** adds a check after the answer, in the pre-retrieval mode. The
question about the caución bursátil was asked with each option:

- **Evidence quote** (`Q_EVIDENCE_QUOTE`): the prompt asks the model to open the
  answer with the sentence of the pages that answers the question. The code then
  looks for that sentence in the pages. The answer opened with
  «Evidencia: «Las cauciones bursátiles son operaciones de muy corto plazo…»»,
  and the code found the sentence (`found` = True).
- **Judge** (`Q_JUDGE_PAGES`): a separate call to the model receives the question
  and the pages, and answers yes or no. The verdict was «sí» (`passed` = True).

When a check fails, the answer stays, with a line that says the pages do not
answer the question (`Q_RELEVANCE_WARNING`). Neither check is calibrated: the
judge is the same kind of model as the one that answered. The turn with the judge
used 13,813 input tokens, and the turn with no check 9,508.

## Wikis whose facts change

The finance wiki keeps the figures that change, such as rates and prices, in the
folder `datasets/`, outside the pages. Each data file is a Markdown table with its
date, `as_of`, and its origin, `fuente`. [`ingestion_walkthrough.md`](ingestion_walkthrough.md)
explains why a figure stays out of the prose of a page: the page is written once,
and the figure has a date.

In the pre-retrieval mode, the code also reads the data files before the model
runs:

- **`Q_NAMES_CATEGORY`** asks whether the question names a category of data, such
  as `dolar`.
- **`Q_NAMES_KEY`** asks whether the question names a key of that category, such
  as `MEP`. With a key, **`Q_KEY_ROWS`** injects the rows of the key. Without a
  key, **`Q_KEY_NAMES`** injects only the names of the keys.

The model also keeps the data tools of the **finance overlay**, the code written
for this one wiki (`base/domain/extensions/finance_argentina/`). The data tools
read the same files.

### A datum with its date

«¿A cuánto está el dólar MEP?» (*what is the MEP dollar trading at?*). The **MEP**
is one of the several legal exchange rates of Argentina. The question named the
category `dolar` and the key `MEP`, so the code injected the row of the key. The
model also called the tool `K_DOLLAR`:

> El dólar MEP está en ARS 1180,00 de compra y ARS 1185,00 de venta, según la cotización del 25/06/2026.

The answer carries two kinds of citation. **`Fuente:`** names the external origin
of the figure, `ambito.com`. **`Referencia:`** names the file of the wiki that
holds the figure, `dolar.md`. `C_FIGURES` checked the two figures and found both
in the data.

### An alias

«¿A cuánto está el billete verde?» (*how much is the green note?*, Argentine slang
for the dollar). The question never names the dollar. The owner of the wiki wrote
the alias in `wiki_config.toml`:

```toml
[alias_datos]
dolar = ["billete verde", "divisa"]
```

`Q_IN_ROSTER` matched `billete verde`. The question named the category through
its alias and no key, so the code injected the key names, and the model called
`K_DOLLAR`. The model listed the five types of dollar of the file, and named the
official dollar as the usual one. `C_FIGURES` checked ten figures and found all of
them in the data.

The ingestion generates aliases too, from the documents. The owner writes the
aliases that the documents never use, such as a slang name.

### Investment advice

«Tengo $1.000.000 que no necesito por 3 meses, ¿qué alternativas tengo y cuánto
ganaría?» (*I have one million pesos I do not need for 3 months; what are my
options and how much would I earn?*). No term of the question is in the roster.
`Q_IS_DATA` recognised the shape of the question: an expression of advice and an
amount. The turn went to `Q_AGENT_TOOLS`, and the model called `K_ALTERNATIVES`.

`K_ALTERNATIVES` computes the gain of each alternative in Python, from the data
files. **`Q_ADVISORY_TABLE`** appends the table the tool returned, below the prose
of the model, whether or not the model copied the table. The first row:

> | plazo_fijo | Banco Credicoop | 90d | 41,84% | $90.000 | 2026-06-25 | bcra.gob.ar |

The **TEA** column is the effective annual rate. Between the prose and the table,
the code writes a note on who wrote each figure:

> Las cifras del texto anterior las escribió el modelo, y el código no las verificó. Las cifras de la tabla siguiente las calculó el código con los datos de su columna «fuente».

The note is older than the figure check: `C_FIGURES` checked the two figures of
the prose of this answer, and found both. The table ends with the instruments whose
gain the code does not estimate, under **Renta variable** (*variable income*):
shares depend on the market price.

### What the code does not estimate

«¿Cuánto ganaría con acciones de YPF?» (*how much would I earn on YPF shares?*).
The question named the topic `acciones` and the key `YPF`. The model received the
pages about shares and the row of the price, called `K_SHARE_PRICE`, and wrote:

> No es posible estimar cuánto ganarías con acciones de YPF.

The data file of shares declares `metodo_calculo: no_deterministico`: no formula
of the finance overlay gives the gain of a share. The answer gives the current
price, with its date, and no gain.

## The loop closes: an answer can become a page

An answer can be worth keeping: the comparison of the endings of three tales is
on no single page. The **Save** button of the conversation turns the conversation
into a wiki page:

1. The user types a title and clicks **Save**.
2. The model drafts a page with the questions and the answers.
3. The user reads and edits the draft in the dialog **Review the page**, and
   clicks **Save to wiki**.
4. The code writes the page, indexes it and commits it to the git repository of
   the wiki.

The model has no tool that writes to the wiki: its tools are the four kinds of
Part 1, and every one of them reads. A page enters the wiki from the chat only
through the click of step 3. The ingestion writes pages with no such click, so the
two ways of writing differ in who authorises them. Every page, from either way, is
a git commit the owner can read and revert in the **History** tab.

## What this adds to the original idea

Karpathy's note proposes that a model keep a persistent encyclopedia, instead of
searching raw fragments for every question.
[`ingestion_walkthrough.md`](ingestion_walkthrough.md) shows the encyclopedia
growing. This document shows what the query side adds:

1. **A coverage check before the model.** The note has no notion of declining a
   question. In the pre-retrieval mode, «the wiki does not cover this» is a branch
   of Python code that never reaches the model. The refusal has the same text on
   every run.
2. **A second kind of knowledge, with a date.** The note does not separate a
   definition from an exchange rate. The data files keep a figure with its date,
   and the code computes over the figures instead of the model.
3. **An audit of each answer.** The buttons **Criterion**, **Sources** and
   **Model answer** open the audit of an answer. The audit shows the route through
   the nodes, the passages the model received, and the text of the model before
   the code replaced it.

The note also asks for a search engine with vectors and re-ranking. This project
searches with the full-text index only, so a page written in other words than the
question is not found. The aliases and the stemmer reduce that gap, and do not
close it. §1 of the [Programmer Manual](manual/programmer_manual.md) grades each
idea of the note as done, partial or deferred.

## Verify it yourself

- `uv run python scripts/capture_query_walkthrough.py` asks the questions of this
  document again, with the model of `.env`, and writes
  [`query_walkthrough_appendix.md`](query_walkthrough_appendix.md). `--only` runs
  some cases: `--only finance-alias finance-blacklist`.
- `tests/unit/test_docs_query_walkthrough.py` checks that every quotation of this
  document is in the appendix, and that every node of the appendix is a node of
  [`query_nodes.md`](query_nodes.md).

## Where to go next

- [`query_nodes.md`](query_nodes.md): every node of the three modes, with its code
  and its trace.
- [`manual/workflows/6.7-chat-rag.md`](manual/workflows/6.7-chat-rag.md): the
  reference of the chat, with the tools and the prompts.
- [`contracts/chat-retrieval.md`](contracts/chat-retrieval.md): the contract of the
  order of the route and of the citation lines.
- [`ingestion_walkthrough.md`](ingestion_walkthrough.md): how the pages, the
  index and the aliases this document reads were built.
