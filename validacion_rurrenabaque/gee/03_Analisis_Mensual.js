/**
 * ==========================================================================
 * BLOQUE 03 — ANÁLISIS MENSUAL Y CONTRASTE ENTRE LAS DOS VÍAS
 * ==========================================================================
 * Script GEE: 03_Analisis_Mensual
 * Fase: 3
 *
 * QUÉ HACE
 *   Dibuja los doce meses de 2023 en un mosaico y produce la tabla mensual
 *   de GEE, que sirve para CONTRASTAR con la tabla que produce el script de
 *   Python. Las dos vías son independientes:
 *
 *       Python : detecciones puntuales del archivo tabular FIRMS,
 *                recortadas al polígono punto-en-polígono.
 *       GEE    : píxeles-día de la colección ráster FIRMS, recortados
 *                por clip al mismo polígono.
 *
 *   NO tienen por qué dar el mismo NÚMERO —una cuenta detecciones y la otra
 *   píxeles de 1 km, y varias detecciones caen en el mismo píxel— pero SÍ
 *   deben dar la misma FORMA: el mismo mes pico y el mismo periodo crítico.
 *   Si señalan meses distintos, hay un problema de recorte o de fechas en
 *   alguna de las dos, y hay que resolverlo antes de elegir el evento.
 *
 *   Esa comprobación cruzada es lo que hace defendible la Fase 3. Un solo
 *   camino no se puede verificar a sí mismo.
 *
 * PRODUCE
 *   Mosaico de 12 paneles + tabla mensual exportable a CSV.
 * ==========================================================================
 */

var RUTA = 'projects/fire-prediction-apolo/assets/';
var ROI  = ee.FeatureCollection(RUTA + 'Mun_RBQ').geometry(1);
var ANIO = 2023;

var NOMBRES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
               'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre',
               'Diciembre'];

var firms = ee.ImageCollection('FIRMS').filterDate(
  ee.Date.fromYMD(ANIO, 1, 1), ee.Date.fromYMD(ANIO + 1, 1, 1));

// --- 1. Tabla mensual ------------------------------------------------------
// Se calculan tres cosas por mes, y cada una responde a algo distinto:
//   pixeles_dia  cuánta actividad hubo (intensidad de la temporada)
//   pixeles_unicos  qué superficie tocó el fuego (extensión)
//   t21_max      cuánto llegó a calentarse (severidad aparente)
var filas = ee.List.sequence(1, 12).map(function (m) {
  m = ee.Number(m);
  var d0 = ee.Date.fromYMD(ANIO, m, 1);
  var sub = firms.filterDate(d0, d0.advance(1, 'month')).filterBounds(ROI);

  var conteo = sub.select('T21').count().clip(ROI);
  var pixelesDia = conteo.reduceRegion({
    reducer: ee.Reducer.sum(), geometry: ROI, scale: 1000, maxPixels: 1e10
  }).get('T21');

  var pixelesUnicos = conteo.gt(0).reduceRegion({
    reducer: ee.Reducer.sum(), geometry: ROI, scale: 1000, maxPixels: 1e10
  }).get('T21');

  var t21max = sub.select('T21').max().clip(ROI).reduceRegion({
    reducer: ee.Reducer.max(), geometry: ROI, scale: 1000, maxPixels: 1e10
  }).get('T21');

  return ee.Feature(null, {
    mes: m,
    mes_nombre: ee.List(NOMBRES).get(m.subtract(1)),
    pixeles_dia: pixelesDia,
    pixeles_unicos: pixelesUnicos,
    km2_tocados: ee.Number(pixelesUnicos),   // 1 píxel FIRMS ≈ 1 km²
    t21_max_k: t21max,
    imagenes: sub.size()
  });
});

var tabla = ee.FeatureCollection(filas);
print('=== TABLA MENSUAL (vía GEE) ===', tabla);
print('Contrastar con ../resultados/f3_focos_por_mes.csv (vía Python).');
print('Los números no coincidirán —píxeles de 1 km vs detecciones puntuales—');
print('pero el MES PICO debe ser el mismo en las dos.');

print(ui.Chart.feature.byFeature(tabla, 'mes_nombre', ['pixeles_dia'])
  .setChartType('ColumnChart')
  .setOptions({
    title: 'Actividad FIRMS por mes (píxeles-día) — Rurrenabaque ' + ANIO,
    hAxis: {title: 'Mes'}, vAxis: {title: 'Píxeles-día'},
    legend: {position: 'none'}, colors: ['#B45309']
  }));

// --- 2. Mosaico de doce paneles -------------------------------------------
Map.centerObject(ROI, 9);
Map.setOptions('SATELLITE');
Map.addLayer(ee.Image().byte().paint(ee.FeatureCollection([ee.Feature(ROI)]), 1, 2),
             {palette: ['#00E5FF']}, 'Límite');

// Se añaden apagadas: doce capas encendidas a la vez no se leen. Se van
// activando una a una desde el panel de capas para ver la progresión.
for (var m = 1; m <= 12; m++) {
  var d0 = ee.Date.fromYMD(ANIO, m, 1);
  var capa = firms.filterDate(d0, d0.advance(1, 'month'))
                  .select('T21').max().clip(ROI).selfMask();
  Map.addLayer(capa, {min: 325, max: 400,
               palette: ['#FFF176', '#FB8C00', '#B71C1C']},
               ('0' + m).slice(-2) + ' · ' + NOMBRES[m - 1], false);
}

// --- 3. Exportar -----------------------------------------------------------
Export.table.toDrive({
  collection: tabla,
  description: 'RBQ_FIRMS_tabla_mensual_' + ANIO,
  folder: 'SIPRO_validacion_RBQ',
  fileNamePrefix: 'rbq_firms_tabla_mensual_' + ANIO,
  fileFormat: 'CSV'
});

print('--- Siguiente: 04_Seleccion_Evento ---');
