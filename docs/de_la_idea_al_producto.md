# De la idea al producto

### Los puntos de fuga de la wiki-LLM y cómo se sellan

> ⚠️ **Esta versión quedó atrasada.** La canónica es
> [`from_idea_to_product.md`](from_idea_to_product.md), en inglés, verificada
> contra el código el 2026-08-07. Este texto es de julio y varios de sus estados
> ya no son ciertos — en particular el punto 10 ("la configuración se pudre"),
> que figuraba como *diseñado, sin construir* y hoy está construido, y el punto
> 11, que habla de seis chequeos cuando son nueve. Se conserva hasta que se
> retraduzca desde el inglés; no lo enlaces como referencia.

> **Cómo leer este documento.** Cada punto describe *una forma en que la idea
> hace agua* cuando se la lleva a la práctica con un agente genérico, un ejemplo
> concreto, y cómo este proyecto lo sella. La línea gris de `Estado` es doble
> propósito: para el lector es una marca de honestidad (qué está hecho y qué
> falta); para nosotros es el **checklist**. El documento no se publica hasta que
> todos los puntos estén en verde.
>
> **Una aclaración de tono.** Esto **no** es una crítica a la idea de Karpathy. La
> idea es suya y es enorme. Lo que hace agua no es la idea: es lo que pasa cuando
> uno la copia-pega tal cual a un agente genérico —Claude Code, Codex, Cursor
> apuntando a una carpeta— y espera que "salga solo". La idea describe el camino
> feliz; este documento mapea los pozos del camino real.
>
> Todos los ejemplos son de **finanzas** —el primer campo donde lo estamos
> aplicando— pero al final se ve por qué sirve para mucho más.

---

## La idea, en una frase

La mayoría de las herramientas de "chateá con tus documentos" redescubren el
conocimiento desde cero en cada pregunta: buscan pedazos sueltos y arman una
respuesta que después se pierde en el chat. Nada se acumula.

La idea de Karpathy es distinta y brillante: en vez de re-buscar cada vez, un
modelo de lenguaje **construye y mantiene una wiki** —un conjunto de páginas
enlazadas— que se arma una sola vez y se mantiene al día. Las referencias
cruzadas ya están; las contradicciones ya están marcadas; la síntesis ya refleja
todo lo que leíste. Es la diferencia entre buscar en una pila de papeles y
consultar una enciclopedia que se escribe sola.

El problema es que la nota lo avisa: es un patrón, no una receta. Esta es la
lista de detalles que no salen solos, sin construirlos a propósito. Van uno
por uno.

---

### Al responder

---

## 1. Cuando no encuentra, inventa

`Estado: ✅ hecho` · *ancla: `base/domain/chat/guardrail.py`*

**La fuga.** Un agente genérico, cuando busca en la wiki y no encuentra nada, no
se queda callado: completa con lo que "sabe" de fábrica. Y lo dice con la misma
seguridad con la que te da un dato real.

**Ejemplo.** Le preguntás por la tasa de un banco que no está en tus datos. En vez
de decir "no lo tengo", te tira un número redondo y convincente —sacado de su
memoria, viejo o directamente inventado— y vos no tenés forma de darte cuenta.

**Cómo lo sella este proyecto.** En el modo que viene puesto de fábrica (el modo
estricto): después de que el modelo responde, un control automático revisa la
respuesta. Si el sistema **no encontró nada de nada**, la respuesta no pasa: se
reemplaza por una abstención honesta. Queda el caso más tramposo —encontró algo,
pero ese algo no respalda la respuesta—: ese es el punto que sigue.

---

## 2. El fundamento falso: una excusa para largar lo que sabe

`Estado: 🚧 parcial (funciona, falta validación en uso real)` · *ancla: `base/domain/chat/preretrieval.py::plan_retrieval`, `overlap.py`*

**La fuga.** Peor que inventar de la nada es inventar *con coartada*. Le preguntás
por un tema que no cubrís; el agente encuentra un fragmento que apenas **menciona
al pasar** ese tema, y lo usa de excusa para largar una explicación general. La
respuesta *parece* fundada —hasta cita algo— pero el fragmento no responde nada; es
un decorado.

