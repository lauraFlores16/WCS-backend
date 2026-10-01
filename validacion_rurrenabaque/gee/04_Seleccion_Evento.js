/**
 * ==========================================================================
 * BLOQUE 04 — VENTANA DEL EVENTO SELECCIONADO
 * ==========================================================================
 * Script GEE: 04_Seleccion_Evento
 * Fase: 4 (verificación cartográfica de lo que decidió Python)
 *
 * QUÉ HACE
 *   Toma el evento que identificó f3_f4_analisis_y_evento.py y lo dibuja
 *   día a día en GEE, para comprobar visualmente que el grupo es UN incendio
 *   y no varios que el agrupamiento juntó por error.
 *
 * POR QUÉ ESTA COMPROBACIÓN NO SOBRA
 *   DBSCAN agrupa por densidad, y en la temporada seca del Beni puede unir
 *   dos quemas agrícolas vecinas que arrancaron el mismo día a 1 km una de
 *   otra. Numéricamente forman un grupo; físicamente son dos incendios. La
 *   diferencia se ve mirando la progresión diaria: un incendio único avanza
 *   como un frente conexo desde su origen; dos incendios aparecen como dos
 *   manchas que crecen cada una por su lado y a veces se tocan al final.
 *
 *   Si al mirar los paneles se ven dos orígenes claramente separados, hay
 *   que volver a la Fase 4 y apretar --eps-m, DOCUMENTANDO el cambio. Lo que
 *   no se puede hacer es dejarlo pasar: el autómata arranca de un foco y
 *   propaga por vecindad, así que jamás reproducirá dos incendios
 *   independientes, y el IoU bajo se atribuiría al modelo cuando el fallo
 *   estaría en la definición del evento.
 *
 * ANTES DE EJECUTAR
 *   Rellena FECHA_INICIO y FECHA_FIN con lo que diga
 *   ../resultados/f4_evento_seleccionado.json. Están vacías A PROPÓSITO:
 *   poner unas fechas de ejemplo invitaría a usarlas sin comprobar, y el
 *   §42 prohíbe inventar fechas.
 * ==========================================================================
 */

var RUTA = 'projects/fire-prediction-apolo/assets/';
var ROI  = ee.FeatureCollection(RUTA + 'Mun_RBQ').geometry(1);

// ⚠ RELLENAR con f4_evento_seleccionado.json — no dejar como está.
var FECHA_INICIO = '';   // p. ej. '2023-09-14'  (campo inicio_utc)
var FECHA_FIN    = '';   // p. ej. '2023-09-22'  (campo fin_utc)

if (FECHA_INICIO === '' || FECHA_FIN === '') {
  print('⚠ FALTAN LAS FECHAS DEL EVENTO.');
  print('Ejecuta antes python/f3_f4_analisis_y_evento.py y copia aquí');
  print('inicio_utc y fin_utc de ../resultados/f4_evento_seleccionado.json');
  print('Este script no continúa con fechas inventadas (§42).');
} else {
  var d0 = ee.Date(FECHA_INICIO);
  var d1 = ee.Date(FECHA_FIN).advance(1, 'day');
  var nDias = d1.difference(d0, 'day');

  var firms = ee.ImageCollection('FIRMS').filterDate(d0, d1).filterBounds(ROI);

  print('=== EVENTO SELECCIONADO ===');
  print('Ventana:', FECHA_INICIO, '→', FECHA_FIN);
  print('Días:', nDias);
  print('Imágenes FIRMS en la ventana:', firms.size());

  Map.centerObject(ROI, 10);
  Map.setOptions('SATELLITE');
  Map.addLayer(ee.Image().byte().paint(ee.FeatureCollection([ee.Feature(ROI)]), 1, 2),
               {palette: ['#00E5FF']}, 'Límite');

  // --- Progresión día a día ----------------------------------------------
  // Cada día en su capa. Se encienden en orden desde el panel de capas: así
  // se ve si el frente crece desde un origen o si aparecen focos nuevos
  // desconectados (señal de que son incendios distintos).
  var dias = ee.List.sequence(0, nDias.subtract(1));
  var lista = dias.getInfo();
  lista.forEach(function (k) {
    var di = ee.Date(FECHA_INICIO).advance(k, 'day');
    var etiqueta = di.format('YYYY-MM-dd').getInfo();
    var capa = ee.ImageCollection('FIRMS')
                 .filterDate(di, di.advance(1, 'day'))
                 .select('T21').max().clip(ROI).selfMask();
    Map.addLayer(capa, {min: 325, max: 400,
                 palette: ['#FFF176', '#FB8C00', '#B71C1C']},
                 'Día ' + (k + 1) + ' · ' + etiqueta, k < 2);
  });

  // --- Acumulado del evento ----------------------------------------------
  // Este es el MAPA 4 del §25: los focos del evento seleccionado.
  var acumulado = firms.select('T21').count().clip(ROI).selfMask();
  Map.addLayer(acumulado, {min: 1, max: 8,
               palette: ['#FFE082', '#FB8C00', '#B71C1C']},
               'MAPA 4 · Acumulado del evento');

  // Superficie tocada por actividad térmica durante el evento. NO es el área
  // quemada (§10): FIRMS detecta anomalías, no cicatrices, y su píxel es de
  // 1 km. La superficie observada sale del dNBR, en el bloque 07.
  var kmTocados = acumulado.gt(0).multiply(ee.Image.pixelArea())
    .reduceRegion({reducer: ee.Reducer.sum(), geometry: ROI,
                   scale: 1000, maxPixels: 1e10}).get('T21');
  print('Superficie con actividad térmica FIRMS (km²)',
        ee.Number(kmTocados).divide(1e6));
  print('  ⚠ Esto NO es el área quemada. Es la superficie de los píxeles de');
  print('  1 km que registraron anomalía. La cicatriz sale del dNBR (§10).');

  Export.image.toDrive({
    image: acumulado.unmask(0).toInt16(),
    description: 'RBQ_FIRMS_evento_acumulado',
    folder: 'SIPRO_validacion_RBQ',
    fileNamePrefix: 'rbq_firms_evento_acumulado',
    region: ROI, scale: 1000, crs: 'EPSG:4326', maxPixels: 1e10
  });

  print('--- Siguiente: 05_Sentinel_Landsat_PrePost ---');
}
