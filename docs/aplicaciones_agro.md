# Aplicaciones al agro

### Ocho casos, y qué aporta el sistema en cada uno

> **Qué es este documento.** Un análisis exploratorio, escrito el 2026-08-15. No
> describe nada construido: el único dominio con overlay implementado es
> finanzas (`examples/finanzas-argentinas`). Sirve para dimensionar un segundo
> dominio, no para anunciarlo.
>
> El argumento general de por qué el patrón se generaliza está en
> [`from_idea_to_product.md`](from_idea_to_product.md), sección *Beyond
> finance*. Este documento desarrolla un solo campo en detalle.

## Por qué el agro

El sistema tiene propiedades que un asistente genérico no tiene: prosa
compilada que se acumula entre documentos, datos con fecha y fuente declaradas,
cuentas hechas por código y nunca por el modelo, negativa explícita cuando algo
no es calculable, y cita obligatoria en cada respuesta.

El agro encaja por una razón estructural, no por afinidad temática: tiene mucho
de las dos mitades que el sistema separa. Un cuerpo grande de conocimiento
durable —agronomía, manejo, normativa— disperso en boletines, ensayos, manuales
y resoluciones; y números que cambian todo el tiempo, en decisiones donde
equivocarse cuesta dinero o expone legalmente.

Los casos están ordenados por cuánto aprovechan la arquitectura, no por cuánto
se usarían. Cada uno se describe en cuatro partes: qué va en la prosa, qué va
en los datos, qué calcula el código, y qué cambia respecto de cómo se hace hoy.

---

## 1. Cumplimiento y trazabilidad de aplicaciones

**Prosa.** Límites máximos de residuos por mercado de destino, períodos de
carencia por principio activo y cultivo, estado de registro de cada producto,
protocolos del comprador. Hoy eso vive en documentos de organismos y de acopios.

**Datos.** El registro de aplicaciones por lote —producto, dosis, fecha— y la
fecha de cosecha prevista.

**Cuenta.** Si la fecha de cosecha respeta el período de carencia de cada
aplicación hecha en ese lote, y si el principio activo está permitido en el
mercado al que va ese grano.

**Qué cambia.** Es el caso donde la arquitectura calza exactamente: la regla
está en un documento, la fecha está en un dato, y la verificación es una resta.
Hoy alguien cruza a mano un documento con un cuaderno, y el error aparece
cuando el comprador rechaza la carga o detecta residuo. Acá la respuesta llega
con la cita a la norma y al asiento del registro, de modo que es auditable ante
un tercero — que es exactamente lo que un cálculo de planilla no es. Y como la
negativa es una rama de código, un producto sin dato de carencia cargado
produce «no puedo verificarlo» en lugar de un número inventado, que en este
caso sería el peor resultado posible.

## 2. Margen bruto por lote y por cultivo

**Prosa.** Qué compone cada línea de costo, por qué se elige una rotación, qué
significa cada concepto comercial.

**Datos.** Precios de granos, precios de insumos, rindes históricos por lote,
tarifas de flete por distancia, derechos de exportación, tipo de cambio.

**Cuenta.** Margen bruto por hectárea, sensibilidad al precio y al rinde,
comparación entre cultivos para el mismo lote.

**Qué cambia.** Hoy esto vive en planillas que mezclan datos con supuestos,
envejecen sin avisar y nadie puede auditar. El aporte no es calcular más
rápido: es que cada número queda fechado y con fuente, y que la fórmula es
código, así que dos personas obtienen el mismo resultado. La prosa explica el
supuesto detrás de cada línea y enlaza a la página que lo justifica, con lo que
la planilla deja de depender de la memoria de quien la armó. El punto de fuga
que el proyecto ya cerró en finanzas —que el modelo no escriba cifras— es el
mismo acá: el margen sale de la aritmética sobre datos declarados, nunca de la
prosa.

## 3. Diagnóstico de suelo y prescripción de fertilización

**Prosa.** Interpretación de un análisis de suelo, curvas de respuesta de
ensayos regionales, criterios de reposición.

**Datos.** Análisis por lote y por año —fósforo, materia orgánica, pH,
nitratos—, historia de rindes, precio y riqueza de cada fertilizante.

**Cuenta.** Balance de nutrientes entre extracción y reposición, dosis según
una función de respuesta declarada, costo por kilo adicional de grano.

**Qué cambia.** Una recomendación agronómica es justo lo que un modelo de
lenguaje produce con seguridad y sin fundamento. Acá la dosis sale de una
fórmula declarada y de la página del ensayo del que se tomó, no de la prosa. El
valor mayor está en el caso negativo: si para ese suelo y esa zona no hay curva
de respuesta cargada, el sistema lo dice en lugar de dar un número plausible.
Esa distinción entre lo calculable y lo no calculable ya está construida y es
la que un agrónomo necesita que se respete.

## 4. Manejo de resistencia en plagas, malezas y enfermedades

**Prosa.** Biología de la plaga, umbrales de daño económico, modos de acción y
su clasificación, estrategia de rotación de modos de acción.

**Datos.** Monitoreos por lote y fecha, historial de aplicaciones con su modo
de acción.

**Cuenta.** Si se cruzó el umbral, cuántas aplicaciones consecutivas del mismo
modo de acción lleva ese lote, si el plan de la campaña respeta la rotación.