**Ejemplo real.** Preguntamos, textual: *"¿Qué son los CEDEARs y conviene
comprarlos?"* a una wiki que **no tiene** una página de CEDEARs. El agente
encontró un documento sobre bonos que nombraba la palabra "CEDEARs" una vez, de
refilón, y con esa excusa se puso a explicar CEDEARs de memoria. Cita presente,
fundamento ausente.

**Cómo lo sella este proyecto.** Hoy, una **lista corta de temas que sabemos que
no cubrimos** frena la pregunta antes de tocar nada (esto vive en el flujo de
recuperación por código, que hoy viene apagado por defecto) —CEDEARs entró en
esa lista después de este caso—. Y para lo que igual llega desde un documento crudo (porque
el tema no está en la lista pero tampoco tiene página propia), un segundo control
compara la **respuesta contra la fuente** —el mismo control del punto 12, que mide
calidad—: si la respuesta no *sale* de verdad de ahí, no se muestra. El paso
siguiente —abstenerse si el tema no tiene página propia, sin depender de una
lista mantenida a mano— es el punto 10, "La propia configuración se pudre".

---

## 3. La conversación se degrada con el uso

`Estado: ✅ hecho (gestión de historial y post-procesado) · 🚧 parcial (recuperación forzada)` · *ancla: `base/domain/chat/history.py`, `postprocess.py`, `preretrieval.py`*

**La fuga.** Un agente genérico contesta bárbaro en las primeras preguntas y se va
arruinando a medida que la charla se alarga. Empieza a **imitar el formato** de sus
respuestas anteriores, deja de usar sus herramientas de búsqueda, y a los pocos
turnos contesta de memoria. No es que "se rompió": es que la conversación acumulada
lo arrastra.

**Ejemplo real.** En una prueba nuestra, las primeras respuestas citaban la fuente
y mostraban una tabla de comparación prolija. A los tres o cuatro turnos —misma
sesión— la misma consulta salía en prosa, sin tabla y sin cita. El modelo se
estaba copiando a sí mismo hacia abajo. Autocrítica: nuestro propio agregado de
la tabla grande al historial de cada turno agravaba el problema — por eso ahora
se compacta.

**Cómo lo sella este proyecto** (en el modo que viene puesto de fábrica). Tres
cosas: el sistema **recupera la información por su cuenta** antes de contestar,
así el modelo no puede "saltear" la búsqueda; se mantiene la conversación
**liviana** (se compactan las tablas viejas para que la charla no se vuelva
pesada para el modelo); y lo que tiene que estar garantizado —la cita, la
tabla— lo **agrega el programa**, no depende de que el modelo se acuerde de
hacerlo turno a turno.

---

## 4. Lo que descubrís charlando se evapora

`Estado: ✅ hecho` · *ancla: formulario "guardar al wiki" (human-in-the-loop), `marimo/read_app.py`*

**La fuga.** Con un agente genérico, una respuesta buena —una comparación que
pediste, una conexión que descubriste charlando— se pierde en el chat. La idea
original pide archivarla en la wiki, pero eso depende de que vos te acuerdes de
hacerlo a mano.

**Ejemplo.** Le pedís una comparación entre plazo fijo y caución —una colocación a
pocos días— que te queda muy buena, cerrás el chat, y esa respuesta desaparece
con la sesión.

**Cómo lo sella este proyecto.** Debajo de cada respuesta hay un botón para
guardarla al wiki: con tu OK, la convierte en una página permanente del wiki.
Tus exploraciones se acumulan igual que los documentos que cargás, en vez de
perderse en el historial del chat.

---

### El conocimiento en sí

---

## 5. Solo sabe "qué es", no "cuánto vale hoy" — y no hace cuentas (el diferencial)

`Estado: ✅ hecho` · *ancla: `base/domain/datasets/`, `base/domain/finance_argentina/`*

**La fuga.** La wiki de la idea original es una enciclopedia: nada mantiene
números al día ni hace cuentas por vos. Sabe explicar qué es un plazo fijo, pero
no sabe a cuánto está la tasa hoy —y si alguna vez la anotó, quedó vieja—. Peor:
aunque tuviera el número, un agente genérico **hace la cuenta él mismo**, y ahí
inventa precisión y se equivoca.

