# Example wikis

Pre-ingested, ready-to-browse demo workspaces used by [`quickstart.py`](../quickstart.py).
Each folder is a complete wiki workspace — drop it in as your `WIKI_PATH` and the
read app shows real content immediately, **no ingestion or LLM required just to
browse** (chat needs an LLM; browsing the generated pages does not).

| Demo | Language | Contents |
|------|----------|----------|
| `fairy-tales/` | English | Three public-domain fairy tales (Cinderella, Snow White, Little Red Riding Hood) ingested into concept + summary pages, with a prebuilt FTS index. |
| `cuentos-de-hadas/` | Spanish (`es`) | Three public-domain tales (La Cenicienta, Blancanieves, Caperucita Roja — Perrault & the Grimms) ingested into Spanish concept + summary pages, with a prebuilt FTS index. Mirrors `fairy-tales/` to show the `language = "es"` path. |
| `finanzas-argentinas/` | Spanish (`es`) | Argentine personal-finance advisor. Six instrument docs (plazo fijo, FCI money market, caución, acciones, bonos CER/UVA, dólar linked) ingested into Spanish concept + summary pages, **plus a `datasets/` folder** of live rates/prices that a deterministic `estimar_alternativas` advisory ranks and cites (never fabricating a number; equities/FX flagged non-estimable). Ships `GUIA_DEMO.md`, a step-by-step demo script that doubles as a 9-question acceptance test. |

## What's inside a demo

```
fairy-tales/
├── wiki/              # generated concept/summary/overview/index pages (markdown)
├── sources/           # the original PDFs the wiki was built from
├── .llmwiki/index.db  # prebuilt SQLite + FTS5 index (so search/chat work offline)
├── wiki_config.toml   # per-wiki assistant config (language, system prompt, prompts)
└── .gitignore         # a realistic per-wiki ignore (note: it ignores .llmwiki/)
```

> The `.llmwiki/index.db` is **force-added** in this repo so the demo ships
> search-ready. The demo's own `.gitignore` still excludes `.llmwiki/`, so when
> the installer copies this folder into your `wikis/` and you later ingest your
> own documents there, your index churn won't be tracked.

A demo may also carry a **`datasets/`** folder (markdown tables of live,
structured data such as rates or prices) and a **`GUIA_DEMO.md`** walkthrough —
`finanzas-argentinas/` has both. Datasets feed the chat's `query_dataset` tool
and any domain advisory; see that demo's guide for a worked example.

## Adding a demo

Drop another pre-ingested workspace folder here; `quickstart.py` auto-discovers
any subfolder that contains a `wiki/` directory.
