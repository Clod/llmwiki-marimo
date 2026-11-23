# Adding documents: the upload and the scan

You copy ten PDFs into the folder `sources/` of a wiki with the file manager. The
wiki does not read them by itself: nothing watches the folder. When you open the
wiki, a warning above the page lists the files of `sources/` that were never
ingested. The button **Scan sources/ for changes** of the **Ingest** tab ingests
them.

This guide says what the scan does to the wiki, and how the scan differs from the
upload form. It also says what happens when a file changes, leaves the folder or
changes its name. The reference for programmers is
[§6.5 of the Programmer Manual](manual/workflows/6.5-scan-sources.md).

## Two ways to add a document

The **Ingest** tab has two panels. Both end in the same ingestion of each file, and
both write the same pages:

| | The upload form | The scan |
|---|---|---|
| **What you do** | choose files on your computer and click **Ingest** | copy files into `sources/`, then click **Scan sources/ for changes** |
| **Which files it ingests** | the files you chose | every file of `sources/` that is new, changed, or whose last ingestion failed |
| **Where the file goes** | the form copies the file into `sources/` | the file is already in `sources/` |
| **Files removed by hand** | not looked for | removed from the wiki first, with the pages only they supported |
| **Aliases of the data files** | not generated | generated again when `datasets/` changed |
| **Review at the end** | deterministic, or with the model when you tick **Full repair with the model, slower** | deterministic only |
| **Verification of the pages** | with the box **Verify the generated pages** | with the box **Verify the generated pages** of the scan panel |

Use the upload form for one or two documents you have at hand. Use the scan after
you copy files into `sources/` with another program, or after you edit or delete a
file there.

## What the scan does

The scan runs these steps in order (`scan_sources` in `base/services/ingest.py`):

1. **Removes the files that left `sources/`.** A document of the wiki whose file is
   no longer in `sources/` is deleted from the wiki. The deletion also deletes every
   page that only that document supported. The progress log names each one: «🗑
   … is no longer in sources/: deleted with N page(s)».
2. **Lists the candidate files.** The scan walks `sources/` and its subfolders,
   and keeps the files of the formats the wiki reads (README, «Document formats»).
   The scan skips a file or a folder whose name starts with a dot. It also skips an
   image that a Markdown file of `sources/` shows. That image is a figure of the
   Markdown file, not a document of its own.
3. **Ingests each candidate that needs it.** A file is ingested when it is new, when
   its contents changed, or when its last ingestion failed. An unchanged file is
   skipped with the line «⏭ … — already up to date», and costs no call to the
   model.
4. **Generates the aliases of the data files.** When the files of `datasets/`
   changed since the last scan, the model proposes other names for their terms.
   The progress log lists each new alias for review in the **Vocabulary** tab.
5. **Reviews the pages it wrote.** When the scan ingested at least one file, a
   final review runs. The review lints the pages of the ingested files. The review repairs
   what the repairs without the model can fix, adds the «See also» links, and makes
   one git commit.
6. **Verifies the pages**, only with the box **Verify the generated pages** ticked.
   The local verifier checks each sentence of the pages the scan wrote against the
   text of their sources. The text of the pages does not change.

The log ends with one line of counts: «🏁 Scan complete — ingested: N, skipped: N,
failed: N».

## What the scan writes

| Place | What changes |
|---|---|
| `sources/` | nothing: the scan reads the files and never edits them |
| `wiki/` | the summary page of each ingested file, the concept pages it creates or updates, `index.md`, `overview.md` and `log.md` |
| `.llmwiki/index.db` | the rows of each document, its fragments and the citation edges |
| `.llmwiki/aliases.generated.toml` | the aliases of the new concepts and, after step 4, of the data terms |
| the git history of the wiki | one commit per removed file, `delete source: <file>`, one per ingested file, `ingest: <file>`, and one of the final review |

Each commit is a point of the **History** tab. To undo a scan, go back to the point
before its first `ingest:` commit.

## When a file changes, leaves or changes its name

**A changed file.** The scan compares the modification time of the file first, and
its contents only when the time differs. A file saved again with the same contents
is not ingested again. A file with new contents is ingested again: its summary
page is written again from the new text.

**A file removed from `sources/`.** Step 1 deletes the document from the wiki,
with every page that only that document supported. A page that also cites another
document stays, is marked stale, and stops citing the removed one. That page can still hold text
that came from the removed document: a page does not record which sentence came
from which document. The README, «Limitations & non-goals», says how to remove
that text.

The same removal runs when you open the wiki, before you click anything. A
notice above the page then names each removed document.

**A file renamed in `sources/`.** The wiki knows a document by its path under
`sources/`. For the scan, a renamed file is a removed file and a new file. Step 1
deletes the document of the old name with its pages. Step 3 ingests the file under
the new name, with calls to the model. Rename a file only when the new name
is worth that cost.

**A file whose ingestion failed.** The scan tries it again at every run. A failure
often comes from the setup, not from the file: a missing Java runtime, or a
scanned PDF with no OCR engine. Correct the setup and scan again.

## Many files at once

The scan, like the upload form, ingests the files one after the other. Each file
calls the model to rewrite `overview.md`, and makes its own commit. Fifty new files
mean fifty rewrites of the overview.

`batch_ingest` (`base/domain/ingestion/batch.py`) ingests a list of files and
rewrites the overview once, at the end, with one commit for the whole list. No
button of the web interface calls `batch_ingest`. A programmer who loads a large
folder once can call `batch_ingest` from Python, as
`scripts/build_golden_corpus.py` does. The final review of the scan (step 5) does
not run then: open the **Maintain** tab and run lint and repair afterwards.