**Ejemplo.** "Tengo un millón que no toco por 60 días, ¿cuánto gano y qué me
conviene?" La enciclopedia no tiene el número de hoy ni sabe calcular el interés;
el agente genérico o te dice "depende" o te tira una cuenta hecha a ojo.

**Cómo lo sella este proyecto.** Se agrega un **segundo tipo de conocimiento** al
lado de las páginas: **datos que vos actualizás** y el sistema toma tal cual
—tasas, precios, estadísticas—, siempre con su fecha. Y las cuentas las hace un
**programa que no improvisa** —siempre la misma fórmula, nunca el modelo—. Lo que
no se puede calcular con certeza (acciones, inflación, dólar) se marca como **"no
estimable"** en vez de adivinar un número. Es el punto que separa este proyecto
de una enciclopedia común: de acá cuelga toda la sección "Más allá de finanzas".

---

## 6. "Leé el índice primero" no escala

`Estado: ✅ hecho` · *ancla: `base/domain/tools/search.py` (FTS), grafo de citas en SQLite*

**La fuga.** La idea propone que el agente lea un archivo índice para orientarse.
El agente genérico copiado y pegado arranca **sin buscador**: la nota original
avisa que a medida que la wiki crece vas a necesitar uno de verdad —hasta receta
una solución más completa— pero te lo deja como tarea tuya. Sin eso, con cientos
de páginas el índice se vuelve enorme, el agente se pierde en él, y termina
contestando desde una página incompleta sin que nadie note que se salteó la
buena.

**Ejemplo.** Tu wiki financiera creció a cientos de páginas. Preguntás por
cauciones —una colocación a pocos días—; el agente ojea un índice interminable,
agarra la primera página que suena parecido y te contesta desde ahí, ignorando
la página específica que sí tenía la respuesta.

**Cómo lo sella este proyecto.** Acá viene incluido de fábrica un **buscador de
verdad** sobre las páginas (no "leer el índice a ojo"), junto con el **registro
de qué página salió de qué fuente**, guardado de forma consultable. Honestidad:
hoy busca **por palabras, no por significado** — la parte de reconocer sinónimos
automáticamente es el punto 10, "La propia configuración se pudre".

---

## 7. La wiki te queda en dos idiomas mezclados

`Estado: ✅ hecho` · *ancla: `[wiki] language` en `wiki_config.toml`, `base/domain/i18n.py`*

**La fuga.** Con fuentes en inglés y preguntas en castellano, un agente genérico
arma un spanglish: páginas a medio traducir, títulos en un idioma y cuerpo en
otro, respuestas que saltan de un idioma al otro según qué fragmento citó.

**Ejemplo.** Cargás informes de mercado en inglés y preguntás en castellano; la
página que se genera queda con el título en español y el cuerpo calcado del
inglés original.

**Cómo lo sella este proyecto.** El idioma se fija **por wiki** (castellano o
inglés, y se puede sumar otro): todo —páginas, títulos y respuestas del chat—
sale en ese idioma sin importar en qué idioma estén las fuentes. Podés tener una
wiki en cada idioma al lado, cada una consistente puertas adentro.

---

### Al cargar los documentos

---

## 8. La carga no da lo mismo dos veces

`Estado: ✅ hecho` · *ancla: skip por hash (`ingestion/detector.py`), regresión de caracterización, `WIKI_TRACE=1`*

**La fuga.** "El agente lee la fuente y arma las páginas" suena bien hasta que lo
corrés dos veces. Sin disciplina, la segunda corrida **no da lo mismo** que la
primera: duplica páginas, cambia cosas al azar, y no tenés forma de saber qué
tocó ni por qué. Un sistema que no es reproducible no es confiable.

**Ejemplo.** Volvés a cargar el mismo PDF de tasas —porque no te acordabas si ya
lo habías cargado— y la wiki queda distinta: dos resúmenes parecidos, una página
pisada. ¿Cuál es la buena? No hay manera de saberlo.

