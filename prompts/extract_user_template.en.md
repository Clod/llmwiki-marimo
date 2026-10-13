Extract knowledge from this document.

Document: {filename} ({file_type}, {page_count} pages)

Content:
---
{content}
---

Return JSON with this exact structure:
{{
  "document_summary": "2-3 sentence summary of the document",
  "concepts": [
    {{
      "name": "Short canonical name — a noun phrase, no parentheses or vs-comparisons",
      "category": "entity|instrument|theme",
      "insight": "The specific new insight this document adds about this concept",
      "aliases": ["other names for the exact same thing", "abbreviations, synonyms"]
    }}
  ]
}}

Extract 2-5 concepts. Only include concepts worth a dedicated wiki page. Use an
empty list for "aliases" when the concept has no common alternate names.