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
| p_base congelado | 0.2376 |
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
| Área simulada | 9.50 km² |
| Diferencia | +2.25 km² |
| Error de área | 31.03 % |
| **IoU** | **0.3137** |
| **Precision** | **0.4211** |
| **Recall** | **0.5517** |
| **F1-score** | **0.4776** |
| Accuracy (complementaria) | 0.9964 |
| Error angular | 12.93° |
| Distancia entre centroides | 1.143 km |
| **Comportamiento** | **SOBREESTIMACIÓN** |

Matriz de confusión espacial: TP 16 · FP 22 ·
FN 13 · TN 9,798.

## Interpretación

El autómata sobreestimó la superficie afectada en 2.25 km² (31.0 %). El Recall de 0.5517 frente a una Precision de 0.4211 indica que la simulación alcanzó buena parte de la superficie observada, pero incluyó además superficie que no resultó afectada.

El error angular de 12.9° es reducido: la dirección principal de propagación simulada se corresponde con la observada, y la discrepancia está en la extensión, no en el rumbo.

La Accuracy se presenta como métrica complementaria: 9,798 de las
9,849 celdas evaluadas (99.5 %) son verdaderos
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
