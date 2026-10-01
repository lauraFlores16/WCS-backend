# VALIDACIÓN EXTERNA DEL AUTÓMATA CELULAR — RURRENABAQUE 2023

Trabajo de grado: *Modelo de simulación predictiva de propagación de incendios
forestales mediante autómatas celulares y aprendizaje automático.*
Caso: Wildlife Conservation Society (WCS).

| | |
|---|---|
| **Caso de estudio principal** | Municipio de Apolo, provincia Franz Tamayo, La Paz |
| **Área de validación externa** | Municipio de Rurrenabaque, provincia General José Ballivián, **Beni** |
| **Objeto de la validación** | El autómata celular. **No** XGBoost. |
| **Año analizado** | 2023 |

---

## Estado real de cada fase

Solo la Fase 1 está ejecutada. Todo lo demás está preparado y **pendiente de
cálculo**: requiere Google Earth Engine, una clave de NASA FIRMS y el motor del
proyecto corriendo, y ninguna de las tres cosas se puede ejecutar desde aquí.
Ningún número de las fases 2 a 16 debe darse por bueno hasta que salga de una
ejecución real (§42).

| Fase | Qué es | Estado | Con qué se hace |
|---|---|---|---|
| 1 | Cargar y verificar Mun_RBQ | **Ejecutada y verificada** | `python/verificar_shapefile.py` + `gee/01_Cargar_ROI.js` |
| 2 | FIRMS Rurrenabaque 2023 | Pendiente — necesita clave FIRMS | `python/f2_descargar_firms_2023.py` |
| 3 | Periodo de mayor actividad | Pendiente — depende de la fase 2 | `python/f3_f4_analisis_y_evento.py` |
| 4 | Selección del evento | Pendiente | mismo script + `gee/04_Seleccion_Evento.js` |
| 5 | Focos iniciales | Pendiente | mismo script |
| 6 | Imágenes pre/post | Pendiente — necesita GEE | `gee/05_Sentinel_Landsat_PrePost.js` |
| 7 | NBR / dNBR | Pendiente — necesita GEE | `gee/06_NBR_dNBR.js` |
| 8 | Referencia observada + banda `valido` | Pendiente — necesita GEE | `gee/07_Mascara_Quemada.js` **(reescrito)** |
| 9-10 | Variables y grid compatible | Pendiente — necesita GEE | `gee/08_Exportar_Resultados.js` **(reescrito)** |
| 10b | **Verificación del grid** | **Herramienta lista y probada** | `python/f10_verificar_grid_rbq.py` **(nuevo)** |
| 11 | Ejecutar el CA congelado | **Herramienta lista y probada** · bloqueada por `p_base` | `python/f11_ejecutar_ca_rbq.py` **(nuevo)** |
| 12-14 | Métricas y figuras | **Herramienta lista y probada** | `python/f12_metricas_validacion.py` **(reescrito)** |
| 15 | Redacción | Se genera sola | `resultados/resumen_validacion.md` |
| 16 | Integración con SIPRO FIRE | No empezar aún | — |

---

## Actualización — evento definitivo y cierre de la cadena (30-09-2026)

**Evento de validación: E122** (2023-11-11 17:59 → 2023-11-15 18:24 UTC, 91 focos
VIIRS, 96 h, centro −14,5964 / −67,2153). Sustituye a E41.

Por qué: `f6` ahora mide la cicatriz MapBiomas **sobre la huella FIRMS de cada
evento** (no por radio alrededor del centro). E41, el de más focos, solo tiene
cicatriz en el 24 % de sus celdas y 1 celda observada: no es validable. E122 es
el único que cumple los tres criterios fijados antes de simular: acierto
FIRMS→cicatriz 0,98, 29-30 celdas observadas (7,25 km²) y contaminación por
incendios de otras fechas 0,07.

Correcciones:

