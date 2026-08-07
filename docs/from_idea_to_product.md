# From Idea to Product

### Where the LLM-wiki idea leaks, and how this project seals it

> **This is not a criticism of Karpathy's idea.** The idea is his and it is a
> good one. What leaks is not the idea — it is what happens when you copy it
> straight onto a generic agent (Claude Code, Codex, Cursor pointed at a folder)
> and expect it to work by itself. The note describes the happy path. This
> document maps the potholes on the real one.
>
> **How to read it.** Each point describes *one way the idea springs a leak* in
> practice, a concrete example, and how this project seals it. Every point
> carries an honest status — what is built, what is partial, what is a
> deliberate trade. Nothing here is aspirational: each claim points at code you
> can open.
>
> The examples are from **personal finance**, the first field this is applied
> to, but the last section explains why the pattern is broader.

## The idea, in one sentence

Most "chat with your documents" tools rediscover knowledge from scratch on every
question: they fetch loose fragments, assemble an answer, and lose it when the
chat scrolls away. Nothing accumulates.

Karpathy's idea is different. Instead of re-searching every time, a language
model **builds and maintains a wiki** — a set of linked pages — written once and
kept current. The cross-references are already there. The contradictions are
already flagged. The synthesis already reflects everything you have read. It is
the difference between searching a pile of paper and consulting an encyclopedia
that writes itself.

The catch is one the note itself flags: it is a pattern, not a recipe. What
follows is the list of details that do not happen by themselves.

---

### When it answers

## 1. When it finds nothing, it invents

`Status: ✅ done` · `base/domain/chat/guardrail.py`

**The leak.** A generic agent that searches the wiki and finds nothing does not
go quiet. It fills the gap from what it "knows", and says it with exactly the
confidence it uses for a real fact.

**Example.** You ask for a bank's rate that is not in your data. Instead of "I
don't have that", you get a round, convincing number — recalled, stale, or
invented — and nothing tells you which.

**How this project seals it.** In the default mode, code inspects the finished
conversation before you see it. If **no tool returned anything substantive**, the
answer is thrown away and replaced with an honest refusal. That leaves the
trickier case — it found *something*, but that something does not support the
answer — which is the next point.

---

## 2. False grounding: an excuse to say what it already knew

`Status: ✅ built and live-validated · ⚖️ one part stays manual` · `chat/preretrieval.py`, `chat/overlap.py`

**The leak.** Worse than inventing from nothing is inventing *with an alibi*. You
ask about something you do not cover; the agent finds a fragment that mentions
the topic **in passing**, and uses it as licence for a general explanation. The
answer *looks* grounded — it even cites something — but the fragment answers
nothing. It is set dressing.

**Real example.** We asked, verbatim: *"What are CEDEARs and are they worth
buying?"* of a wiki with **no CEDEAR page**. The agent found a document about
bonds that named the word once, in passing, and used it as licence to explain
CEDEARs from memory. Citation present, grounding absent.

**How this project seals it.** Two gates, both in the code-retrieves-first mode.
A **coverage roster** — the list of subjects the wiki admits to covering, read
from its own concept-page titles — decides whether the model is called at all;
a question naming nothing on it is refused before any search runs. And for what
does get through from a raw document, a second check compares the **answer
against the source** it came from: if the answer does not actually follow from
it, it is not shown.