**Cómo lo sella este proyecto.** Lo que no cambió **no se vuelve a procesar** (se
reconoce por su contenido, no por la fecha del archivo). La parte del motor que
no depende del modelo —cómo se trocea, cómo se indexa, cómo se arma cada página a
partir de un texto dado— está **congelada por una prueba** que avisa si una
corrida se desvía de lo esperado. Lo que sigue sin cerrar: si volvés a cargar un
archivo que **sí cambió**, esa corrida real vuelve a pasar por el modelo, y ahí no
hay garantía de que dé el mismo resultado palabra por palabra dos veces. Hay,
además, un registro opcional, paso a paso, de todo lo que hizo la carga, para
poder auditarla.

---

## 9. El documento no entra entero

`Estado: ✅ hecho (troceado) · ⬜ roadmap (escaneados/OCR)` · *ancla: `base/domain/ingestion/pdf_extract.py`, `chunker.py`*

**La fuga.** "El agente lee la fuente" funciona hasta que la fuente tiene 300
páginas (no entra de una) o es un escaneo (una imagen sin texto debajo).

**Ejemplo.** Un informe de mercado de 250 páginas, o un PDF escaneado de un banco.

**Cómo lo sella este proyecto.** Los documentos se parten en pedazos manejables
al cargarlos, así entran completos por largos que sean. Pendiente: los PDF
escaneados —sólo imagen, sin texto— todavía entran vacíos o con texto roto;
reconocer texto en imágenes (OCR) es roadmap.

---

## 10. La propia configuración se pudre

`Estado: ⬜ roadmap (diseñado, sin construir)` · *ancla: generación de vocabulario en la carga, §4.5 de la guía*

**La fuga.** Lo que hace que el agente sea disciplinado es un archivo de reglas y
convenciones que vos mantenés a mano. A medida que la wiki crece, ese archivo
**envejece**: los sinónimos, el alcance, las convenciones quedan viejos, y el
agente empieza a no reconocer cosas que sí cubrís — la misma limitación de
búsqueda por palabras del punto 6, pero también del lado de qué temas reconoce
el sistema como "cubiertos" antes de buscar (punto 2).

**Ejemplo.** Agregás instrumentos nuevos, pero la lista de sinónimos quedó vieja.
Alguien pregunta por el "billete verde" y el asistente no lo asocia con el dólar,
porque nadie actualizó esa equivalencia a mano.

**Cómo lo sella este proyecto.** En vez de mantener esas listas a mano, se van a
**generar durante la carga** —el modelo ya está leyendo cada documento, así que
sabe los nombres alternativos de cada cosa— y un chequeo automático va a vigilar
que esas listas no choquen ni queden obsoletas. Es también el paso que
reemplazaría la lista negra a mano del punto 2 por un chequeo de "¿tiene página
propia?" sin mantenimiento manual. (Es la pieza más nueva; diseñada, todavía sin
construir.)

---

### Al cuidarla

---

## 11. El mantenimiento es un favor, no una operación

`Estado: ✅ hecho (seis chequeos + auto-reparación) · ⬜ roadmap (detector de página floja, vigilancia del vocabulario)` · *ancla: `base/domain/lint/`, `base/domain/repair/`*

**La fuga.** La idea dice "cada tanto, pedile al modelo que revise la salud de la
wiki". Eso es un favor, no un mecanismo: depende de que te acuerdes de pedirlo, de
que el modelo lo haga bien esta vez, y de que además **arregle** algo. En la
práctica, la wiki acumula contradicciones y páginas sueltas y nadie las toca.

**Ejemplo.** Dos fuentes distintas dejaron dos tasas de referencia contradictorias
en dos páginas. Nadie corre el chequeo; la wiki convive con la contradicción y en
algún momento te contesta con la vieja.

**Cómo lo sella este proyecto.** El mantenimiento es una **operación de verdad**:
chequeos concretos (contradicciones, páginas viejas, páginas huérfanas, conceptos
sin página, enlaces faltantes, huecos de datos) que corren siempre igual y
**reparan solos** los casos seguros. En construcción: un detector de "página más
pobre que su fuente" —el que el punto 12 menciona sin resolver— y la vigilancia
del vocabulario del punto 10, para que no se pudra.

---

## 12. Nadie verifica ni mide la calidad

`Estado: ✅ hecho (evaluación, idoneidad) · 🚧 parcial (respuesta-vs-fuente)` · *ancla: `base/domain/eval/`, `base/domain/chat/overlap.py`*