| Script | Fallo | Arreglo |
|---|---|---|
| `f3_f4` | f7/f6 no podían filtrar por evento: ningún CSV traía la etiqueta | escribe `firms_eventos_etiquetados.csv`; el título usa `E122`, no el índice DBSCAN |
| `f6` | asociaba por radio desde el centro → todos los eventos caían en la mancha #579 | asociación por huella FIRMS, con acierto, celdas observadas y contaminación |
| `f7` | sin `--grid` cargaba el grid de Apolo → curva a 0 celdas | usa el grid de la zona y `rejilla.py` (sin Django); `p_base` sale del JSON congelado |
| `f8` | NDVI: NIR/rojo (10 m) recortados a la forma de SCL (20 m) → solo el cuadrante NO y máscara desplazada | las tres bandas en la misma rejilla de 20 m; ventana recortada a la tesela |
| `f8` | DEM: Open-Meteo en 429 | `--dem-fuente opentopodata` (SRTM 30 m, por defecto). Una sola fuente para todo el grid |
| `f11` | `focos_iniciales.csv` trae `fila`/`columna` vacías → NaN al arrancar | si están vacías, las asigna |
| `f12` | caía al dibujar focos sin celda | las asigna por posición |
| **nuevo** `f9d` | no había nada que produjera `observado_grid.csv` desde MapBiomas | referencia del evento con `observado` + `valido` |
| f10, f11, f9, f9b | ruta por defecto `rbq_grid_500m.csv` | `rurrenabaque_grid_500m.csv` |

Orden desde aquí (PowerShell, en `python/`):

```
python f8_construir_grid.py --zona rurrenabaque --desde 2023-11-11 --hasta 2023-11-15
python f10_verificar_grid_rbq.py
python f9d_observado_evento.py --evento E122
#  Calibrar Apolo (Monitoreo → Capa 4 → Calibrar constantes K) y después:
python congelar_parametros.py
python f13_validar_apolo.py
python f11_ejecutar_ca_rbq.py
python f12_metricas_validacion.py
python f7_curva_crecimiento.py --csv ..\datos\firms_rbq_2023.csv --evento E122
```

### Lo que bloquea la validación real, en orden

1. **Recalibrar Apolo con la vecindad corregida.** Es lo primero. La
   calibración que había (`p_base` 0,214) se hizo con 4 vecinos y ya no vale:
   con Moore el mismo conjunto de constantes quema unas 4,5 veces más.
   `POST /api/calibracion/ejecutar` con conexión a internet.
2. **`p_base` sin decidir.** `config/parametros_ca_congelados.json` lo tiene en
   `null` a propósito y `f11` se niega a arrancar. Se rellena con el resultado
   del punto 1.
3. **Clave de NASA FIRMS** para descargar 2023.
4. **Acceso a Google Earth Engine** para los bloques 05 a 08.

Nada de eso se puede resolver desde aquí. Todo lo demás —la maquinaria— está
escrito, ejecutado y verificado con datos sintéticos de solape conocido.

### Pruebas de la maquinaria

```
cd validacion_rurrenabaque/python
python pruebas_validacion.py
```

40 comprobaciones, todas pasan. Cubren las seis del §42: grid compatible,
8 vecinos de Moore, NoData excluido, métricas contra un ejemplo calculado a
mano, parámetros congelados en un solo archivo y semillas reproducibles.

---

## FASE 1 — Resultados verificados

Leídos directamente de `Mun_RBQ.shp` con `pyshp` / `pyproj` / `shapely`.
Reproducible con `python/verificar_shapefile.py`.

**Identificación.** El `.dbf` da código `080304`, municipio *Puerto Menor de
Rurrenabaque*, capital Rurrenabaque, provincia General José Ballivián,
departamento **Beni**, zona UTM 19.

Conviene fijarse en el departamento: Apolo está en La Paz y Rurrenabaque en
Beni. Son departamentos, provincias y contextos fisiográficos distintos, y eso
refuerza el argumento de validación *externa* — no es una zona contigua que
comparta clima y relieve con el área de calibración.

**Geometría.** Polígono simple, un anillo, 2.058 vértices, sin huecos,
topológicamente válido. CRS declarado WGS 1984 UTM zona 19S (EPSG:32719).

**Superficie.** Calculada por dos vías independientes:

| Método | Resultado |
|---|---|
| Planimétrico sobre UTM 19S | 2.519,84 km² |
| Geodésico sobre elipsoide WGS84 | 2.519,77 km² |
| Discrepancia | 0,07 km² (0,003 %) |

Que las dos coincidan confirma que el `.prj` dice la verdad sobre la
proyección. Si no coincidieran, el CRS declarado sería falso y todo lo que
viniera después estaría desplazado sin avisar.

