# Preguntas frecuentes

> **Estado: borrador — trabajo en proceso.** Está versionado para poder revisarlo  
> y discutirlo, no porque esté terminado: **ningún índice ni README lo enlaza**, y  
> no debería enlazarse hasta cerrar el roadmap (vocabulario / grounding). Hasta  
> entonces, cualquier respuesta de acá puede cambiar de estado.  
> Igual que [de la idea al producto](de_la_idea_al_producto.md): cada afirmación  
> grande viene con su estado real; nada se pinta de terminado si no lo está.

**Qué es, en una frase:** una wiki que un modelo de lenguaje arma y mantiene solo,  
pero que **responde únicamente sobre lo que realmente cubre** y **nunca inventa los**  
**números** — pensada para temas que cambian seguido y necesitan cuentas, como  
finanzas.

Este documento es la puerta de acceso rápido: resuelve la duda puntual en veinte  
segundos. Para el pitch general está el [README](../README.md); para el argumento  
a fondo, [de la idea al producto](de_la_idea_al_producto.md).

---

## A — ¿No es solo un RAG / chatbot?

### 1. ¿En qué se diferencia de un chatbot al que le paso mis documentos?

Un chatbot con tus documentos (lo que se suele llamar *RAG*) busca fragmentos  
sueltos y deja que el modelo improvise una respuesta con lo que encontró. Acá el  
modelo primero **construye páginas curadas** —como una enciclopedia ordenada— y al  
responder se apoya en ellas y **cita de dónde salió cada cosa**. No es "buscar y  
contestar": es tener una fuente ordenada, responder desde ahí, y frenar cuando la  
información no alcanza.

### 2. ¿El modelo no inventa los números?

No: los números **no** los produce el modelo. Salen de tablas de datos que vos  
cargás, calculados por código; el modelo solo redacta el texto alrededor. Si un  
cálculo no se puede hacer con los datos disponibles, lo dice ("no estimable") en  
vez de inventarlo. En finanzas, esa es la diferencia entre una herramienta usable y  
una peligrosa.

### 3. ¿Y el texto? ¿Cómo sé que no "alucina" la explicación? 🏷️

Tres frenos: responde apoyándose en las páginas curadas (no en su memoria suelta),  
**cita la fuente** de cada respuesta, y para el material menos confiable compara lo  
que dijo contra el documento de origen y lo descarta si no coincide. 🏷️ *Hoy esa*  
*comparación es léxica (coincidencia de palabras); una verificación semántica más*  
*profunda está en el roadmap.* **No elimina el riesgo: lo acota y lo hace  
auditable**.

### 4. ¿Qué pasa si le pregunto algo que la wiki no cubre?

Se **abstiene**. Antes de responder chequea si el tema está en su "padrón" —lo que  
realmente cubre—; si no está, dice que no lo tiene en vez de improvisar, y ni  
siquiera mira los documentos crudos para zafar con un fragmento tangencial. Es lo  
opuesto al chatbot que siempre contesta algo, aunque sea con seguridad y  
equivocado.

---

## B — ¿No es solo la wiki de Karpathy?

### 5. ¿Qué propuso Karpathy y qué toma este proyecto de esa idea?

Andrej Karpathy planteó la idea de una wiki personal mantenida por un LLM: le pasás  
documentos y el modelo arma y actualiza las páginas. Este proyecto **parte de esa**  
**idea** —de hecho deriva de una implementación open-source, con crédito y licencia  
Apache-2.0— y la lleva hacia un producto resolviendo lo que la versión simple deja  
abierto.

### 6. ¿Qué agrega esta versión — por qué copiar la idea "tal cual" no alcanza? ↪

La idea simple, pegada a un agente genérico, tiene varios *puntos de fuga*: inventa  
datos, contesta lo que no sabe, la wiki se desactualiza sin que nadie lo note, el  
vocabulario se mantiene a mano. Esta versión sella cada uno con mecanismos concretos  
(números deterministas, portón de cobertura, un linter que vigila, vocabulario que  
se genera al cargar). ↪ *El mapa completo de fugas y cómo se sella cada una está en*  
*[de la idea al producto](de_la_idea_al_producto.md).*

