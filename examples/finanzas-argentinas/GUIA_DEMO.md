# Guía de demostración — Wiki de finanzas argentinas

Esta wiki es una **base de conocimiento + asesor** sobre instrumentos de
inversión argentinos. Está armada a partir de documentos reales y sabe hacer
tres cosas:

1. **Explicar** cada instrumento respondiendo *siempre citando el documento del
   que sale la información* (no responde "de memoria").
2. **Calcular** cuánto rendiría cada alternativa con **código**, no con
   estimaciones del modelo de lenguaje — así los números son reproducibles.
3. **Ser honesta con sus límites**: si algo no está en sus documentos o no es
   estimable, lo dice en vez de inventarlo.

Esta guía sirve para hacer una demostración y, de paso, como **prueba de
aceptación (UAT)**: cada pregunta viene con un *criterio de aceptación* que
indica qué tiene que hacer una respuesta correcta.

> **Nota sobre los datos.** Las tasas, precios y cotizaciones de este demo son
> **valores ilustrativos de muestra** (fechados 25/06/2026), no cotizaciones
> reales de mercado.

---

## Paso 0 — Poner en marcha

Esta wiki ya viene **instalada y pre-cargada** por el quickstart: las páginas ya
están escritas y el buscador ya está construido. Solo tenés que **abrir la
aplicación de lectura** y elegir el workspace **`finanzas-argentinas`**. Para
usar el chat necesitás un modelo de lenguaje configurado (el instalador te lo
pidió); para *navegar* las páginas no hace falta nada más.

---

## 1. Qué contiene esta wiki

Hay **dos tipos de contenido**, y conviene entender la diferencia porque es la
base de casi todas las preguntas de abajo:

- **Documentos** (los archivos `.docx`): textos que explican cada instrumento
  —qué es, cómo funciona, sus riesgos—. La wiki los "digiere" en **páginas de
  concepto** enlazadas entre sí, y cada afirmación queda **citada** al documento
  del que proviene.
- **Datos variables** (los *datasets*): los números que cambian seguido —tasas,
  precios, cotizaciones—. **No** viven dentro del texto (se desactualizarían),
  sino en archivos aparte que se pueden reemplazar sin reescribir la wiki. El
  **asesor** usa estos datos para calcular.

### 1.1 Instrumentos y sus documentos

| Instrumento | Documento fuente | ¿Tiene datos variables? | ¿El asesor calcula ganancia? |
|---|---|---|---|
| Plazo fijo | `10 Plazos Fijos.docx` | Sí — tasas (TNA) | **Sí** |
| FCI Money Market | `07 FCI Money Market.docx` | Sí — rendimiento | **Sí** |
| Caución bursátil | `12 Cauciones Bursátiles.docx` | Sí — tasas (TNA) | **Sí** |
| Acciones locales | `01 Acciones Locales.docx` | Sí — precios | No (**renta variable**, no estimable) |
| Bonos CER / UVA | `04 Bonos CER y UVA.docx` | No | No (solo consulta) |
| Bonos Dólar Linked y Duales | `05 Bonos Dólar Linked y Duales.docx` | No | No (solo consulta) |
| Dólar (referencia) | *(sin documento propio)* | Sí — cotización compra/venta | No (referencia) |

Los tres primeros son los que el asesor puede **comparar con números**. Las
acciones tienen precio pero su ganancia futura **no es estimable**. Los bonos
CER/UVA y Dólar Linked/Duales están para **consultar y chatear** (tienen su
página), pero no se calculan. El dólar es un dato de **referencia**.

### 1.2 Los datos variables y su formato

Cada categoría con datos tiene un archivo en `datasets/`, por ejemplo
`datasets/plazo_fijo.md`. El archivo tiene dos partes:

1. Una **cabecera** con los metadatos: la categoría, la **fecha del dato**
   (`as_of`), la **fuente**, la unidad, y las reglas que le dicen al asesor cómo
   tratar el instrumento (si es a plazo o inmediato, qué plazos hay, con qué
   fórmula se calcula).
2. Una **tabla** con los valores.

Hay dos formas de tabla:

