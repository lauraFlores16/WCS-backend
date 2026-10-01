# Resumen de la validación externa — Rurrenabaque

**Generado automáticamente por `f12_metricas_validacion.py`. No editar a mano:
se regenera en cada ejecución.**

## Evento

| | |
|---|---|
| Municipio | Rurrenabaque, provincia General José Ballivián, Beni |
| Evento | E122 |
| Periodo | 2023-11-11 a 2023-11-15 |
| Focos iniciales | 3 detecciones FIRMS |
| Criterio de selección | Elegido a mano (E122) tras revisar continuidad, concentración, duración y disponibilidad de imágenes pre/post. |
| Sensor principal | VIIRS_SNPP_SP |

## Fuentes

| Elemento | Fuente |
|---|---|
| Actividad térmica y focos iniciales | NASA FIRMS (VIIRS / MODIS, procesamiento estándar) |
| Superficie observada | dNBR sobre Sentinel-2 / Landsat, umbral USGS 0,27 |
| Variables del territorio | ERA5-Land, MODIS MOD13A1, SRTM |
| Modelo de propagación | Autómata celular de SIPRO FIRE, sin modificar |

FIRMS **no** representa directamente superficie quemada: detecta anomalías
térmicas. La superficie observada procede del dNBR, no del recuento de focos.

## Ejecución

| | |
|---|---|
| Repeticiones | 30 |
| Semillas | 1 a 30, registradas |
| Umbral de quema | 0.5 de las repeticiones |
| p_base congelado | 0.1659 |
| Parámetros congelados el | 2026-09-30 |
| Horizonte | 96.4 h (386 pasos de 15 min) |

Los parámetros se fijaron **antes** de ejecutar Rurrenabaque y no se
modificaron con sus resultados.

## Cobertura de la observación

| | |
|---|---|
| Celdas cruzadas | 10,429 |
| Excluidas por falta de dato | 580 (5.6 %) |
| Evaluadas | 9,849 |

Las celdas sin información satelital válida (nube, sombra, agua) se excluyeron
por completo del cálculo: no se contabilizaron como TP, FP, FN ni TN.

## Resultados

| Indicador | Resultado |
|---|---|
| Área observada | 7.25 km² |
| Área simulada | 1494.50 km² |
| Diferencia | +1487.25 km² |
| Error de área | 20513.79 % |
| **IoU** | **0.0042** |
| **Precision** | **0.0042** |
| **Recall** | **0.8621** |
| **F1-score** | **0.0083** |
| Accuracy (complementaria) | 0.3952 |
| Error angular | 40.17° |
| Distancia entre centroides | 12.705 km |
| **Comportamiento** | **SOBREESTIMACIÓN** |

Matriz de confusión espacial: TP 25 · FP 5,953 ·
FN 4 · TN 3,867.

## Interpretación

El autómata sobreestimó la superficie afectada en 1487.25 km² (20513.8 %). El Recall de 0.8621 frente a una Precision de 0.0042 indica que la simulación alcanzó buena parte de la superficie observada, pero incluyó además superficie que no resultó afectada.

El error angular de 40.2° es moderado: la dirección simulada se desvía de la observada de forma apreciable.

La Accuracy se presenta como métrica complementaria: 3,867 de las
9,849 celdas evaluadas (39.3 %) son verdaderos
negativos, de modo que un valor alto reflejaría sobre todo la extensión no
afectada del municipio y no la calidad de la representación de la propagación.

## Limitaciones

- La prueba evalúa la capacidad del autómata para reproducir el patrón espacial
  de un evento, no su exactitud predictiva operativa.
- La referencia observada depende del umbral de dNBR adoptado (0,27, criterio
  USGS). El bloque 07 publica la sensibilidad del área a ese umbral.
- El autómata es estocástico; los resultados corresponden a la agregación de
  30 repeticiones y no a una realización única.
- XGBoost no interviene en esta validación. La columna `prob_ignicion` del grid
  de Rurrenabaque se fijó en 0 y los focos iniciales proceden de observación
  satelital.
