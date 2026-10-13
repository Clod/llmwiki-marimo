Write a wiki page for the following document.

Document metadata:
- Filename: {filename}
- File type: {file_type}
- Pages: {page_count}
- Parser: {parser}
- Ingested: {ingested_at}

Document content:
---
{content}
---

Produce a wiki page with exactly these sections in this order:

# {title}

**Source:** {filename} | **Type:** {file_type} | **Pages:** {page_count} | **Ingested:** {ingested_at}

## Summary
(2–4 paragraphs: what the document is about, its purpose, main conclusions)

## Key Topics
(bullet list of the main themes and subjects covered)

## Key Entities
(bullet list of notable people, organisations, places, and dates mentioned)

## Important Data & Figures
(bullet list of notable numbers, statistics, metrics, or conclusions)

## Source Information
- **File:** {filename}
- **Type:** {file_type}
- **Pages:** {page_count}
- **Ingested:** {ingested_at}
- **Parser:** {parser}

Output only the markdown. Do not add any preamble or explanation outside the page.