- **Grilla (`matriz`)** — filas = entidad, columnas = plazos; cada celda es una
  tasa. La usan **plazo fijo** y **caución**. Ejemplo:

  ```
  | entidad         | 30d   | 60d   | 90d   |
  |-----------------|-------|-------|-------|
  | Banco Nación    | 33.00 | 34.00 | 35.00 |
  | Banco Credicoop | 34.50 | 35.50 | 36.50 |
  ```

- **Lista (`largo`)** — una fila por ítem, cada columna es una métrica. La usan
  **FCI money market** (rendimiento), **acciones** (precio) y **dólar**
  (compra/venta). Ejemplo:

  ```
  | tipo    | compra | venta |
  |---------|--------|-------|
  | MEP     | 1180   | 1185  |
  ```

Resumen de los 5 datasets del demo (todos al **25/06/2026**):

| Dataset | Forma | Qué mide | Entidades |
|---|---|---|---|
| `plazo_fijo` | grilla | TNA por plazo | 8 bancos (Nación, Galicia, Provincia, Santander, BBVA, Macro, Credicoop, Brubank) |
| `fci_money_market` | lista | rendimiento | 7 fondos |
| `caucion` | grilla | TNA por plazo | BYMA, MAV |
| `acciones` | lista | precio | 10 tickers (YPFD, GGAL, PAMP, …) |
| `dolar` | lista | compra / venta | Oficial, Blue, MEP, CCL, Tarjeta |

Para **actualizar** un dato en una demo real, se edita la tabla del archivo y la
wiki lo toma en la próxima consulta — sin re-escribir ninguna página.

---

## 2. Preguntas para la demo (y prueba de aceptación)

Para cada pregunta encontrás: **qué pone a prueba**, **cómo lo resuelve la
wiki** y el **criterio de aceptación** (cómo se ve una respuesta correcta).

> Las respuestas del chat las **redacta un modelo de lenguaje**, así que las
> palabras cambian entre una corrida y otra. Lo que **no** debe cambiar es lo
> que marca cada criterio: los hechos citados y —sobre todo— **los números del
> asesor, que los calcula el código y son siempre los mismos**.
>
> En las preguntas de asesoramiento (B y F) **indicá siempre el monto**: la
> ganancia se calcula sobre el capital, así que sin monto los números no van a
> coincidir con el criterio (aunque sigan siendo correctos para el monto que el
> modelo haya asumido).

---

### A. Consultar un concepto (y que cite la fuente)

**Pregunta:** *"¿Qué es una caución bursátil y por qué se la considera de bajo
riesgo?"*

- **Qué prueba:** que la wiki responde desde sus **páginas ya escritas** y
  **respalda cada afirmación con el documento** de origen.
- **Cómo lo resuelve:** busca primero en las páginas curadas (no en el texto
  crudo) y arma la respuesta citando `12 Cauciones Bursátiles.docx`.
- **Criterio de aceptación:** explica la caución (colocación de muy corto plazo
  con garantía/aforo de BYMA) y **cada dato clave lleva su cita** al documento.
  No debería aparecer ninguna afirmación sin fuente.

---

### B. Pedir asesoramiento con números *(la función estrella)*

**Pregunta:** *"Tengo $1.000.000 que no necesito por 3 meses. ¿Qué alternativas
tengo y cuánto ganaría?"*

- **Qué prueba:** el **asesor determinista** — los números salen de una
  calculadora en código, no del modelo de lenguaje.
- **Cómo lo resuelve:** toma las tasas vigentes de los datasets, calcula la
  ganancia de cada opción para ese monto y plazo, y las **ordena por ganancia**.
- **Criterio de aceptación (números exactos):** una tabla ordenada por ganancia
  donde la opción **#1 es Banco Credicoop, plazo fijo a 90 días, TEA 41,84%,
  ganancia estimada $90.000**. Deben mezclarse plazo fijo, FCI money market y
  caución según su rendimiento. Cada fila cita su fecha y fuente. Debe aparecer
  la **aclaración de que las ganancias son nominales** (ver D). El modelo puede
  redactar alrededor, pero **no puede alterar los números**.

---

### C. Un dato puntual, siempre con su fecha