**La fuga.** Aunque la wiki se escriba sola, **¿quién controla que lo que escribe
sea fiel a la fuente?** En la idea original, nadie. Una página puede resumir mal
un documento, y ese error se propaga a todas las respuestas que la usen, sin
ninguna señal de alarma. Tampoco hay forma de saber si el modelo que estás usando
está a la altura.

**Ejemplo.** Una página resume un informe y, al comprimir, cambia "hasta 30 días"
por "30 días". Chico, pero ahora todas las respuestas sobre plazos salen mal.

**Cómo lo sella este proyecto.** Lo que está hecho hoy: una **evaluación con
nota**, contra una vara fija —preguntas, respuestas, evidencia citada y páginas
comparadas contra el documento del que salieron— que sirve para medir la calidad
y hasta para comparar modelos; e incluye un chequeo de "¿este modelo da la
talla?" antes de confiarle la wiki. Lo que falta cerrar: comparar cada respuesta
contra la fuente que dice usar en el momento en que se genera —hoy ese control
vive dentro del flujo de recuperación por código (el punto 2), que **viene
apagado por defecto**—. La evaluación mide el error de "hasta 30 días" → "30
días" cuando la corrés; atraparlo en el momento en que se escribe la página misma
es el detector pendiente del punto 11, "El mantenimiento es un favor, no una
operación".

---

## 13. No hay botón de deshacer

`Estado: 🚧 parcial (cada wiki es git ✅; retroceso coordinado en diseño)` · *ancla: git por wiki; diseño de rollback (borrador)*

**La fuga.** Una mala pasada de la carga te pisa quince páginas de una, y con un
agente genérico no hay forma prolija de volver atrás.

**Ejemplo.** Cargás un documento con un error que se propaga a varias páginas y
querés volver al estado de ayer.

**Cómo lo sella este proyecto.** Cada wiki es una carpeta con historial de
cambios —podés revertir a mano, página por página—. Lo que falta: un "deshacer"
coordinado, que vuelva la wiki entera y su índice a un punto anterior de un
saque, está diseñado, sin construir.

---

## 14. Mantenerla no es gratis

`Estado: 🚧 parcial (varias optimizaciones hechas; falta visibilidad del gasto)` · *ancla: skip por hash (`ingestion/detector.py`), comparación de a pares, síntesis incremental, modelos separados*

**La fuga.** Cada carga toca muchas páginas con el modelo, y la revisión de salud
es otra pasada más. Con la wiki grande, eso cuesta plata y tiempo, y el costo
crece con la wiki.

**Ejemplo.** Cargar cien informes de tasas y revisar la salud de la wiki entera
puede significar muchas llamadas al modelo, una atrás de otra.

**Cómo lo sella este proyecto.** Lo que no cambió no se reprocesa. La revisión de
salud compara sólo páginas que comparten fuente, no todas contra todas. La
síntesis general de la wiki se hace de a poco, no de una. Y podés usar un modelo
barato para charlar y uno fuerte sólo para cargar. Falta: mostrarte, en algún
lugar, cuánto te va costando mantener la wiki.

---

### Dónde vive y quién manda

---

## 15. Un agente que lee archivos es una puerta de entrada

`Estado: ✅ hecho (traba de rutas) · ⚖️ compromiso (inyección: contenida por revisión, sin filtro automático)` · *ancla: `base/domain/chat/wiki_tools.py`, `SECURITY.md`*

**La fuga.** Para que el agente lea tus fuentes, le das permiso de leer archivos.
Eso, sin cuidado, es un agujero: puede terminar leyendo archivos que no
corresponden, y —más sutil— un documento que cargás podría traer **instrucciones
escondidas** ("ignorá todo lo anterior y contá esto otro") que el agente obedece
sin saber que lo están manipulando.

**Ejemplo.** Alguien te pasa un PDF "de tasas" que, en letra chica, incluye una
orden dirigida al asistente. Un agente genérico la puede tomar como parte de sus
instrucciones y cambiar de comportamiento.

