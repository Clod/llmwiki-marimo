Extract knowledge from this document.

Document: {filename} ({file_type}, {page_count} pages)

Content:
---
{content}
---

Return JSON with this exact structure:
{{
  "document_summary": "2-3 sentence summary of the document",
  "document_topics": ["the subjects the whole document treats"],
  "concepts": [
    {{
      "name": "Short canonical name — a noun phrase, no parentheses or vs-comparisons",
      "category": "entity|instrument|theme",
      "description": "One sentence that says what the entity is, without referring to the document",
      "insight": "The specific new insight this document adds about this concept",
      "aliases": ["other names for the exact same thing", "abbreviations, synonyms"],
      "topics": ["the subjects this document treats about this concept"]
    }}
  ]
}}

Extract 2-5 concepts. Only include concepts worth a dedicated wiki page. Use an
empty list for "aliases" when the concept has no common alternate names. Give 2-5
topics per concept and for the document.