**Qué cambia.** El manejo de resistencia es contabilidad a lo largo de años y
sobre muchos lotes, y es la clase de registro que en la práctica no se sostiene
en planillas. Es también donde una wiki que compone tiene su mejor argumento:
la página de una maleza acumula lo que aportaron el boletín de este año, el
ensayo del anterior y el manual de hace cinco, enlazados entre sí, en lugar de
que cada documento quede aislado. Y la parte que decide —cuántas veces seguidas
se repitió un modo de acción— es un conteo, no una opinión.

## 5. Elección de genética: híbridos y variedades

**Prosa.** Grupos de madurez, tolerancias a enfermedades, comportamiento por
ambiente.

**Datos.** Resultados de redes de ensayos por localidad y por año, precios de
semilla.

**Cuenta.** Ranking por ambiente, estabilidad entre años, costo por rinde
esperado.

**Qué cambia.** La literatura de los semilleros es material comercial y las
redes de ensayos son medición. El sistema separa una de otra en el lugar donde
más se confunden: la afirmación queda en la prosa, con su fuente citada, y el
número queda en los datos, con su localidad y su año. La comparación entre
materiales deja de depender de qué folleto se leyó último.

## 6. Balance hídrico y decisión de riego

**Prosa.** Requerimientos del cultivo por etapa fenológica, capacidad de
retención según el suelo, coeficientes y de dónde salen.

**Datos.** Serie de lluvias, evapotranspiración, riego aplicado, tipo de suelo
por lote.

**Cuenta.** Balance acumulado, déficit a la fecha, lámina a reponer.

**Qué cambia.** Es aritmética sobre una serie, que es lo que el modelo hace
peor y el código mejor. El aporte específico sobre una planilla es que los
coeficientes usados están explicados en una página y citados a su origen, así
que cuando alguien discute el resultado se discute el supuesto en lugar de la
cuenta.

## 7. Ganadería: nutrición, stock y cargas

**Prosa.** Requerimientos por categoría animal, calidad de forraje, conceptos
de carga y receptividad.

**Datos.** Pesadas por categoría y fecha, análisis de forraje, precios de
alimentos.

**Cuenta.** Aumento diario, costo por kilo producido, costo de la ración, carga
por hectárea.

**Qué cambia.** Misma forma que los anteriores, con una ventaja adicional: la
pesada es un dato que se toma periódicamente y cuya serie es el activo. Fechar
cada valor y calcular sobre la serie convierte un cuaderno en algo consultable
en lenguaje natural sin perder la trazabilidad de cada número.

## 8. Costos de maquinaria y labores

**Prosa.** Cómo se costea una máquina, qué es amortización, criterios para
decidir entre propio y contratista.

**Datos.** Horas, combustible, reparaciones, valor de reposición, tarifas de
labores.

**Cuenta.** Costo por hectárea de cada labor, y comparación entre hacerla y
contratarla.

**Qué cambia.** Es el caso más modesto de la lista, pero alimenta al primero:
el costo por hectárea de cada labor es una entrada del margen bruto. Vale por
composición, no por sí solo.

---

## Dónde conviene no meterse, o meterse con cuidado

**Comercialización y cobertura.** Precios de futuros, bases, porcentaje
cubierto y punto de equilibrio son perfectamente calculables. Pero la frontera
entre informar y aconsejar se cruza rápido, y el proyecto ya tomó la decisión
de marcar lo no estimable y no dar consejo de inversión. En el agro haría falta
la misma disciplina: mostrar el cálculo y su fuente, nunca la recomendación.

**Prescripción con consecuencia legal.** Vale lo mismo que se anotó para
dosificación clínica: una dosis de fitosanitario mal informada tiene
responsable. El sistema debería presentar el valor calculado y la norma citada,
y dejar la firma en manos de quien corresponde.

**El límite real no es agronómico, es de datos.** Los datasets de este proyecto
son archivos markdown que alguien mantiene a mano. Precios diarios, lluvias y
monitoreos son series que en el agro se generan rápido y en volumen. Sin una
vía de carga —importación desde el sistema del acopio, desde una estación
meteorológica, desde la aplicación de monitoreo— los casos 2, 6 y 7 quedan
descritos y sin usar. Ese es el trabajo que habría que dimensionar antes que
cualquier otro.

**Una limitación de método.** Las redes de ensayos tienen heterogeneidad entre
años y ambientes que una fórmula simple no captura. El caso 5 corre riesgo de
producir un ranking con más precisión aparente que la que los datos sostienen.

---

## Qué haría falta para construir uno

El andamiaje de datos es el mismo que ya existe. Lo que cada dominio necesita
por encima es corto, y en el orden en que haría falta:

1. **Una vía de carga de datos** para las series que se generan en volumen. Es
   el bloqueante, no la agronomía.
2. **Un recetario de cuentas del dominio**, declarado como código, equivalente
   al overlay de finanzas. Las fórmulas de cada campo son pocas y conocidas.
3. **Las tres listas de vocabulario** —términos fuera de alcance, otros nombres
   de lo que sí se cubre, y pares que no son sinónimos— curadas por alguien del
   área. Un dominio acotado permite escribirlas con precisión, cosa que una
   wiki genérica no permite.
4. **Un corpus de prueba** con documentos reales, para medir antes de afirmar.
