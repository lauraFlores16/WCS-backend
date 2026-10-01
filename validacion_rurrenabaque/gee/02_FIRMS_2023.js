/**
 * ==========================================================================
 * BLOQUE 02 — ACTIVIDAD TÉRMICA FIRMS 2023
 * ==========================================================================
 * Script GEE: 02_FIRMS_2023
 * Fase: 2 (parte cartográfica)
 *
 * REPARTO DE TRABAJO — leer esto antes de tocar nada
 *   El conteo mensual y la identificación del evento NO se hacen aquí. Se
 *   hacen en python/f2_descargar_firms_2023.py y f3_f4_analisis_y_evento.py.
 *
 *   El motivo es una limitación real de GEE, no una preferencia: la
 *   colección `FIRMS` de GEE es un RÁSTER diario de 1 km con la banda T21
 *   (temperatura de brillo). No trae hora de adquisición, ni FRP, ni sensor
 *   por detección. Sin hora no hay secuencia temporal, y sin secuencia
 *   temporal no se pueden determinar los focos iniciales (§15) ni la
 *   evolución del evento (§11). El FRP lo pide explícitamente el §9.
 *
 *   Esos atributos solo existen en el archivo tabular de FIRMS, que es lo
 *   que descarga el script de Python.
 *
 *   Además: la colección FIRMS de GEE es de 1 km, DOS VECES más gruesa que
 *   la celda de 500 m del autómata. Usarla para delimitar superficie sería
 *   un error de escala. Para eso están el dNBR (bloques 06-07) y, como
 *   contraste, MCD64A1 a 500 m.
 *
 * QUÉ HACE ESTE BLOQUE
 *   · MAPA 2 — todas las detecciones FIRMS de 2023 sobre el municipio
 *   · MAPA 3 — mosaico de concentración mensual
 *   · contraste independiente con MODIS MCD64A1 (área quemada, 500 m)
 *
 * POR QUÉ SE AÑADE MCD64A1
 *   Es un producto de ÁREA QUEMADA, no de anomalía térmica: detecta el
 *   cambio de reflectancia del suelo tras el fuego y trae la fecha de quema
 *   por píxel. Sirve para dos cosas:
 *     1. confirmar que el periodo crítico que salga de FIRMS es el mismo;
 *     2. dar una segunda referencia observada, independiente del dNBR, con
 *        la que contrastar la cicatriz del bloque 07.
 *   Su resolución nominal (463 m) es casi la celda del autómata, así que la
 *   comparación es directa.
 * ==========================================================================
 */

var RUTA = 'projects/fire-prediction-apolo/assets/';
var ROI  = ee.FeatureCollection(RUTA + 'Mun_RBQ').geometry(1);
var ANIO = 2023;

var inicio = ee.Date.fromYMD(ANIO, 1, 1);
var fin    = ee.Date.fromYMD(ANIO + 1, 1, 1);

// --- 1. FIRMS: máximo anual de temperatura de brillo -----------------------
var firms = ee.ImageCollection('FIRMS').filterDate(inicio, fin).filterBounds(ROI);
print('Imágenes diarias FIRMS con datos en 2023:', firms.size());

// `max()` sobre el año: cada píxel se queda con la temperatura más alta que
// alcanzó. Es un mapa de «dónde hubo fuego», no de «cuánto».
var t21Max = firms.select('T21').max().clip(ROI);

Map.centerObject(ROI, 9);
Map.setOptions('SATELLITE');
Map.addLayer(ee.Image().byte().paint(ee.FeatureCollection([ee.Feature(ROI)]), 1, 2),
             {palette: ['#00E5FF']}, 'Límite Rurrenabaque');
Map.addLayer(t21Max, {min: 325, max: 400, palette: ['#FFF176', '#FB8C00', '#B71C1C']},
             'MAPA 2 · FIRMS T21 máx. 2023');

