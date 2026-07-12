# UAT — Vocabulario en la ingesta y portón de cobertura

Compañero del [GUIA_DEMO.md](GUIA_DEMO.md). Aquél prueba **lectura y chat** sobre
la wiki ya armada; éste prueba lo que ocurre **al cargar** (los apodos que se
generan y se vigilan) y el **portón de cobertura** que decide, antes de responder,
si un tema está cubierto o no. Es la prueba de aceptación de las piezas de
*vocabulario en la ingesta* (ver §4.5 de la guía).

> Igual que en el otro UAT: el chat lo **redacta un modelo**, así que las palabras
> cambian entre corridas. Lo que **no** debe cambiar es lo que marca cada
> criterio: qué se abstiene, qué se funda, y qué apodo entra o se descarta —eso lo
> decide el **código**, no el modelo.

---

## Puesta en marcha

1. Relanzá la app de lectura sobre la wiki de finanzas (`wikifintabs`).
2. **Encendé el checkbox «Pre-retrieval»** (arranca prendido porque el config del
   demo trae `enabled = true`). **El portón sólo actúa con pre-retrieval ON** — sin
   eso, los casos D, E y F no aplican.
3. Para mirar por dentro:
   - el artefacto de apodos: `WIKI_PATH/.llmwiki/aliases.generated.toml`;
   - el aviso de colisiones y el lint: el **log de ingesta** y el panel de **lint**
     en el ingest app;
   - el detalle turno a turno: `WIKI_CHAT_TRACE=1` antes de lanzar el chat.

---

## Casos

### A. Los apodos se generan solos al cargar

**Acción:** ingerí un documento **nuevo** que introduzca un instrumento con nombres
alternativos (por ejemplo, uno donde un concepto tenga sigla y nombre largo). *Los
conceptos viejos del demo se cargaron antes de esta función, así que hace falta una
carga nueva para verla.*

- **Qué prueba:** que la extracción entrega **nombre canónico limpio + apodos** y
  que el código los persiste, en vez de mantenerlos a mano.
- **Cómo lo resuelve:** `extract_structured` devuelve los apodos por concepto;
  `update_generated_aliases` los valida contra la cobertura y los escribe.
- **Criterio de aceptación:** después de la carga existe
  `.llmwiki/aliases.generated.toml` con una sección `[alias_datos]` y al menos una
  entrada `"<Concepto>" = ["<apodo>", …]`. Si el documento no tenía nombres
  alternativos, el archivo puede quedar vacío — no es un fallo.

---

### B. Un apodo que choca se descarta (y queda anotado)

**Acción:** ingerí un documento sobre un tema cuyo apodo natural ya es **el nombre
de otra cosa** que la wiki cubre (p. ej. un doc de CEDEARs, sabiendo que
`acciones` ya es una categoría de datos).

- **Qué prueba:** que un apodo que coincide con otro término cubierto **no ensucia**
  el mapa — se cae en la generación.
- **Cómo lo resuelve:** `validate_aliases` detecta la colisión, la descarta y la
  reporta; el pipeline la anota en el log.
- **Criterio de aceptación:** el apodo colisionante **no** aparece en el artefacto;
  si hubo colisión, el log de ingesta muestra «⚠️ N alias collision(s) dropped». (El
  modelo no siempre propone el apodo malo, así que el disparo determinista de esto
  es el caso C.)

---

### C. El linter vigila el vocabulario

**Acción:** agregá **a mano** en `wiki_config.toml` un choque, y corré «lint &
repair» en el ingest app:
```toml
[alias_datos]
"CEDEAR" = ["acciones"]     # acciones ya es una categoría de datos
```

- **Qué prueba:** que la deriva metida a mano (o por un dataset nuevo) se **caza
  como error**, aunque el generador ya no esté para filtrarla — y que el arreglo
  automático toca **sólo** lo que es de la máquina, nunca tu `wiki_config.toml`.
- **Cómo lo resuelve:** `vocabulary_check` cruza el mapa efectivo contra el padrón
  vivo (datasets + nombres de concepto). El auto-repair `repair_vocab_collision`
  **dropea el alias colisionante del artefacto generado**; pero como en este caso
  el choque lo metiste **a mano** en `wiki_config.toml`, la reparación lo **saltea**
  (no edita tu archivo — lo deja marcado para que lo resuelvas vos o lo mandes a
  `[falsos_sinonimos]`).