**Ojo con el `sup_ha` del `.dbf`:** trae 250.000 ha, pero el área real de la
geometría es 251.983,6 ha. Es un valor nominal redondeado. En la tesis se usa
la calculada y se cita la diferencia; si se citara la del `.dbf`, el error
relativo de área de la validación arrastraría ese sesgo desde el primer paso.

**Encuadre.** Perímetro 300,85 km. Extensión 52,2 km E-O × 78,0 km N-S.
Centroide −14,688621 / −67,305708.
BBox WGS84: lon −67,559663 → −67,073719 · lat −15,046462 → −14,343298.
Cadena para NASA FIRMS: `-67.559663,-15.046462,-67.073719,-14.343298`.

**Grid.** Con el mismo paso de 0,0045° que usa el grid de Apolo del proyecto
(≈501 m N-S, ≈484 m E-O a esta latitud): rejilla envolvente de 157 × 109, de
las cuales **10.449 celdas tienen su centro dentro del municipio**. Apolo tiene
36.390, así que Rurrenabaque es un 29 % de su tamaño.

El paso se mantiene idéntico a propósito. Si el grid de la validación tuviera
otra resolución, las métricas de Rurrenabaque no serían comparables con las de
Apolo y la prueba perdería su sentido.

---

## Parámetros congelados del autómata

El §5 exige congelar los parámetros **antes** de tocar Rurrenabaque, y el §18
prohíbe reajustarlos después. Estos son los valores actuales, tomados de
`backend_django/api/motor/automata.py` → `CONSTANTES_POR_DEFECTO`:

```
Pendiente     K_PENDIENTE_ARRIBA 3.0   K_PENDIENTE_ABAJO 1.2   PENDIENTE_MAX 4.0
Viento        K_VIENTO 0.15            EXP_VIENTO 1.0
Temp/humedad  K_TEMPERATURA 0.01       K_HUMEDAD 0.5
Vegetación    NDVI_BARRERA 0.10        NDVI_BASE 0.5
Residencia    RESIDENCIA_MIN 3         RESIDENCIA_MAX 6    DECAIMIENTO_FASE 0.25
Spotting      SPOTTING_PROB 0.015      SPOTTING_VIENTO_MIN 1.0
              SPOTTING_DIST_MIN 2      SPOTTING_DIST_MAX 6
              SPOTTING_DISPERSION 0.35
Vecindad      RADIO_VECINDAD 1         EXP_DISTANCIA 1.0
Lluvia        K_LLUVIA_PROP 0.20       K_LLUVIA_EXT (ver el archivo)
```

**Falta un dato y es importante.** El `p_base` calibrado no está en el proyecto:
`almacen_datos/` viene vacío y `leer_calibracion()` devuelve `None`, con lo que
el motor cae al valor por defecto de 0,30. Antes de ejecutar la Fase 11 hay que
anotar aquí **el `p_base` que produjo la calibración de Apolo, con su fecha y su
F1**, y usar ese. Si se usa el 0,30 por defecto sin decirlo, la validación no
está probando el modelo calibrado sino otro.

**Las cuatro reglas no se tocan.** R1 ARDIENDO→QUEMADO · R2 INERTE→INERTE ·
R3 QUEMADO→QUEMADO · R4 SIN_QUEMAR con vecino ARDIENDO → P(propagación) →
transición probabilística. Lo único que cambia entre Apolo y Rurrenabaque son
los datos del territorio.

---

## Orden de ejecución

```
1.  Subir Mun_Apolo y Mun_RBQ como assets de GEE
    (arrastrar .shp .shx .dbf .prj JUNTOS — sin el .prj el polígono
     aterriza en el golfo de Guinea sin dar ningún error)
2.  gee/01_Cargar_ROI.js          → comprobar contra los números de arriba
3.  python/f2_descargar_firms_2023.py --clave TU_CLAVE
    (o por variable de entorno; la sintaxis cambia según la consola:
     PowerShell  $env:FIRMS_MAP_KEY = "TU_CLAVE"
     CMD         set FIRMS_MAP_KEY=TU_CLAVE
     bash/zsh    export FIRMS_MAP_KEY=TU_CLAVE)
4.  python/f3_f4_analisis_y_evento.py           → tabla mensual, evento, focos
    python/f3_f4_analisis_y_evento.py --sensibilidad
5.  gee/02, 03, 04                → cartografía y comprobación cruzada
6.  gee/05                        → elegir las dos escenas MIRÁNDOLAS
7.  gee/06, 07                    → dNBR y máscara observada en el grid
8.  gee/08                        → variables y grid de Rurrenabaque
9.  FASE 11 — ejecutar el CA (ver más abajo)
10. python/f12_metricas_validacion.py
```