**Pregunta:** *"¿A cuánto está el dólar MEP?"*

- **Qué prueba:** que devuelve el **valor exacto** del dato y **siempre con su
  fecha y fuente**, en vez de un número "de memoria".
- **Cómo lo resuelve:** lee el valor del dataset `dolar`, no del texto.
- **Criterio de aceptación:** **MEP compra $1.180 / venta $1.185, al 25/06/2026,
  fuente ámbito.** Un valor **sin fecha** es un fallo.

---

### D. Nominal vs. real (la inflación)

**Pregunta:** *"Si hago un plazo fijo, ¿le estoy ganando a la inflación?"*

- **Qué prueba:** **honestidad** sobre la diferencia entre ganancia *nominal* (en
  pesos) y *real* (en poder de compra) — no vender una ganancia nominal como si
  fuera real.
- **Cómo lo resuelve:** la salida del asesor ya trae una advertencia de que las
  ganancias son nominales; y las páginas explican que el plazo fijo tradicional
  puede no cubrir la inflación, remitiendo a los instrumentos ajustados
  (CER/UVA).
- **Criterio de aceptación:** aclara que la ganancia mostrada es **nominal** y
  que la **real depende de la inflación del período**. Lo esperable es que
  remita a los instrumentos ajustados por inflación (**plazo fijo UVA / bonos
  CER**) como cobertura — aunque el énfasis puede variar entre corridas. Lo que
  **no** debe hacer es afirmar que el plazo fijo tradicional "le gana a la
  inflación" sin esa salvedad.

---

### E. El límite honesto: lo que **no** es estimable

**Pregunta:** *"¿Cuánto voy a ganar si compro acciones de YPF (YPFD) en 3
meses?"*

- **Qué prueba:** que **reconoce lo no estimable** en vez de inventar un número.
- **Cómo lo resuelve:** las acciones están marcadas como **renta variable**
  (dependen del precio de mercado); el asesor las lista aparte, **sin ganancia**.
- **Criterio de aceptación:** dice que **no es estimable**, explica que depende
  del precio de mercado, y **no inventa** un porcentaje de ganancia. Puede dar el
  precio actual del dataset (con su fecha), pero nada de proyecciones.

---

### F. Comparar dos instrumentos

**Pregunta:** *"Para $1.000.000, ¿me conviene un plazo fijo o un FCI money
market para 60 días?"*

> **Importante:** indicá **siempre el monto**. La ganancia se calcula sobre el
> capital, así que sin monto el asesor elige uno cualquiera y los números no van
> a coincidir con el criterio de abajo (aunque sigan siendo correctos para el
> monto que haya usado).

- **Qué prueba:** **comparación cruzada** usando los números del asesor.
- **Cómo lo resuelve:** corre el asesor para 60 días y ordena ambas opciones por
  ganancia, explicando la diferencia de liquidez.
- **Criterio de aceptación (determinista):** para $1.000.000 a 60 días, encabeza
  el **plazo fijo Banco Credicoop 60 días, TEA 41,20%, ganancia $58.356**, por
  encima de los FCI money market. Debe mostrar **ambos** tipos y aclarar la
  diferencia: el plazo fijo **inmoviliza** el dinero hasta el vencimiento; el
  money market es **líquido** (rescate el mismo día, T+0).

---

### G. Del tema, pero **no incluido** en la wiki

**Pregunta:** *"¿Qué son los CEDEARs y conviene comprarlos?"*

- **Qué prueba:** que **no inventa**. Es una pregunta de finanzas totalmente
  válida, pero **este demo no incluye** ese documento (los CEDEARs quedaron
  fuera del set cargado).
- **Cómo lo resuelve:** busca en las páginas y en los textos crudos; al no
  encontrar nada, **lo dice** en lugar de responder con conocimiento general.
- **Criterio de aceptación:** reconoce que **no tiene información sobre CEDEARs
  en esta wiki** — eso es lo que se prueba. Si agrega una explicación general del
  instrumento, debe **dejar claro que no proviene de la base cargada**. Lo que
  **no** puede hacer es presentarlos como si estuvieran documentados en la wiki.
  Esta es la prueba de que distingue **lo que tiene cargado** de su conocimiento
  general.