- **Criterio de aceptación:** aparece un hallazgo **`vocab_collision`** con
  severidad **error**; al reparar, ese choque a mano se reporta **`skipped`** con
  el motivo (override a mano). *Contraste:* un choque que viniera del **artefacto**
  (p. ej. un dataset nuevo que colisiona con un apodo ya generado) sí se **dropea
  solo** (`deleted`). Probá también las otras variantes: un apodo para un canónico
  que no existe → `vocab_stale` (warning); un término de `[fuera_de_alcance]` que
  hoy ya tiene página → `vocab_covered` (info).

---

### D. El portón funda un tema cubierto (antes rechazaba)

**Pregunta:** *"Si hago un plazo fijo, ¿le estoy ganando a la inflación?"* (y la de
acciones: *"¿Cuánto ganaría con acciones de YPF?"*).

- **Qué prueba:** que un tema **dentro del padrón** se responde con contexto
  inyectado, en vez de rechazarse porque el modelo «no buscó».
- **Cómo lo resuelve:** `in_roster` da verdadero → el código recupera del wiki e
  inyecta antes de responder.
- **Criterio de aceptación:** responde **fundamentado y citado** (inflación:
  nominal vs. real, remite a CER/UVA; acciones: «no estimable» y por qué). **No**
  debe salir la abstención genérica.

---

### E. El portón abstiene un tema NO cubierto — sin tocar los documentos crudos

**Pregunta:** algo que la wiki **no** cubre pero que algún documento roza al pasar.

- **Qué prueba:** que Tier-2 (los documentos crudos) **no se dispara** para temas no
  cubiertos → no hay forma de usar un fragmento tangencial como excusa (el *leak*).
- **Cómo lo resuelve:** `in_roster` da falso → ni se recuperan los documentos
  crudos; se abstiene sin invocar al modelo.
- **Criterio de aceptación:** se abstiene, y en el trace **no** figura recuperación
  de *source chunks* ni llamada al modelo.
  **Prueba fina (recomendada):** sacá `"cedear"` de `[fuera_de_alcance]` y volvé a
  preguntar *"¿Qué son los CEDEARs?"*. Debe **seguir absteniéndose** —ahora por el
  padrón, no por la lista negra— confirmando que el portón, y no sólo la lista a
  mano, es lo que corta el *leak*.

---

### F. Un apodo enruta al dato correcto

**Pregunta:** *"¿A cuánto está el billete verde?"* (apodo de dólar).

- **Qué prueba:** que un apodo —a mano o generado— hace que el portón reconozca el
  tema como cubierto y llegue al dato.
- **Cómo lo resuelve:** `mentions_known_data` matchea el apodo contra el mapa
  efectivo (generado ⊕ overrides).
- **Criterio de aceptación:** responde el **dólar con su valor y fecha**; no se
  abstiene por no reconocer «billete verde».

---

### G. El asesor determinista sigue intacto

**Pregunta:** *"Tengo $1.000.000 que no necesito por 3 meses. ¿Qué alternativas
tengo y cuánto ganaría?"*

- **Qué prueba:** que el portón **no rompió** el camino de herramientas
  (`query_dataset`, `estimar_alternativas`) — una consulta de datos/asesoría sigue
  yendo por las herramientas aunque no tenga página inyectada.
- **Cómo lo resuelve:** sin páginas ni docs, pero mencionando datos, el plan es
  «invocar con herramientas, sin contexto».
- **Criterio de aceptación:** sale la **tabla determinista** de siempre (misma #1
  que en el otro UAT: Credicoop 90d, TEA 41,84%, $90.000). Sin regresión.

---

### H. Los apodos de los *datos* también se generan (no sólo los de conceptos)

**Acción:** corré una ingesta **por lote** (el *scan* de `sources/`, no un solo
archivo suelto) sobre el wiki de finanzas. Mirá después
`.llmwiki/aliases.generated.toml` y el *sidecar*
`.llmwiki/dataset_aliases.fingerprint`.