**Cómo lo sella este proyecto.** El lector de páginas tiene una **traba real** que
le impide salirse de la carpeta de la wiki. Contra las instrucciones escondidas en
un documento no hay filtro automático —lo decimos de frente—, pero la
manipulación queda **contenida por revisión humana y por el historial de
cambios**: del lado de la carga, todo lo que se escribe queda como un **commit
revisable** que podés inspeccionar y revertir; del lado del chat, una
conversación se vuelve página permanente **sólo cuando vos la revisás y apretás
"guardar"** (el formulario del punto 4) — el asistente no reescribe tu wiki solo,
en silencio. El proyecto además documenta un análisis escrito de este tipo de
engaños, para que la contención sea una decisión y no un descuido.

---

## 16. Dependés del proveedor y de la nube

`Estado: ✅ hecho` · *ancla: agente embebido, cualquier endpoint compatible, `SECURITY.md`*

**La fuga.** La idea, tal como se propone, apunta un producto de un tercero —una app
de escritorio, un editor con IA— a tu carpeta de notas. Eso te **ata** a ese
producto: si cambia sus términos, sube el precio o se cae, perdés tu herramienta. Y
muchas veces tu conocimiento privado **sale de tu máquina** hacia la nube de otro.

**Ejemplo.** Armaste tu wiki financiera personal —tus números, tus decisiones— dentro
de una app que mañana cambia el plan gratuito o discontinúa la función. Tu wiki
queda rehén.

**Cómo lo sella este proyecto.** Trae su **propio asistente adentro** (un solo
programa autocontenido, sin depender de un editor externo) y funciona con
**cualquier** proveedor de modelo —incluido uno corriendo en tu propia máquina—.
Tu wiki, tu historial y tu índice viven en tu disco y no se suben a ningún lado;
lo único que viaja es el texto que va al modelo que **vos** elegís — y si elegís
uno corriendo en tu propia máquina, no sale nada de nada. Cada wiki es una
carpeta en tu disco con su propio historial de cambios.

---

### El costo honesto

---

## 17. Sin mapa visual ni extensiones

`Estado: ⚖️ compromiso inherente (lo asumimos a conciencia)`

**Dónde la idea original gana.** El planteo clásico usa Obsidian, que te da una
**vista de grafo** preciosa —ver la forma de la wiki, qué está conectado con qué— y
un ecosistema enorme de extensiones. Al traer nuestro propio asistente y ser
autocontenidos, **renunciamos** a eso.

**Ejemplo.** No vas a poder abrir la vista de grafo de Obsidian para explorar
visualmente tu wiki de un vistazo.

**Por qué lo elegimos igual.** Preferimos el control de punta a punta —fundamento
garantizado, datos que vos controlás, cuentas confiables, todo local— antes que la
vista bonita. Es un intercambio consciente, y lo decimos de frente: para quien el
grafo visual es lo primero, la idea original con Obsidian sigue siendo una gran
opción.

Y además, hoy no hace: **carga sin conversación guiada** —es un disparo
automático de punta a punta, no una charla con vos mientras carga—; **sin
búsqueda web** para llenar huecos que la wiki no cubre; **sin imágenes** —no mira
figuras ni fotos dentro de un documento—; y **sin salidas alternativas** —no arma
presentaciones ni gráficos automáticos—.

---

## Más allá de finanzas

Finanzas es el **primer** campo donde lo aplicamos, no el único. El diferencial de
fondo de este proyecto sobre la idea original es que suma **dos ingredientes** a la
enciclopedia de prosa: **datos que vos actualizás** y la capacidad de **hacer cuentas
confiables sobre ellos**, todo citado y fechado. Cualquier dominio donde vos vayas
acumulando entendimiento *y además* sigas números que cambian en el tiempo es un
candidato natural. Algunos:

- **Salud personal / análisis clínicos.** La wiki explica qué es la ferritina o el
  colesterol (prosa), *y además* guarda tus valores de laboratorio a lo largo del
  tiempo (dato que se actualiza) y te dice cuánto subió o bajó respecto del último y
  si está fuera de rango (cuenta), citando cada informe.

- **Nutrición y entrenamiento.** Conceptos sobre macronutrientes o progresión de
  cargas (prosa), *y además* tu peso, tus comidas y tus entrenamientos por fecha
  (datos), con el balance calórico o la progresión calculados sin adivinar.