---

## Fase 11 — cómo ejecutar el CA sin tocar el motor

`ejecutar_automata(grid, parametros, opciones)` recibe el grid como argumento.
No hay que modificar nada del motor: basta con cargar `rbq_grid_500m.csv` en
lugar del de Apolo y pasar los focos iniciales de `f5_focos_iniciales.csv` por
`focos_iniciales`, con `constantes` fijadas a los valores congelados.

Dos cosas que decidir **antes** de mirar ningún resultado, porque después ya no
se pueden tocar sin invalidar la prueba:

- **Número de repeticiones.** El autómata es estocástico: una sola corrida es
  una muestra. Con 30-50 repeticiones y semillas distintas sale una
  probabilidad de quema por celda, mucho más informativa que un binario.
- **Umbral de la simulación.** Con qué fracción de repeticiones se considera
  quemada una celda. `f12_metricas_validacion.py` usa 0,5 por defecto
  (`--umbral-sim`). Fíjalo ahora y documéntalo.

---

## Tabla de resultados (§27)

Se rellena con lo que devuelva `f13_metricas.json`. **Ningún valor debe
escribirse a mano.**

| Indicador | Resultado |
|---|---|
| Evento | Pendiente de cálculo |
| Área observada | Pendiente de cálculo |
| Área simulada | Pendiente de cálculo |
| Diferencia | Pendiente de cálculo |
| Error relativo | Pendiente de cálculo |
| IoU | Pendiente de cálculo |
| Precision | Pendiente de cálculo |
| Recall | Pendiente de cálculo |
| F1-score | Pendiente de cálculo |
| Accuracy complementaria | Pendiente de cálculo |
| Comportamiento | Pendiente de cálculo |

---

## Riesgos previsibles de esta zona

No son excusas anticipadas: son cosas que conviene tener identificadas para
reconocerlas si aparecen, en vez de descubrirlas al final.

**Nubes y humo.** Rurrenabaque está en el piedemonte amazónico. En temporada de
quemas hay convección casi diaria, y el humo espeso que Sentinel-2 no siempre
marca como nube oscurece precisamente el SWIR, que es la banda del NBR. Si
ninguna escena óptica sirve, la salida documentada es usar MCD64A1 (bloque 02)
como referencia observada principal y declararlo como limitación. Forzar un
dNBR sobre una escena con 60 % de nubes, no.

**Inundación.** El agua libre baja el NBR igual que lo baja el fuego. Un área
inundada entre las dos fechas produce un dNBR alto idéntico al de una quema. Por
eso los bloques 06 y 07 enmascaran el agua con MNDWI en las dos fechas. Es el
riesgo específico de este municipio y no lo tenía Apolo.

**Relieve.** Apolo es transición cordillera-Amazonía, con pendientes fuertes.
Rurrenabaque es sobre todo piedemonte y llanura. El término `f_pendiente` del
autómata pesará bastante menos aquí, y eso hay que tenerlo delante al
interpretar las métricas: parte de la diferencia con Apolo puede ser
fisiográfica, no del modelo.

**NDVI sin barreras.** En Apolo ya se documentó que el NDVI queda por encima de
0,10 en todas las celdas, así que `NDVI_BARRERA` nunca frena nada y el modelo
tiende a sobreestimar. Si en Rurrenabaque pasa lo mismo, el sesgo se repetirá y
hay que decirlo, no corregir el umbral.

**Multi-foco.** El autómata arranca de focos y propaga por vecindad. Si el
evento seleccionado resulta ser en realidad varias quemas agrícolas
simultáneas, el IoU saldrá bajo por un error de definición del evento, no por
un fallo del modelo. El bloque 04 existe justo para detectar eso antes de
gastar la ejecución.

---

## Lo que esta fase NO hace

- No reentrena ni evalúa XGBoost. La columna `prob_ignicion` del grid de
  Rurrenabaque va a **0** a propósito: el modelo se entrenó con Apolo y aplicarlo
  aquí sería extrapolarlo fuera de su dominio (§33). Los focos iniciales vienen
  de FIRMS, que es observación, no predicción.
