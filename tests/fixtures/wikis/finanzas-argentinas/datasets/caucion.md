---
type: dataset
categoria: caucion
formato: matriz
clave: mercado
columnas_dim: plazo
metrica: TNA
unidad: "%"
as_of: 2026-06-25
fuente: byma.com.ar
# Advisory attributes (finance overlay) — instrument → math contract.
disponibilidad: a_plazo
plazos_dias: [1, 7, 14, 30]
moneda: ARS
metodo_calculo: interes_simple_vencimiento
metrica_tasa: TNA
---

<!-- Datos ilustrativos de muestra para el demo — no son cotizaciones reales de mercado. -->

| mercado | 1d    | 7d    | 14d   | 30d   |
|---------|-------|-------|-------|-------|
| BYMA    | 28.00 | 29.50 | 30.50 | 31.50 |
| MAV     | 27.50 | 29.00 | 30.00 | 31.00 |