- **Qué prueba:** que los términos de la **lista de datos** (`dólar`, `plazo fijo`,
  `money market`…) reciben apodos por una pasada propia, en vez de vivir sólo como
  override a mano en `wiki_config.toml`. Cierra la promesa "el vocabulario se
  genera" también del lado de los datos.
- **Cómo lo resuelve:** `regenerate_dataset_aliases` corre **una vez por lote**,
  propone apodos con `extract_dataset_aliases`, los valida contra el padrón (misma
  detección de colisión) y los escribe junto a los de conceptos.
- **Criterio de aceptación:** tras el *scan*, el artefacto suma entradas para
  términos de datos y aparece el *sidecar* `dataset_aliases.fingerprint`. **Prueba
  de la compuerta:** volvé a correr el *scan* sin tocar `datasets/` — la pasada LLM
  **no** se repite (no debe re-loggear «🔤 Generated aliases for N data term(s)»);
  sólo vuelve a dispararse si editás un `datasets/*.md`.

---

## Checklist de aceptación (resumen)

| # | Capacidad probada | Pasa si… |
|---|---|---|
| A | Apodos al ingerir | Tras cargar, `.llmwiki/aliases.generated.toml` trae entradas de apodos |
| B | Colisión descartada | El apodo que choca **no** entra; el log anota la colisión |
| C | Linter de vocabulario | Un choque a mano da **`vocab_collision` (error)** en el lint |
| D | Portón funda lo cubierto | Inflación/acciones **fundamentadas y citadas**, no rechazadas |
| E | Portón corta el *leak* | Tema no cubierto → **abstención sin tocar docs crudos** |
| F | Apodo → dato | «billete verde» llega al **dólar con valor y fecha** |
| G | Asesor intacto | La **tabla determinista** sale igual (sin regresión) |
| H | Apodos de datos generados | El *scan* llena apodos de datos + *sidecar*; no re-corre si `datasets/` no cambió |

Si las ocho pasan, el vocabulario se genera —conceptos **y** datos— y se vigila en
la carga, y el portón funda lo cubierto y se abstiene de lo que no —sin tocar los
documentos crudos.

---

## Apéndice técnico — qué pieza prueba cada caso

Rutas por **archivo y función** (sin línea; los nombres sobreviven a los refactors).
Ver el diseño completo en §4.5 de [GUIA_DEMO.md](GUIA_DEMO.md).

| Caso | Pieza | Dónde vive |
|---|---|---|
| A | Extracción emite apodos + persistencia | `ingestion/wiki_generator.py::extract_structured`; `ingestion/alias_generation.py::update_generated_aliases`; `chat/vocabulary.py::write_generated_aliases` |
| B | Detección de colisión en la generación | `chat/vocabulary.py::validate_aliases` (`Collision`); paso 8b de `ingestion/pipeline.py` |
| C | Guardia permanente del vocabulario + auto-repair | `lint/checks.py::vocabulary_check`; `repair/actions.py::repair_vocab_collision` (dropea del artefacto; saltea overrides a mano) vía `repair/runner.py` |
| D–F | Portón por cobertura (padrón) | `chat/preretrieval.py::pre_retrieval_answer` + `plan_retrieval` (parámetro `in_roster`); `chat/vocabulary.py::build_roster`; `tools/wiki_fs.py::concept_page_names` |
| F | Fusión generado ⊕ overrides − falsos-sinónimos | `chat/vocabulary.py::merge_aliases`; `chat/config.py::load_config` |
| G | Asesor determinista (regresión) | `finance_argentina/` — sin cambios; sólo se verifica que el portón no lo desvía |
| H | Pasada de apodos de datos + compuerta | `ingestion/wiki_generator.py::extract_dataset_aliases`; `ingestion/alias_generation.py::regenerate_dataset_aliases` (fingerprint sidecar); enganchada en `ingestion/pipeline.py::scan_and_ingest` |

**Cobertura automática.** Todo lo determinista de arriba está cubierto por tests
unitarios sin modelo ni navegador: `test_structured_extraction.py`,
`test_chat_vocabulary.py`, `test_alias_generation.py`, `test_lint_vocabulary.py`,
`test_chat_retrieval_plan.py`, `test_chat_pre_retrieval_answer.py`. Este UAT valida
lo que esos tests no pueden: el comportamiento **en vivo** de punta a punta.
