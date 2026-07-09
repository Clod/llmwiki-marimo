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