// --- 2. Concentración mensual ---------------------------------------------
// Cuenta, por píxel, en cuántos días de cada mes hubo detección. Es el
// equivalente ráster de la tabla mensual que produce el script de Python;
// las dos deben contar la misma historia. Si no coinciden, algo falla en el
// recorte al municipio de una de las dos vías.
var meses = ee.List.sequence(1, 12);
var conteoMensual = ee.ImageCollection(meses.map(function (m) {
  m = ee.Number(m);
  var d0 = ee.Date.fromYMD(ANIO, m, 1);
  var d1 = d0.advance(1, 'month');
  var sub = firms.filterDate(d0, d1);
  return sub.select('T21').count()
            .rename('dias_con_deteccion')
            .clip(ROI)
            .set({mes: m, n_imagenes: sub.size(),
                  'system:time_start': d0.millis()});
}));

// Serie temporal: sale directamente el mes pico.
print(ui.Chart.image.series({
  imageCollection: conteoMensual,
  region: ROI,
  reducer: ee.Reducer.sum(),
  scale: 1000,
  xProperty: 'system:time_start'
}).setOptions({
  title: 'MAPA 3 · Detecciones FIRMS por mes — Rurrenabaque ' + ANIO,
  hAxis: {title: 'Mes'},
  vAxis: {title: 'Píxeles-día con detección'},
  legend: {position: 'none'},
  colors: ['#B45309']
}));

var acumuladoAnual = conteoMensual.sum().clip(ROI).selfMask();
Map.addLayer(acumuladoAnual,
             {min: 1, max: 15, palette: ['#FFE082', '#FB8C00', '#B71C1C']},
             'MAPA 3 · Concentración anual FIRMS');

// --- 3. Contraste independiente: MODIS MCD64A1 ----------------------------
var mcd = ee.ImageCollection('MODIS/061/MCD64A1')
            .filterDate(inicio, fin).filterBounds(ROI);

// BurnDate es el día juliano de la quema (1-366), o 0 si no hubo.
var fechaQuema = mcd.select('BurnDate').max().clip(ROI).selfMask();
Map.addLayer(fechaQuema, {min: 180, max: 330,
             palette: ['#FFF59D', '#FB8C00', '#B71C1C', '#4A148C']},
             'MCD64A1 · día juliano de quema', false);

// Área quemada total que ve MCD64A1 en el municipio durante 2023.
var areaQuemadaMCD = fechaQuema.gt(0)
  .multiply(ee.Image.pixelArea())
  .reduceRegion({reducer: ee.Reducer.sum(), geometry: ROI,
                 scale: 463, maxPixels: 1e10})
  .get('BurnDate');
print('MCD64A1 · área quemada 2023 en el municipio (km²)',
      ee.Number(areaQuemadaMCD).divide(1e6));
print('  Referencia independiente. NO es la cicatriz del evento: es el año');
print('  entero y a 463 m. Se compara con el dNBR del bloque 07.');

// Distribución mensual de MCD64A1: debe apuntar al mismo periodo crítico
// que FIRMS. Si no lo hace, hay que explicar por qué antes de seguir.
print(ui.Chart.image.histogram({
  image: fechaQuema, region: ROI, scale: 463, maxBuckets: 24
}).setOptions({
  title: 'MCD64A1 — distribución del día juliano de quema (' + ANIO + ')',
  hAxis: {title: 'Día juliano'},
  vAxis: {title: 'Píxeles de 463 m'},
  colors: ['#6D4C41']
}));

// --- 4. Exportar ----------------------------------------------------------
Export.image.toDrive({
  image: acumuladoAnual.unmask(0).toInt16(),
  description: 'RBQ_FIRMS_' + ANIO + '_concentracion',
  folder: 'SIPRO_validacion_RBQ',
  fileNamePrefix: 'rbq_firms_' + ANIO + '_concentracion',
  region: ROI, scale: 1000, crs: 'EPSG:4326', maxPixels: 1e10
});

Export.image.toDrive({
  image: fechaQuema.unmask(0).toInt16(),
  description: 'RBQ_MCD64A1_' + ANIO + '_burndate',
  folder: 'SIPRO_validacion_RBQ',
  fileNamePrefix: 'rbq_mcd64a1_' + ANIO + '_burndate',
  region: ROI, scale: 463, crs: 'EPSG:4326', maxPixels: 1e10
});

print('--- Siguiente: 03_Analisis_Mensual ---');