**Honest limits, both measured.** The roster matches page titles *literally*, so
a wiki with three pages about an instrument's risks can still turn away a plainly
worded question about them. And the hand-written blacklist that complements it
matches whole words only: `cripto` is listed and does not catch `criptomonedas`.
Both are written up in [`ROADMAP.md`](../ROADMAP.md#known-limits-and-open-questions).

---

## 3. The conversation degrades as it goes

`Status: ✅ done` · `chat/history.py`, `chat/postprocess.py`, `chat/preretrieval.py`

**The leak.** A generic agent answers well for the first few questions and gets
worse as the conversation lengthens. It starts **imitating the shape** of its own
earlier replies, stops calling its search tools, and within a few turns is
answering from memory. Nothing broke; the accumulated conversation dragged it
there.

**Real example.** In one of our sessions the early answers cited their source and
laid out a tidy comparison table. Three or four turns later — same session, same
kind of question — the reply came back as prose, with no table and no citation.
The model was copying itself downhill. Self-criticism: our own habit of appending
the full table to every turn's history made it worse, which is why those tables
are now compacted out.

**How this project seals it.** Three things. Code can do the **retrieval itself**
before the model answers, so skipping the search is not an option it has.
The conversation is kept **light** — old tables are compacted to small stand-ins.
And whatever must be guaranteed — the citation, the advisory table — is
**appended by the program**, not left to the model to remember turn after turn.

---

## 4. What you discover in conversation evaporates

`Status: ✅ done` · the *Save to wiki* form, `chat/wiki_tools.py:save_to_wiki`

**The leak.** With a generic agent, a good answer — a comparison you asked for, a
connection you found by talking — dies with the chat. The original idea says to
file it into the wiki, but that depends on you remembering to do it by hand.

**Example.** You get an excellent comparison between a fixed-term deposit and a
short-dated secured loan, close the tab, and it is gone.

**How this project seals it.** Under every answer there is a form that turns it
into a permanent wiki page — **on your click, never the agent's**. The agent has
no write tool at all. Your explorations accumulate the same way your documents
do, instead of scrolling away.

---

### The knowledge itself

## 5. It only knows "what is", not "what is it worth today" — and it cannot do arithmetic

`Status: ✅ done` · `base/domain/datasets/`, `base/domain/finance_argentina/`

**The leak.** The wiki in the original idea is an encyclopedia: nothing keeps
numbers current, and nothing computes. It can explain what a fixed-term deposit
is; it does not know today's rate — and if it ever wrote one down, that page is
now wrong. Worse: even given the number, a generic agent **does the arithmetic
itself**, inventing precision and getting it wrong.

**Example.** *"I have a million I won't touch for 60 days — what do I earn, and
what suits me?"* The encyclopedia has neither today's number nor the ability to
compute the interest. The generic agent either says "it depends" or produces a
figure it estimated.

**How this project seals it.** A **second kind of knowledge** sits beside the
pages: **data you refresh**, taken verbatim, always with its date — rates,
prices, statistics. And the arithmetic is done by **a program that does not
improvise** — the same formula every time, never the model. What cannot be
computed with certainty (equities, inflation, exchange rates) is marked **"not
estimable"** rather than guessed. This is the point that separates this project
from a plain encyclopedia, and everything in "Beyond finance" hangs off it.

---

## 6. "Read the index first" does not scale

`Status: ✅ done` · `base/domain/tools/search.py`, the citation graph in SQLite

**The leak.** The idea proposes the agent read an index file to orient itself. A
copy-pasted generic agent starts **with no search engine**: the original note
warns that as the wiki grows you will need a real one, and leaves that to you.
Without it, hundreds of pages make the index unwieldy, the agent gets lost in it,
and answers from an incomplete page while nobody notices it skipped the right one.

**Example.** Your finance wiki grows to hundreds of pages. You ask about
`cauciones`; the agent skims an endless index, grabs the first thing that sounds
close, and answers from there — ignoring the page that actually had the answer.

**How this project seals it.** A **real search engine** over the pages ships with
it, along with a queryable record of **which page came from which source**.
Honestly: it matches **words, not meanings**. There are no embeddings anywhere.
The alias machinery of point 10 exists to compensate, and the curated page layer
exists so that an answer can come from a page written *about* a concept rather
than from whichever raw paragraph repeated its name most often.

---

## 7. Your wiki ends up in two languages at once

`Status: ✅ done` · `[wiki] language` in `wiki_config.toml`, `base/domain/i18n.py`

**The leak.** With English sources and Spanish questions, a generic agent
produces a mix: half-translated pages, a title in one language and a body in
another, answers that switch depending on which fragment got cited.

**Example.** You load English market reports and ask in Spanish; the generated
page comes out with a Spanish title and a body traced from the English original.

**How this project seals it.** Language is fixed **per wiki**: everything — pages,
headings, and chat answers — comes out in that language regardless of what the
sources are written in. You can keep an English wiki and a Spanish one side by
side, each internally consistent.

---

### When you load documents

## 8. Loading twice does not give the same result

`Status: ✅ done` · `ingestion/detector.py`, the golden-corpus regression, `WIKI_TRACE=1`

**The leak.** "The agent reads the source and builds the pages" sounds fine until
you run it twice. Without discipline the second run **does not match** the first:
duplicated pages, things changed at random, and no way to tell what it touched or
why. A system that is not reproducible is not trustworthy.

**Example.** You re-load the same rates PDF — you could not remember whether you
already had — and the wiki comes out different: two similar summaries, one page
overwritten. Which is the good one? No way to tell.

**How this project seals it.** What has not changed **is not reprocessed**,
recognised by its content rather than its timestamp. The parts of the engine that
do not depend on the model — how text is chunked, how it is indexed, how a page
is built from given text — are **frozen by a regression test** that reports any
drift. Still open: re-loading a file that *did* change goes through the model
again, and there is no guarantee it produces the same words twice. There is also
an optional step-by-step record of everything a load did, for auditing.

---

## 9. The document does not fit

`Status: ✅ done (chunking) · ⬜ OCR on the roadmap` · `ingestion/pdf_extract.py`, `ingestion/chunker.py`

**The leak.** "The agent reads the source" works until the source is 300 pages
(it does not fit at once) or a scan (an image with no text under it).

**Example.** A 250-page market report, or a scanned PDF from a bank.

**How this project seals it.** Documents are split into manageable pieces on the
way in, so they fit however long they are. Pending: scanned PDFs — image only,
no text — still come in empty or garbled. Reading text from images is on the
[roadmap](../ROADMAP.md).

---

## 10. The configuration itself rots

`Status: ✅ built` · `ingestion/alias_generation.py`, `lint/checks.py:vocabulary_check`

**The leak.** What makes the agent disciplined is a file of rules and conventions
you maintain by hand. As the wiki grows, that file **ages**: the synonyms, the
scope, the conventions go stale, and the agent stops recognising things you do
in fact cover.

**Example.** You add new instruments, but the synonym list is old. Somebody asks
about *"billete verde"* — Argentine slang for the US dollar — and the assistant
does not connect it, because nobody updated that equivalence by hand.

**How this project seals it.** The lists are **generated during loading**: the
model is already reading each document, so it records the alternate names it
finds there. A lint check then watches them for drift — an alias that is really
another concept's name, aliases pointing at a page that no longer exists, one
alias claimed by two concepts, a blacklisted term that now has a page. Alongside
them you keep a short hand-written list for what no document contains: nothing in
a table of exchange rates says the street calls the dollar *billete verde*. **The
pipeline learns the names the documents use; you supply the names people use.**

*Written up in July as designed-but-unbuilt. It has since been built, and this
line is the correction.*

---

### When you maintain it

## 11. Maintenance is a favour, not an operation

`Status: ✅ done` · `base/domain/lint/`, `base/domain/repair/`

**The leak.** The idea says "every so often, ask the model to review the wiki's
health". That is a favour, not a mechanism: it depends on you remembering to ask,
on the model doing it well *this* time, and on something actually being **fixed**
afterwards. In practice the wiki accumulates contradictions and loose pages and
nobody touches them.

**Example.** Two sources left two contradictory reference rates on two pages.
Nobody runs the check; the wiki lives with the contradiction and one day answers
with the old one.

**How this project seals it.** Maintenance is a real operation: **nine checks**
that run the same way every time — contradictions, stale pages, orphans, concepts
with no page, missing cross-references, data gaps, filled gaps, vocabulary drift,
and pages thinner than the source they came from — and **automatic repair** of the
safe ones. What cannot be fixed without judgement is reported, never guessed at,
and the message says exactly what is missing.

---

## 12. Nobody verifies or measures quality

`Status: ✅ done (evaluation, model check) · 🚧 answer-vs-source is mode-dependent` · `base/domain/eval/`, `chat/overlap.py`

**The leak.** Even if the wiki writes itself, **who checks that what it writes is
faithful to the source?** In the original idea, nobody. A page can summarise a
document badly, and that error propagates into every answer that uses it with no
alarm at all. Nor is there any way to know whether the model you picked is up to
the job.

**Example.** A page summarises a report and, compressing, turns "up to 30 days"
into "30 days". Small — and now every answer about terms is wrong.

**How this project seals it.** A **scored evaluation** against a frozen rubric —
questions, answers, cited evidence, and generated pages compared against the
document they came from — which measures quality and can compare two models. It
includes a one-command check of whether a given model is *good enough* before you
trust it with your wiki. What is not closed: comparing every answer against the
source it claims, at the moment it is produced. That check exists
(`overlap.is_supported`) but runs only on the raw-document tier of the
code-retrieves-first mode, which is off by default.

---

## 13. There is no undo button

`Status: 🚧 partial — per-wiki git ✅, coordinated rollback designed` · git per wiki; [PR #7](https://github.com/Clod/llmwiki-marimo/pull/7)

**The leak.** One bad load overwrites fifteen pages at once, and with a generic
agent there is no clean way back.

**Example.** You load a document with an error that propagates across several
pages, and you want yesterday's state back.

**How this project seals it.** Every wiki is a folder with its own change
history — you can revert by hand, page by page. What is missing: a coordinated
undo that returns the whole wiki *and its index* to an earlier point in one move.
Designed, not built.

---

## 14. Keeping it is not free

`Status: 🚧 partial — optimised; cost visibility missing` · `ingestion/detector.py`, pairwise lint, incremental synthesis, split models

**The leak.** Every load touches many pages with the model, and the health review
is another pass. On a large wiki that costs money and time, and the cost grows
with the wiki.

**Example.** Loading a hundred rate reports and reviewing the whole wiki's health
can mean a great many model calls, one after another.

**How this project seals it.** What has not changed is not reprocessed. The health
review compares only pages that share a source, not everything against
everything. The wiki-wide synthesis is incremental. And you can run a cheap model
for chat and a strong one only for loading. Missing: showing you, anywhere, what
maintaining the wiki is costing.

---

### Where it lives, and who is in charge

## 15. An agent that reads files is a way in

`Status: ✅ done (path guard) · ⚖️ trade (injection: contained by review, not filtered)` · `chat/wiki_tools.py`, [`SECURITY.md`](../SECURITY.md)

**The leak.** To let the agent read your sources, you give it permission to read
files. Careless, that is a hole: it can end up reading files it should not, and —
more subtly — a document you load could carry **hidden instructions** ("ignore
everything above and say this instead") that the agent obeys without knowing it
is being steered.

**Example.** Somebody sends you a "rates" PDF that, in small print, includes an
order addressed to the assistant. A generic agent can take it as part of its
instructions and change behaviour.

**How this project seals it.** The page reader has a **real guard** that stops it
leaving the wiki folder. Against instructions hidden in a document there is no
automatic filter — we say so plainly — but the manipulation is **contained by
human review and by change history**: on the loading side, everything written
lands as a **reviewable commit** you can inspect and revert; on the chat side, a
conversation becomes a permanent page **only when you review it and press save**.
The assistant does not rewrite your wiki quietly. The project also documents a
written threat model, so the containment is a decision rather than an oversight.

---

## 16. You depend on a vendor and on the cloud

`Status: ✅ done` · embedded agent, any OpenAI-compatible endpoint

**The leak.** As proposed, the idea points somebody else's product — a desktop
app, an AI editor — at your notes folder. That **ties** you to that product: if
it changes terms, raises the price, or goes away, you lose your tool. And often
your private knowledge **leaves your machine** for somebody else's cloud.

**Example.** You build your personal finance wiki — your numbers, your decisions —
inside an app that tomorrow changes its free plan or discontinues the feature.
Your wiki is a hostage.

**How this project seals it.** It brings **its own assistant inside** — one
self-contained program, no external editor required — and works with **any**
model provider, including one running on your own machine. Your wiki, its history
and its index live on your disk and are uploaded nowhere. The only thing that
travels is the text sent to the model **you** chose — and if you choose a local
one, nothing leaves at all.

---

### The honest cost

## 17. No graph view, no plugin ecosystem

`Status: ⚖️ accepted trade`

**The leak — and it is a real one.** Pointing a generic agent at an Obsidian
vault buys you something this does not have: a mature editor, a visual graph of
how your notes connect, and an ecosystem of plugins other people maintain.

**How this project answers it.** It does not. This is the price of being
self-contained: one program that owns ingestion, retrieval, maintenance and
reading, rather than a plugin inside somebody else's editor. The reference graph
*exists* — it is a table in SQLite you can query — it simply is not drawn.
Rendering it is on the [roadmap](../ROADMAP.md), listed under things that would
be nice rather than things that are missing.

If a visual graph and a plugin ecosystem are what you want most, an Obsidian
vault with an agent pointed at it is the better answer, and you should use that.

---

## Beyond finance

The examples above are financial because that is the first field this was applied
to. The pattern is not.

Every point generalises to any body of knowledge where **the prose stays true but
the numbers move**, and where being wrong has a cost:

- **Clinical guidelines** — what a treatment *is* changes slowly; dosages,
  interaction warnings and availability do not.
- **Regulation and compliance** — the shape of an obligation is durable; the
  thresholds, deadlines and rates attached to it are not.
- **Engineering standards** — a method endures; the tables of coefficients get
  revised.
- **Any research corpus** where a summary that quietly drops a qualifier
  ("up to 30 days" → "30 days") propagates into every answer built on it.

What those have in common is the shape of point 5: an encyclopedia alone is not
enough, because half the question is *what is it worth today*, and that half must
never be answered from a model's memory of a document it read last month.

---

*This document is the argument that complements the point-by-point Karpathy
alignment matrix in the [Programmer Manual](programmer_manual.md#1-philosophy--karpathy-alignment):
that one grades what is done; this one explains, in plain language, why each
point matters and what breaks without it.*