- No toca la base de datos ni Supabase. Todo sale a CSV, GeoJSON, GeoTIFF y
  JSON (§34).
- No toca el frontend. La sección «Validación → Rurrenabaque 2023» se
  implementará **después** de que estas métricas existan (§35, §36).
- No menciona Roboré en ningún sitio.

---

## Estructura de archivos

```
validacion_rurrenabaque/
├── LEEME.md
├── datos/
│   ├── Mun_RBQ.shp/.shx/.dbf/.prj/.cpg    originales, sin modificar
│   ├── Mun_RBQ_wgs84.geojson              derivado (Fase 1)
│   └── rbq_roi.json                       constantes verificadas
├── gee/
│   ├── 01_Cargar_ROI.js
│   ├── 02_FIRMS_2023.js
│   ├── 03_Analisis_Mensual.js
│   ├── 04_Seleccion_Evento.js
│   ├── 05_Sentinel_Landsat_PrePost.js
│   ├── 06_NBR_dNBR.js
│   ├── 07_Mascara_Quemada.js
│   └── 08_Exportar_Resultados.js
├── python/
│   ├── verificar_shapefile.py             Fase 1 · ejecutado
│   ├── f2_descargar_firms_2023.py         Fase 2
│   ├── f3_f4_analisis_y_evento.py         Fases 3-5
│   └── f12_metricas_validacion.py         Fases 12-14
└── resultados/                            vacío hasta ejecutar
```

Los bloques 04, 05, 06, 07 y 08 tienen variables de fecha y de id de escena
**vacías a propósito**. Se abortan solos con un aviso si se ejecutan sin
rellenarlas. Poner fechas de ejemplo invitaría a usarlas sin comprobar, y el
§42 prohíbe inventar fechas.

---

## Hallazgos de esta fase (verificados sobre el código, no supuestos)

### 1. La convención espacial estaba al revés — CORREGIDO

Medido sobre `backend_django/api/datos/grid.csv`:

| | Apolo |
|---|---|
| fila 11 → lat −14,00037 · fila 233 → lat −14,99937 | **la fila crece hacia el SUR** |
| col 26 → lon −68,99822 · col 247 → lon −68,00372 | **la columna crece hacia el ESTE** |

El borrador anterior del bloque 08 generaba la fila creciendo hacia el norte.
Eso no habría dado ningún error: habría producido un grid con aspecto correcto
sobre el que el motor calcula la influencia del viento y de la pendiente **en
la dirección contraria**. `automata.py` línea 157:

```python
dot = ((dc / norma) * u + (-df / norma) * v) / vel
```

`u` es la componente este del viento y `v` la norte. El signo negativo de `df`
solo es correcto si `df` positivo significa ir hacia el sur.

`f10_verificar_grid_rbq.py` comprueba esto y **detiene el proceso** si no
cuadra. La suite incluye una prueba que le pasa un grid invertido a propósito
para confirmar que lo rechaza.

### 2. La variable `humedad` no es humedad relativa

Evidencia medida sobre el grid de Apolo:

| | |
|---|---|
| Rango | 0,2196 – 0,3995 · mediana 0,3763 |
| Valores distintos | 87 en 36.390 celdas |
| Bloques contiguos | hasta ~50 × 22 km con el mismo valor |
| `viento_u` | 97 valores distintos → misma resolución nativa |
| `ndvi` | 2.749 valores → fuente fina, distinta |

No puede ser humedad relativa del aire (en Apolo en seca ronda 0,60–0,75) ni
humedad del combustible fino (0,05–0,30, y varía a resolución de vegetación).
Encaja con **humedad volumétrica del suelo en m³/m³ de un reanálisis ERA5**: el
rango es el de suelo tropical y los bloques gruesos corresponden a la
resolución del reanálisis, la misma que comparte con el viento.

El bloque 08 usa ahora `volumetric_soil_water_layer_1` de ERA5-Land. **Es una
inferencia fuerte, no una lectura de la fuente.** Para cerrarla del todo hay
que abrir `variables_gee_v2.csv` o el script de GEE que generó el grid de
Apolo. Si resultase ser otra banda, se cambia la constante `BANDA_HUMEDAD` y se
vuelve a exportar: una línea.

### 3. El spotting invierte el signo norte-sur — NO corregido

`automata.py` líneas 665-670:

```python
dir_viento = math.atan2(-v, u)
sf = f + round(-math.sin(ang) * alcance)
sc = c + round( math.cos(ang) * alcance)
```