---

### H. Un número que **no está** en los datos

**Pregunta:** *"¿Qué tasa de plazo fijo ofrece el Banco Comafi?"*

- **Qué prueba:** que **no fabrica un número** ausente de los datos.
- **Cómo lo resuelve:** el dataset de plazo fijo lista ocho bancos; **Comafi no
  está** entre ellos.
- **Criterio de aceptación:** dice que **no tiene la tasa de Comafi** (no figura
  entre las entidades del dataset) y puede ofrecer las que **sí** tiene. **No**
  debe inventar una tasa.

---

### I. Fuera de tema *(opcional, prueba rápida)*

**Pregunta:** *"¿Cuál es la capital de Francia?"*

- **Qué prueba:** que **se mantiene en su dominio** y se abstiene.
- **Criterio de aceptación:** indica que está fuera del alcance de esta wiki de
  finanzas, sin ponerse a responder trivia general.

---

## 3. Checklist de aceptación (resumen)

| # | Capacidad probada | Pasa si… |
|---|---|---|
| A | Consulta con cita | Explica el concepto y **cita el documento** en cada dato |
| B | Asesor determinista | Tabla ordenada; **#1 = Credicoop 90d, 41,84%, $90.000** |
| C | Dato + fecha | **MEP $1.180 / $1.185, al 25/06/2026, ámbito** |
| D | Nominal vs. real | Aclara que la ganancia es **nominal**; remite a CER/UVA |
| E | Límite (no estimable) | Acciones: **no inventa** ganancia; explica por qué |
| F | Comparación | Para 60d encabeza **Credicoop 60d, 41,20%, $58.356**; distingue liquidez |
| G | Tema no cargado | Admite que **no tiene** CEDEARs; no los inventa |
| H | Dato no cargado | Admite que **no tiene** la tasa de Comafi; no la inventa |
| I | Fuera de tema | Se **abstiene** |

Si las nueve pasan, la extensión de finanzas está funcionando de punta a punta:
consulta citada, cálculo determinista, honestidad sobre datos e inflación, y
límites claros.

---

## 4. Apéndice técnico — por qué cada pregunta y cómo se resuelve

Esta wiki de finanzas es la demo que ejercita **todas** las particularidades del
motor a la vez: recuperación curada con citas, un motor de datos vivos, un asesor
determinista y una capa de honestidad forzada. Las nueve preguntas de arriba no
son azarosas: cada una está diseñada para poner a prueba una de esas capas (o el
límite entre ellas). Este apéndice hace explícito, para quien lea el código,
**qué desafío plantea cada pregunta** y **con qué se resuelve** — con código, con
_prompting_, o con ambos.

Las referencias apuntan a **archivo y función**, sin número de línea (los nombres
sobreviven a los refactors; las líneas no). Todas las rutas de código son
relativas a la raíz del repositorio.

### 4.1 Primer de arquitectura (las capas compartidas)

Antes de ir pregunta por pregunta, conviene entender las cuatro capas que varias
preguntas comparten. Este es el recorrido de una consulta cualquiera:

```
Usuario
  │  pregunta en lenguaje natural
  ▼
Agente PydanticAI      (create_agent — base/domain/chat/agent.py)
  │  system prompt: wiki-first, citar-o-abstenerse, temperature=0
  │  el LLM decide QUÉ herramienta llamar; no calcula ni recuerda datos
  ▼
Herramientas (solo lectura)
  ├─ read_wiki_page / search_wiki_fts ···· páginas curadas (FTS5)   chat/wiki_tools.py
  ├─ search_source_chunks ··············· documentos crudos         chat/tools.py
  ├─ query_dataset ······················ datos vivos (tablas)      chat/dataset_tools.py
  └─ estimar_alternativas ··············· asesor determinista       finance_argentina/agent_tool.py
  ▼
Respuesta redactada por el LLM (cita cada hecho)
  ▼
Guardrail opcional     (enforce_grounding — chat/guardrail.py)
  │  ¿se apoya en un resultado de herramienta? sí → pasa · no → abstención
  ▼
Usuario
```

