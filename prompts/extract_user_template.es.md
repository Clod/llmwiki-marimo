Extraé el conocimiento de este documento.

Documento: {filename} ({file_type}, {page_count} páginas)

Contenido:
---
{content}
---

Devolvé un JSON con exactamente esta estructura:
{{
  "document_summary": "Resumen del documento en 2-3 oraciones",
  "document_topics": ["los asuntos que trata el documento entero"],
  "concepts": [
    {{
      "name": "Nombre canónico corto: un sintagma nominal, sin paréntesis ni comparaciones con vs",
      "category": "entity|instrument|theme",
      "description": "Una oración que dice qué es la entidad, sin referirse al documento",
      "insight": "El aporte nuevo y específico de este documento sobre este concepto",
      "aliases": ["otros nombres para exactamente la misma cosa", "abreviaturas, sinónimos"],
      "topics": ["los asuntos que este documento trata sobre este concepto"]
    }}
  ]
}}

Extraé de 2 a 5 conceptos. Incluí solo los conceptos que merecen una página propia del wiki. Usá una
lista vacía en "aliases" cuando el concepto no tenga otros nombres habituales. Dá de 2 a 5
temas por concepto y para el documento.