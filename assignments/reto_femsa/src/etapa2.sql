-- SQLite. Ejecutar después de modelar.py, con la base local como entrada.
DROP VIEW IF EXISTS etapa2_conciliacion;
DROP VIEW IF EXISTS etapa2_diaria;
CREATE VIEW etapa2_diaria AS
SELECT d.planta_id, d.fecha_id, d.mes, d.corriente, d.archivo, d.hoja,
       d.celda_volumen, d.valor_original, d.volumen_m3 * 1000.0 AS volumen_l,
       CASE WHEN d.volumen_m3 IS NULL THEN 'Sin lectura válida'
            WHEN d.volumen_m3 < 0 THEN 'Negativo'
            WHEN d.volumen_m3 = 0 THEN 'Cero' ELSE 'Positivo' END AS estado,
       f.fin_semana
FROM fact_lectura_diaria d JOIN dim_fecha f USING (fecha_id);

CREATE VIEW etapa2_conciliacion AS
WITH diario AS (
  SELECT planta_id, mes,
         SUM(CASE WHEN corriente = 'PTAR' THEN volumen_l END) AS ptar_l,
         SUM(CASE WHEN corriente = 'Riego' THEN volumen_l END) AS riego_l,
         COUNT(CASE WHEN corriente = 'PTAR' THEN volumen_l END) AS dias_validos,
         COUNT(CASE WHEN corriente = 'PTAR' THEN 1 END) AS dias_esperados
  FROM etapa2_diaria GROUP BY planta_id, mes
), pares AS (
  SELECT p.planta, d.*, r.reporting_L, r.celda AS celda_reporting,
         b.celda_total AS celda_total_bitacora, d.ptar_l - b.total_hoja_L AS delta_interno_l,
         d.ptar_l / NULLIF(r.reporting_L, 0) AS ratio
  FROM diario d JOIN dim_planta p USING (planta_id)
  LEFT JOIN fact_reporting_mensual r ON r.planta_id = d.planta_id AND r.mes = d.mes AND r.es_total = 0
  LEFT JOIN fact_bitacora_mensual b ON b.planta_id = d.planta_id AND b.mes = d.mes AND b.corriente = 'PTAR'
), factores(factor) AS (VALUES (0.001),(0.01),(0.1),(10),(100),(1000)),
escala AS (
  SELECT *, COALESCE((SELECT factor FROM factores WHERE ratio BETWEEN factor*0.8 AND factor*1.2),1) AS factor_candidato
  FROM pares
)
SELECT *, ABS(ptar_l - reporting_L) AS brecha_original_l,
       ABS(riego_l - reporting_L) AS brecha_riego_l,
       ABS(ptar_l + riego_l - reporting_L) AS brecha_ptar_riego_l,
       reporting_L * factor_candidato AS reporting_hipotesis_l,
       ABS(ptar_l - reporting_L * factor_candidato) AS brecha_residual_l,
       100.0 * ABS(ptar_l - reporting_L) / NULLIF(ABS(reporting_L), 0) AS pct_original,
       100.0 * ABS(ptar_l - reporting_L * factor_candidato)
             / NULLIF(ABS(reporting_L * factor_candidato), 0) AS pct_residual,
       100.0 * dias_validos / NULLIF(dias_esperados, 0) AS cobertura_pct
FROM escala;