Comprobado numéricamente:

| Viento | Δfila resultante | Correcto |
|---|---|---|
| sopla al NORTE | +4 → SUR | **no** |
| sopla al SUR | −4 → NORTE | **no** |
| sopla al ESTE | +4 → ESTE | sí |
| sopla al OESTE | −4 → OESTE | sí |

El eje este-oeste está bien y el factor de viento de la propagación normal
también: el fallo afecta solo a los saltos de pavesas, en el eje norte-sur.

En Apolo estaba **dormido**: el viento del grid tiene máximo 0,52 m/s y
`SPOTTING_VIENTO_MIN` es 1,0 m/s, así que el spotting nunca se dispara. En
Rurrenabaque el viento puede superarlo, y entonces sí contaminaría el
resultado.

No se ha tocado: el §45 pide explicar primero, y cambiarlo alteraría el modelo.
`f11` cuenta los saltos y avisa en pantalla si alguno se disparó. Si ocurre,
hay que declararlo al reportar las métricas, y el análisis de sensibilidad
honesto es repetir con `SPOTTING_ACTIVO = false` y comparar — no quedarse con
el resultado que salga mejor.

### 4. NoData se contaba como «no quemado» — CORREGIDO

La versión anterior del bloque 07 exportaba `observadoGrid.unmask(0)`. Una
celda tapada por una nube pasaba a valer `observado = 0`, y si el autómata
propagaba por debajo se contaba como **falso positivo**: el modelo pagaba por
una nube. Al revés, una celda quemada bajo nube se contaba como TN e inflaba la
Accuracy.

Ahora el bloque 07 exporta una banda `valido` y `f12` evalúa únicamente las
celdas con `valido = 1`. Las demás se excluyen por completo: no son TP, ni FP,
ni FN, ni TN. La suite lo comprueba con un caso construido a propósito, donde
100 celdas sin dato con simulado = 1 dan **FP = 0**.

### 5. La vecindad era de von Neumann — CORREGIDA a Moore

El filtro de `_construir_vecindad` era de distancia euclídea y con radio 1
dejaba fuera las diagonales: 4 vecinos en vez de 8. Corregido a distancia de
Chebyshov, con una constante nueva `VECINDAD` que vale `"moore"`.

**Consecuencia medida sobre Apolo** (foco f177 c181, 24 pasos, 10 semillas):

| Vecindad | Celdas quemadas (media) |
|---|---|
| von Neumann (antes) | 109,7 |
| **Moore (ahora)** | **492,2 · ×4,49** |

Eso **invalida la calibración anterior** (`p_base` 0,214 de `MEJORAS.md`, hecha
con 4 vecinos) y las métricas de validación de Apolo. Antes de congelar nada
para Rurrenabaque hay que **recalibrar sobre Apolo con la vecindad corregida**.

Como orientación, no como sustituto: con Moore, `p_base` ≈ 0,14 da un área
parecida a la que daba `p_base` 0,30 con von Neumann.

---

## Cambios de esta fase: atacar la sobreestimación

Todo lo que sigue está medido, no estimado.

### 6. El autómata no tenía ningún mecanismo para apagarse

Es el hallazgo de fondo. Una celda deja de arder al agotar su tiempo de
residencia, pero el frente avanza indefinidamente. Medido sobre Apolo con
`p_base` 0,14, vecindad de Moore, sin capa de terreno ni clima:

| Horizonte | Pasos | Superficie quemada | % del municipio |
|---|---|---|---|
| 6 h | 24 | 27 km² | 0,3 % |
| 12 h | 48 | 136 km² | 1,5 % |
| 24 h | 96 | 720 km² | 7,9 % |
| 48 h | 192 | 3.557 km² | 39,1 % |
| 96 h | 384 | 8.282 km² | **91,0 %** |

Bajar `p_base` solo retrasa la inundación. Por eso la validación de Apolo que
había —un foco durante 86-91 días— daba una sobreestimación de 18×: no medía
el modelo, medía esto.

Lo que frena un incendio real y el modelo no estaba viendo son **tres canales
que ya existían en el código y nadie alimentaba**.

### 7. Ciclo diurno: clima horario real del evento

