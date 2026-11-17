---
type: dataset
categoria: plazo_fijo
formato: matriz
clave: entidad
columnas_dim: plazo
metrica: TNA
unidad: "%"
as_of: 2026-06-25
fuente: bcra.gob.ar
# Advisory attributes (finance overlay) — instrument → math contract.
disponibilidad: a_plazo
plazos_dias: [30, 60, 90, 180, 365]
monto_minimo: 1000
moneda: ARS
metodo_calculo: interes_simple_vencimiento
metrica_tasa: TNA
---

<!-- Datos ilustrativos de muestra para el demo — no son cotizaciones reales de mercado. -->

| entidad          | 30d   | 60d   | 90d   | 180d  | 365d  |
|------------------|-------|-------|-------|-------|-------|
| Banco Nación     | 33.00 | 34.00 | 35.00 | 36.00 | 37.00 |
| Banco Galicia    | 32.50 | 33.50 | 34.50 | 35.50 | 36.50 |
| Banco Provincia  | 34.00 | 35.00 | 36.00 | 37.00 | 38.00 |
| Banco Santander  | 31.50 | 32.50 | 33.50 | 34.50 | 35.50 |
| BBVA Argentina   | 32.00 | 33.00 | 34.00 | 35.00 | 36.00 |
| Banco Macro      | 33.50 | 34.50 | 35.50 | 36.50 | 37.50 |
| Banco Credicoop  | 34.50 | 35.50 | 36.50 | 37.50 | 38.50 |
| Brubank          | 30.00 | 31.00 | 32.00 | 33.00 | 34.00 |