- **Campo y commodities.** Páginas sobre manejo de un cultivo (prosa), *y además*
  precios de granos, clima y rindes que se actualizan (datos), con la rentabilidad
  por hectárea calculada al dato del día.

- **Servicios del hogar / energía.** Qué es la potencia contratada, cómo leer una
  factura (prosa), *y además* tus consumos mes a mes y las tarifas vigentes (datos),
  con el costo proyectado calculado, no estimado a ojo.

- **Análisis de empresas** (el análisis previo a poner plata en una empresa). La
  tesis sobre una compañía o un sector (prosa), *y además* métricas que cambian
  —facturación, empleados, rondas de inversión— fechadas (datos), con crecimientos
  y múltiplos calculados y con fuente.

- **Investigación con datos.** El estado del arte de un tema, leído en decenas de
  estudios publicados (prosa), *y además* los conjuntos de datos experimentales
  (números), con los cálculos estadísticos hechos por programa y trazables a su
  origen.

El andamiaje de datos es el mismo en todos los casos, pero a cada campo hay que
escribirle su propio recetario de cuentas —corto, porque las fórmulas de cada
dominio son pocas y conocidas—.

El patrón se repite: **una parte que se entiende una vez y perdura, y una parte que
cambia y hay que seguir**. La idea original resuelve la primera. Este proyecto suma
la segunda —y se asegura de que ninguna de las dos invente.

---

## Estado y orden de construcción (checklist)

| # | Punto de fuga | Estado |
|---|---------------|--------|
| 1 | Cuando no encuentra, inventa | ✅ hecho |
| 2 | El fundamento falso (la excusa tangencial) | 🚧 parcial (funciona, falta validación en uso real) |
| 3 | La conversación se degrada con el uso | ✅ / 🚧 (recuperación forzada) |
| 4 | Lo que descubrís charlando se evapora | ✅ hecho |
| 5 | Solo sabe "qué es", no "cuánto vale hoy" — y no hace cuentas (el diferencial) | ✅ hecho |
| 6 | "Leé el índice primero" no escala | ✅ hecho |
| 7 | La wiki te queda en dos idiomas mezclados | ✅ hecho |
| 8 | La carga no da lo mismo dos veces | ✅ hecho |
| 9 | El documento no entra entero | ✅ (troceado) / ⬜ (OCR) |
| 10 | La propia configuración se pudre | ⬜ diseñado, sin construir |
| 11 | El mantenimiento es un favor, no una operación | ✅ / ⬜ (detector de página floja, vocabulario) |
| 12 | Nadie verifica ni mide la calidad | ✅ (evaluación, idoneidad) / 🚧 (respuesta-vs-fuente) |
| 13 | No hay botón de deshacer | 🚧 parcial (git por wiki ✅; retroceso coordinado en diseño) |
| 14 | Mantenerla no es gratis | 🚧 parcial (optimizado; falta visibilidad de costo) |
| 15 | Un agente que lee archivos es una puerta de entrada | ✅ (traba de rutas) / ⚖️ (inyección: contenida por revisión) |
| 16 | Dependés del proveedor y de la nube | ✅ hecho |
| 17 | Sin mapa visual ni extensiones | ⚖️ compromiso asumido |

**El documento se publica cuando la columna Estado esté toda en ✅** (salvo los
compromisos asumidos a conciencia —la parte de inyección del punto 15 y todo el
punto 17—, que no son deudas). Lo que falta cerrar, en orden:
validar en vivo la recuperación por código —afecta los puntos 2 y 3, y la parte
respuesta-vs-fuente del 12—; construir la generación y vigilancia del vocabulario
en la carga (10); y cerrar los detectores, el rollback coordinado y la
visibilidad de costo (13, 14, y el detector de página floja del 11). El
reconocimiento de texto en imágenes (OCR) del punto 9 queda como pendiente
diferido, no bloqueante.

---

*Este documento es el complemento argumentativo de la matriz de alineación con la
idea de Karpathy que ya vive en la documentación: aquélla grada punto por punto qué
está hecho; éste explica, en criollo, por qué cada punto importa y qué se rompe sin
él.*