`serie_ambiental` estaba implementado y solo se alimentaba desde el
**pronóstico**, que no sirve para un evento de 2021 o 2023. Se añadió
`obtener_serie_ambiental_historica()` en `servicios/meteo.py`, que trae del
archivo ERA5 la temperatura, humedad relativa, VPD, lluvia y humedad del suelo
hora a hora de las fechas del evento.

Con eso, de noche la humedad sube y la propagación se frena sola, que es lo que
pasa de verdad. Y si llovió durante el evento, el canal de lluvia lo apaga.

`f11` (Rurrenabaque) y `f13` (Apolo) lo usan por defecto. `--sin-clima` lo
desactiva para medir cuánto aporta.

### 8. Caminos y quebradas: antes no hacían nada

`servicios/terreno.py` calculaba una resistencia por tipo —río 0,0 · agua 0,0 ·
quebrada 0,45 · camino 0,6 · roca desnuda 0,1— y acto seguido descartaba todo
lo que no bajara de 0,05. Solo ríos y agua llegaban al motor, como barrera
binaria. **Una carretera no frenaba el fuego ni un poco.**

Ahora la resistencia viaja entera al autómata y entra en la regla R4:

```
p = p_base · peso · factor_fase · f_pend · f_viento · f_veg · f_hum · f_lluvia · f_barrera
f_barrera = 1 − K_BARRERA · (1 − resistencia_destino)
```

Es la resistencia del **destino**, no del origen: lo que frena es la
discontinuidad de combustible que hay que atravesar para prender allí.

**El spotting no la aplica.** Las pavesas vuelan por encima de una carretera.
Es justamente el mecanismo por el que un cortafuegos falla con viento fuerte, y
quitarlo sobreestimaría la eficacia de los caminos. Las barreras duras (agua,
roca, NDVI) sí lo bloquean.

Verificado con resistencia 0,6 aplicada a todo el grid:

| `K_BARRERA` | `f_barrera` | Celdas quemadas |
|---|---|---|
| 0,00 | 1,00 | 1.407 |
| 0,25 | 0,90 | 1.210 |
| 0,50 | 0,80 | 1.004 |
| 0,75 | 0,70 | 801 |
| 1,00 | 0,60 | 605 |

Monotónicamente decreciente. Y con una capa realista (un río y una carretera),
las barreras duras solas ya bajan la superficie un 29 %.

### 9. La calibración corría en un mundo distinto del de la simulación

`views.py` llamaba a `calibrar()` **sin pasarle `elevacion` ni
`barreras_extra`**, aunque la función los acepta. El optimizador buscaba los
parámetros en un municipio sin relieve y sin ríos, y esos parámetros se
aplicaban luego a corridas que sí los tenían. La `p_base` resultante venía
inflada para compensar unas barreras que durante la calibración no existían.

Corregido: la calibración recibe ahora DEM, barreras duras y resistencia
parcial. **Sospecho que esto pesa más en la sobreestimación que cualquier
mecanismo nuevo.**

### 10. Dos genes más, y uno que estaba muerto

`NDVI_BARRERA` estaba fijo en 0,10 y el mínimo del grid de Apolo es exactamente
0,100: ninguna celda quedaba por debajo, así que el modelo **nunca** generaba
barreras por falta de combustible. Ahora es un gen calibrable en [0,10 · 0,30],
y que decidan los perímetros reales.

`K_BARRERA` también es gen, en [0 · 1]: cuánto frena de verdad una carretera de
tierra a escala de 500 m no se sabe a priori.

### 11. Validación de Apolo con el mismo método que Rurrenabaque

`python/f13_validar_apolo.py`. Aplica a Apolo exactamente el procedimiento de
Rurrenabaque: ventana corta del evento, todos los focos del primer día, N
repeticiones con semillas registradas, probabilidad de quema, umbral congelado,
mismo tamaño de celda y mismo tratamiento del NoData.

Sin eso, «está bien calibrado en Apolo y en Rurrenabaque» no se puede sostener:
los dos números no serían comparables.

**Limitación que hay que resolver:** por defecto usa el perímetro FIRMS
dilatado ±1 celda como referencia observada, porque no existe todavía el dNBR
de Apolo. FIRMS es un muestreo disperso de anomalías térmicas, no un polígono
de área quemada: **subestima la superficie real e infla la sobreestimación
aparente**. Para la memoria hace falta correr los bloques 05-07 de GEE también
sobre Apolo y usar `--referencia dnbr --observado ...`.