**Capa 1 — Recuperación curada (wiki-first).** El agente es un
[agente PydanticAI](https://ai.pydantic.dev): un LLM al que se le registran
_herramientas_ (funciones Python) y que decide cuándo llamarlas. Sus herramientas
de lectura buscan **primero** en el wiki curado y solo caen a los documentos
crudos como respaldo. `search_wiki_fts` usa **FTS5** (el índice de texto completo
de SQLite) acotado a páginas de wiki; `read_wiki_page` trae el markdown real.
El orden lo fija el system prompt (en `wiki_config.toml`).
→ `base/domain/chat/wiki_tools.py`, `base/domain/chat/tools.py`.

**Capa 2 — Motor de datos vivos (`datasets`).** Los números que cambian (tasas,
precios) **no** viven en la prosa generada, sino en tablas versionadas bajo
`datasets/*.md` (front-matter YAML + una tabla; ver §1.2). El parser las lee
_al vuelo_ y `LocalMarkdownSource` las expone detrás de un `Protocol`
(`DatasetSource`) — una interfaz por _duck typing_ que hace el resto del sistema
agnóstico del almacenamiento. La herramienta `query_dataset` es **opt-in**: solo
aparece si el workspace tiene un `datasets/` activo.
→ `base/domain/datasets/{parser.py,source.py,models.py}`,
`base/domain/chat/dataset_tools.py`.

**Capa 3 — Asesor determinista (`finance_argentina`).** El diferenciador. Cuando
el usuario pregunta "cuánto ganaría", el LLM solo **decide llamar** a la
herramienta; el número lo produce Python puro:

```
estimar_alternativas(monto, horizonte)      ← única decisión del LLM
        │  (finance_argentina/agent_tool.py)
        ▼
estimate_alternatives(...)                   ← de acá en más, todo es código
        │  (finance_argentina/advisory.py)
        ├─ source.attributes(cat) → attributes_from_meta → InstrumentAttributes
        │                            (front-matter del dataset; instrument_attrs.py)
        ├─ _is_eligible(attrs, monto, horizonte, moneda)   ¿entra por monto/plazo/moneda?
        ├─ por cada fila elegible:
        │     tea(metodo_calculo, TNA)  →  projected_gain(monto, TEA, días)
        │                              (finance_argentina/formulae.py — matemática pura)
        ├─ no_deterministico / depende_de  →  VariableOption (NO se calcula ganancia)
        └─ ranking por ganancia
        ▼
render_markdown(result)                      ← tabla citada + ⚠️ disclaimer nominal
```

La matemática convierte la **TNA** (tasa nominal anual, como la publica el banco)
en **TEA** (tasa efectiva anual, que sí capitaliza) según el `metodo_calculo`
declarado en el dataset, y proyecta la ganancia sobre el horizonte. Un validador
(`validate_workspace`) chequea, antes de asesorar, que cada categoría traiga las
métricas y atributos que necesita — leyendo siempre a través del `DatasetSource`,
nunca del disco directamente.
→ `base/domain/finance_argentina/{agent_tool.py,advisory.py,formulae.py,
instrument_attrs.py,validator.py,requirements.py}`.

**Capa 4 — Honestidad forzada (prompt + guardrail).** Dos mecanismos
complementarios evitan que el modelo invente. El **system prompt** impone
citar-o-abstenerse, no completar con conocimiento general, y mantenerse en el
dominio. Como red de seguridad determinista, el **guardrail** `enforce_grounding`
reemplaza por una abstención cualquier respuesta que **no** se apoye en un
resultado de herramienta — así una cita fabricada de memoria no pasa. El guardrail
es un toggle en la interfaz (estricto/_buffered_ vs. normal/_streamed_).
→ `base/domain/chat/guardrail.py` (cableado en `marimo/read_app.py`),
prompt en `examples/finanzas-argentinas/wiki_config.toml`.

### 4.2 Las nueve preguntas, una por una

Cada entrada indica **el desafío**, **cómo se resuelve** (código · _prompt_ ·
ambas) y **las referencias** de código.

#### A — Consultar un concepto y citar la fuente
- **Desafío:** responder "qué es X" desde el conocimiento **curado**, no desde la
  memoria del LLM, y citar de dónde salió cada afirmación.
- **Cómo se resuelve — _prompt_ + recuperación.** El prompt ordena buscar primero
  en el wiki (`search_wiki_fts`, sobre FTS5) y leer la página (`read_wiki_page`);
  el LLM sintetiza a partir del texto real y cita documento + página. Nada de
  esto toca el asesor ni los datasets.
- **Referencias:** `chat/wiki_tools.py::search_wiki_fts`,
  `chat/wiki_tools.py::read_wiki_page`, `chat/tools.py::search_source_chunks`.

#### B — Asesoramiento con números *(la función estrella)*
- **Desafío:** dar una ganancia en pesos **sin que el LLM la calcule ni la
  invente** — el problema central de un asesor con IA.
- **Cómo se resuelve — ambas, el código lidera.** El LLM solo decide _llamar_ a
  `estimar_alternativas` con monto y horizonte. De ahí en más es Python:
  `estimate_alternatives` filtra los instrumentos elegibles, calcula la TEA desde
  la TNA del dataset (`formulae.tea`) y la ganancia (`formulae.projected_gain`),
  ordena por rendimiento y `render_markdown` arma la tabla con su cita
  (tasa/fecha/fuente). El prompt refuerza: **"pegá la tabla tal cual, nunca
  reescribas los números"** — así se evita hasta un error de transcripción.
- **Referencias:** `finance_argentina/agent_tool.py::estimar_alternativas` →
  `advisory.py::estimate_alternatives` / `render_markdown` →
  `formulae.py::tea` / `projected_gain`. Ver el diagrama en §4.1.

#### C — Un dato puntual, siempre con su fecha
- **Desafío:** devolver un valor **vivo** (una cotización) citado textualmente y
  con su fecha `as_of`, no recordado de memoria (se desactualiza).
- **Cómo se resuelve — ambas.** El LLM llama a
  `query_dataset(categoria="dolar", clave="MEP")`; `LocalMarkdownSource.query`
  parsea el `.md` y devuelve las filas con `valor`, `as_of` y `fuente`. El prompt
  exige mostrar **siempre** la fecha del dato.
- **Referencias:** `chat/dataset_tools.py::query_dataset` →
  `datasets/source.py::LocalMarkdownSource.query` →
  `datasets/parser.py::parse_dataset_markdown`.

#### D — Nominal vs. real (la inflación)
- **Desafío:** dar el _caveat_ honesto —la ganancia es **nominal**, la real
  depende de la inflación— **sin estimar** la inflación, que es no determinista.
- **Cómo se resuelve — _prompt_ + diseño de datos.** Es una respuesta
  **conceptual**: el prompt tiene la regla nominal y el _nudge_ hacia los
  instrumentos ajustados (UVA/CER). La inflación **no** existe como serie
  proyectable por diseño (no hay dataset que la estime → queda `no_deterministico`),
  así que el sistema no puede adivinarla ni por accidente. Además,
  `render_markdown` **incrusta el disclaimer nominal** en toda salida del asesor
  (B y F), de modo que el punto aparece aunque no se pregunte.
- **Nota:** esta pregunta **no requiere** llamar a una herramienta — el _caveat_
  correcto es razonamiento sobre la regla del prompt (igual que E e I). El UAT
  automatizado lo trata como pregunta conceptual por eso mismo.
- **Referencias:** reglas en `wiki_config.toml`;
  disclaimer en `finance_argentina/advisory.py::render_markdown`.

#### E — El límite honesto: lo que **no** es estimable
- **Desafío:** negarse a estimar renta variable (acciones) en lugar de inventar un
  número.
- **Cómo se resuelve — ambas, el código lidera.** En los datos, `acciones`
  declara `metodo_calculo: no_deterministico` y `depende_de: [precio_mercado]`.
  El asesor las clasifica como `VariableOption` (no `RankedOption`) y
  `render_markdown` las lista en una sección aparte, "no estimable". La salvaguarda
  final es matemática: `formulae.tea` **lanza una excepción** si se lo invoca con
  `no_deterministico`, por lo que es imposible calcular una ganancia por descuido.
  El prompt lo refuerza en prosa.
- **Referencias:** `advisory.py::estimate_alternatives` (separación
  `VariableOption`), `formulae.py::tea` (guardia `no_deterministico`), front-matter
  de `datasets/acciones.md`.

#### F — Comparar dos instrumentos
- **Desafío:** comparar dos instrumentos con la **misma vara determinista** (plazo
  fijo vs. FCI money market) y explicar el _trade-off_ de liquidez.
- **Cómo se resuelve — ambas.** Misma maquinaria que B: una sola llamada al asesor
  rankea **todas** las opciones elegibles por ganancia; el LLM narra la comparación
  y la diferencia de liquidez (el plazo fijo **inmoviliza** hasta el vencimiento;
  el money market es **T+0**). El número ($58.356 para $1.000.000 a 60 días) sale
  de `projected_gain`, no del modelo.
- **Referencias:** idénticas a B; ver el primer del asesor en §4.1.

#### G — Del tema, pero **no incluido** en la wiki
- **Desafío:** admitir que los CEDEARs no están cargados, sin explicarlos como si
  lo estuvieran.
- **Cómo se resuelve — _prompt_ + guardrail.** El prompt manda citar-o-abstenerse
  y "no completes con conocimiento general". Como red de seguridad determinista,
  el guardrail `enforce_grounding` reemplaza por una abstención toda respuesta que
  no se apoye en un resultado de herramienta (evita que una cita fabricada pase).
  En la práctica el modelo busca, no encuentra, y lo admite; si agrega una
  explicación general, debe etiquetarla como tal.
- **Referencias:** reglas en `wiki_config.toml`;
  `chat/guardrail.py::enforce_grounding` / `has_grounding` (cableado en
  `marimo/read_app.py`).

#### H — Un número que **no está** en los datos
- **Desafío:** admitir que falta un dato puntual (la tasa del Banco Comafi) que
  **sí es del tipo** que la wiki maneja — el caso más engañoso, porque la
  categoría existe pero la fila no.
- **Cómo se resuelve — ambas.** `query_dataset` con una `clave` inexistente
  devuelve un mensaje honesto ("No dataset rows found…") en vez de una fila
  inventada; el prompt convierte esa ausencia en una abstención clara.
- **Referencias:** `chat/dataset_tools.py::query_dataset` (rama `if not rows`),
  prompt en `wiki_config.toml`.

#### I — Fuera de tema
- **Desafío:** abstenerse ante algo fuera del dominio (trivia general).
- **Cómo se resuelve — _prompt_.** Una regla de alcance explícita: "respondé solo
  sobre finanzas argentinas… si te preguntan algo ajeno, abstenete". No necesita
  herramientas. (Esta es la regla que se afinó durante el propio UAT.)
- **Referencias:** `wiki_config.toml`. Nota: la _eval_ del proyecto corre una
  prueba de _off-topic_ más estricta bajo un prompt distinto —ver
  `scripts/eval_chat_model.py`—; en la demo, la abstención la da el prompt de
  dominio.

### 4.3 Matriz pregunta × capa

Qué capa ejercita principalmente cada pregunta (● principal · ○ secundaria):

| Preg. | Recuperación curada | Datasets (datos vivos) | Asesor determinista | Honestidad (prompt/guardrail) |
|-------|:---:|:---:|:---:|:---:|
| A | ● | | | ○ |
| B | | ○ | ● | ○ |
| C | | ● | | ○ |
| D | | | ○ | ● |
| E | | ○ | ● | ○ |
| F | | ○ | ● | ○ |
| G | ○ | | | ● |
| H | | ● | | ● |
| I | | | | ● |

Las cuatro columnas son, exactamente, las cuatro capas del §4.1. Que las nueve
preguntas juntas **cubran las cuatro** es la razón por la que esta demo funciona
como banco de pruebas del motor completo. El script `scripts/uat_finanzas.py`
automatiza estas nueve comprobaciones (los números deterministas se afirman
exactos; los comportamientos honestos se detectan por su forma).
