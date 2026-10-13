Extraé el conocimiento de este documento.

Documento: {filename} ({file_type}, {page_count} páginas)

Contenido:
---
{content}
---

Devolvé un JSON con exactamente esta estructura:
{{
  "document_summary": "Resumen del documento en 2-3 oraciones",
  "concepts": [
    {{
      "name": "Nombre canónico corto: un sintagma nominal, sin paréntesis ni comparaciones con vs",
      "category": "entity|instrument|theme",
      "insight": "El aporte nuevo y específico de este documento sobre este concepto",
      "aliases": ["otros nombres para exactamente la misma cosa", "abreviaturas, sinónimos"]
    }}
  ]
}}

Extraé de 2 a 5 conceptos. Incluí solo los conceptos que merecen una página propia del wiki. Usá una
lista vacía en "aliases" cuando el concepto no tenga otros nombres habituales.