---

## C — ¿Quién la mantiene y por qué confiar?

### 7. ¿Quién mantiene la wiki al día?

El propio LLM, en la carga: cada documento nuevo actualiza las páginas. Y un  
"linter" recorre la wiki buscando problemas —páginas huérfanas, datos  
desactualizados, vocabulario que chocó, páginas que quedaron más flacas que su  
fuente— y **repara solo los casos deterministas**. La idea es que la wiki se cuide  
sola lo más posible, con el humano revisando, no tipeando.

### 8. ¿La IA puede crear o cambiar páginas sola? ¿Eso no es riesgoso? 🏷️

Sí, puede: la ingesta escribe páginas de forma autónoma, y desde el chat se pueden  
guardar respuestas como páginas nuevas (esto último **siempre lo dispara una**  
**persona**). No es "solo lectura", y no lo vendemos como tal. 🏷️ *La seguridad no es*  
*una promesa vacía: todo queda en git —revisable y reversible— y las páginas son*  
*markdown que un humano lee y aprueba.* El control es **revisión + historial**, no  
"la IA no toca nada".

### 9. ¿Puedo auditar lo que dice y de dónde salió?

Sí. Cada respuesta cita su fuente, y toda la wiki son archivos markdown versionados  
en git: podés ver qué cambió, cuándo, y a partir de qué documento. No es una caja  
negra — es texto plano con historial.

---

## D — Alcance y práctica

### 10. ¿Por qué finanzas y por qué Argentina?

Porque es donde el enfoque **más se nota**: los datos cambian todo el tiempo  
(cotizaciones, tasas), hay que hacer cuentas, y un número inventado hace daño real.  
Argentina, porque es el contexto que mejor conozco y donde la necesidad —inflación,  
 tipos de dólar, instrumentos que mutan— es más aguda.

### 11. ¿Sirve para otros dominios además de finanzas? ↪

Sí: para cualquier tema con **datos que se actualizan seguido + necesidad de hacer**  
**cuentas o citar con precisión**. Salud y dosis, normativa que cambia, precios,  
logística, estadística deportiva. ↪ *El cierre de*  
*[de la idea al producto](de_la_idea_al_producto.md) desarrolla los casos.* Finanzas es el primero,  
no el único. ↪ *Un campo analizado en detalle, con ocho casos*
*y sus límites: [aplicaciones al agro](aplicaciones_agro.md).*

### 12. ¿Necesito internet? ¿Mis datos se suben a algún lado? 🏷️

La wiki vive **local**: markdown en git más una base local; tus documentos y páginas  
no van a ningún servidor del proyecto. 🏷️ *La salvedad honesta: para redactar y*  
*actualizar usa un modelo de lenguaje, y salvo que corras uno propio (self-hosted),*  
*ese texto viaja a la API del proveedor del modelo. Es "local-first", no "100%*  
*aislado".*

### 13. ¿Por qué una wiki (y notebooks marimo) y no una app cerrada?

Porque el valor está en el **conocimiento portable**, no en la interfaz. Las páginas  
son markdown en git: las leés, buscás, versionás y te las llevás sin depender de  
nadie. Y la interfaz son notebooks marimo, así que **la abrís y la reordenás**  
—columnas, celdas— sin tocar código de frontend. Una app cerrada te ata a su forma;  
esto no.

---

## Cierre

### 14. ¿Esto es un producto o un prototipo? 🏷️

Es un **proyecto abierto en desarrollo**, no un producto terminado. Varias piezas  
centrales ya funcionan (números deterministas, portón de cobertura, linter,  
vocabulario automático); otras están diseñadas y en camino. Se muestra con  
honestidad: cada afirmación grande trae su estado real, sin pintar de terminado lo  
que todavía no lo está.
