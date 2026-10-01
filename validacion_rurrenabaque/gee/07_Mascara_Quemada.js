/**
 * ==========================================================================
 * BLOQUE 07 — MÁSCARA OBSERVADA CON BANDA DE VALIDEZ
 * ==========================================================================
 * Script GEE: 07_Mascara_Quemada
 * Fases: 8 y 10
 *
 * QUÉ CAMBIÓ RESPECTO DE LA VERSIÓN ANTERIOR (ver 07_Mascara_Quemada.js.bak)
 *
 *   EL FALLO. La versión anterior exportaba `observadoGrid.unmask(0)`. Ese
 *   `unmask(0)` convertía en «no quemado» TODO lo que no tuviera información:
 *   nubes, sombras de nube, agua y los huecos del borde de escena. En un
 *   municipio del piedemonte amazónico eso puede ser una fracción enorme del
 *   territorio.
 *
 *   Por qué importa tanto: una celda tapada por una nube pasaba a valer
 *   observado = 0. Si el autómata propagaba por debajo de esa nube, la celda
 *   se contaba como FALSO POSITIVO. El modelo pagaba por una nube. Y al revés,
 *   una celda quemada bajo nube se contaba como TN, inflando la Accuracy.
 *
 *   LA CORRECCIÓN. Ahora se exporta una tercera banda:
 *
 *       observado         1 = quemado · 0 = no quemado
 *       fraccion_quemada  fracción de píxeles quemados dentro de la celda
 *       valido            1 = hay información satelital · 0 = no la hay
 *
 *   `valido` se construye a partir de la máscara real del dNBR (que ya
 *   incorpora nubes, sombras y agua) agregada a la celda de 500 m: una celda
 *   se declara válida solo si tiene COBERTURA_MINIMA de píxeles con dato.
 *
 *   f12_metricas_validacion.py calcula TP/FP/FN/TN únicamente sobre las
 *   celdas con valido = 1. Las de valido = 0 se excluyen por completo: no son
 *   TN, ni FP, ni FN, ni TP. Simplemente no se sabe.
 *
 * ==========================================================================
 * LAS TRES DECISIONES QUE HAY QUE JUSTIFICAR
 * ==========================================================================
 *
 *   1. UMBRAL dNBR = 0,27. Es el corte «moderada-baja» de la escala USGS /
 *      Key & Benson, no un valor buscado a mano. Por debajo de 0,10 se cuela
 *      ruido (estrés hídrico, sombras residuales, cambios fenológicos); 0,27
 *      es el primer corte que la literatura asocia a pérdida efectiva de
 *      cubierta. El script publica el área para seis umbrales para que la
 *      sensibilidad quede documentada — esa tabla NO sirve para escoger el
 *      que más convenga.
 *
 *   2. FRACCIÓN DE CELDA = 0,50. Mayoría simple. Es neutral: no favorece al
 *      Recall (que subiría con un umbral bajo, marcando como quemada
 *      cualquier celda rozada) ni a la Precision (que subiría con uno alto).
 *
 *   3. COBERTURA MÍNIMA = 0,50. Una celda de 500 m contiene ~625 píxeles de
 *      20 m. Si menos de la mitad tienen dato, lo que se sepa de esa celda es
 *      poco fiable y se declara no válida. Es el parámetro que gobierna
 *      cuánta superficie entra en la validación, así que se reporta siempre.
 *
 * ==========================================================================
 * ADVERTENCIA DE ESCALA
 *   Resolución del SENSOR 10-30 m · del ANÁLISIS 20-30 m · del AUTÓMATA
 *   500 m, que no cambia. Que la imagen sea de 10 m no convierte al modelo en
 *   un modelo de 10 m. La armonización se hace aquí, no en las métricas.
 *
 * ANTES DE EJECUTAR
 *   Copia COLECCION, ID_PRE e ID_POST tal como quedaron en el bloque 06.
 * ==========================================================================
 */

var RUTA = 'projects/fire-prediction-apolo/assets/';
var ROI  = ee.FeatureCollection(RUTA + 'Mun_RBQ').geometry(1);

// ⚠ Los mismos que en el bloque 06
var COLECCION = 'COPERNICUS/S2_SR_HARMONIZED';
var ID_PRE    = '';
var ID_POST   = '';

// --- Parámetros de decisión, declarados y visibles -------------------------
var UMBRAL_DNBR       = 0.27;   // USGS: severidad moderada-baja
var FRACCION_CELDA    = 0.50;   // mayoría simple de píxeles quemados
var COBERTURA_MINIMA  = 0.50;   // píxeles con dato mínimos para fiarse de la celda
var PASO_GRID         = 0.0045; // el mismo grid que Apolo

// Convención espacial de Apolo (ver bloque 08). Se repite aquí para que el
// ráster observado y el grid del CA salgan con los MISMOS índices.
var LAT_ORIGEN = -14.3420;
var LON_ORIGEN = -67.5630;

var ES_SENTINEL = COLECCION.indexOf('COPERNICUS') === 0;
var NIR    = ES_SENTINEL ? 'B8'  : 'SR_B5';
var SWIR2  = ES_SENTINEL ? 'B12' : 'SR_B7';
var VERDE  = ES_SENTINEL ? 'B3'  : 'SR_B3';
var ESCALA = ES_SENTINEL ? 20 : 30;

if (ID_PRE === '' || ID_POST === '') {
  print('⚠ FALTAN LOS IDS DE ESCENA. Cópialos del bloque 06.');
} else {

  // --- Reconstrucción del dNBR (idéntica al bloque 06) --------------------
  function preparar(id) {
    var im = ee.Image(COLECCION + '/' + id);
    if (!ES_SENTINEL) {
      im = ee.Image(im.select(['SR_B.']).multiply(0.0000275).add(-0.2)
                      .copyProperties(im, im.propertyNames()));
    }
    return im;
  }
  function enmascarar(im, id) {
    var o = ee.Image(COLECCION + '/' + id);
    if (ES_SENTINEL) {
      // SCL: 3 sombra de nube · 8 nube media · 9 nube alta · 10 cirros ·
      // 11 nieve. La sombra importa tanto como la nube: baja el NIR y dispara
      // un dNBR falso.
      var scl = o.select('SCL');
      return im.updateMask(scl.neq(3).and(scl.neq(8)).and(scl.neq(9))
                              .and(scl.neq(10)).and(scl.neq(11)));
    }
    var qa = o.select('QA_PIXEL');
    var mala = qa.bitwiseAnd(1 << 1).neq(0).or(qa.bitwiseAnd(1 << 2).neq(0))
                .or(qa.bitwiseAnd(1 << 3).neq(0)).or(qa.bitwiseAnd(1 << 4).neq(0));
    return im.updateMask(mala.not());
  }

  var pre  = enmascarar(preparar(ID_PRE),  ID_PRE).clip(ROI);
  var post = enmascarar(preparar(ID_POST), ID_POST).clip(ROI);

  var nbrPre  = pre.normalizedDifference([NIR, SWIR2]);
  var nbrPost = post.normalizedDifference([NIR, SWIR2]);
  var dnbr    = nbrPre.subtract(nbrPost).rename('dNBR');

  // Agua: MNDWI positivo en CUALQUIERA de las dos fechas. Un río que cambió
  // de cauce entre ambas produciría un dNBR alto sin que se haya quemado nada.
  // Es el riesgo específico de esta zona: Apolo no lo tenía.
  var agua = pre.normalizedDifference([VERDE, SWIR2]).gt(0.0)
             .or(post.normalizedDifference([VERDE, SWIR2]).gt(0.0));
  var dnbrValido = dnbr.updateMask(agua.not());

  // ======================================================================
  // LA BANDA `valido` — la corrección de esta versión
  // ======================================================================
  // `mask()` del dNBR ya vale 1 donde hay dato y 0 donde no (nube, sombra,
  // borde de escena), y updateMask(agua.not()) le ha restado el agua. Al
  // promediar esa máscara dentro de la celda de 500 m sale la FRACCIÓN de
  // píxeles con información.
  var proyGrid = ee.Projection('EPSG:4326').scale(PASO_GRID, PASO_GRID);

  var cobertura = dnbrValido.mask()
    .reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 4096})
    .reproject({crs: proyGrid})
    .rename('cobertura');

  var valido = cobertura.gte(COBERTURA_MINIMA).rename('valido');

  // La fracción quemada se calcula sobre los píxeles CON DATO, no sobre todos.
  // Si se dividiera entre todos, una celda medio tapada por una nube saldría
  // artificialmente poco quemada.
  var quemadoPix = dnbrValido.gte(UMBRAL_DNBR);
  var fraccion = quemadoPix.unmask(0)
    .reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 4096})
    .reproject({crs: proyGrid})
    .divide(cobertura.max(1e-6))          // ← normalizada por la cobertura
    .clamp(0, 1)
    .rename('fraccion_quemada');

  var observado = fraccion.gte(FRACCION_CELDA).rename('observado');

  // Fuera de las celdas válidas, observado y fraccion no significan nada.
  observado = observado.updateMask(valido);
  fraccion  = fraccion.updateMask(valido);

  // --- Informe -----------------------------------------------------------
  print('=== BLOQUE 07 — MÁSCARA OBSERVADA ===');
  print('Umbral dNBR         :', UMBRAL_DNBR, '(USGS moderada-baja)');
  print('Fracción de celda   :', FRACCION_CELDA, '(mayoría simple)');
  print('Cobertura mínima    :', COBERTURA_MINIMA);

  var areaNativa = quemadoPix.multiply(ee.Image.pixelArea())
    .reduceRegion({reducer: ee.Reducer.sum(), geometry: ROI,
                   scale: ESCALA, maxPixels: 1e10}).get('dNBR');
  print('Área quemada a resolución nativa (km²)',
        ee.Number(areaNativa).divide(1e6));

  var areaGrid = observado.selfMask().multiply(ee.Image.pixelArea())
    .reduceRegion({reducer: ee.Reducer.sum(), geometry: ROI,
                   scale: 500, maxPixels: 1e10}).get('observado');
  print('Área observada EN EL GRID de 500 m (km²)',
        ee.Number(areaGrid).divide(1e6));
  print('  Esta segunda es la que entra en las métricas. La diferencia entre');
  print('  ambas es el coste de la agregación y hay que declararla.');

  // Cuánta superficie se pierde por falta de dato. Es LA cifra que decide si
  // la validación es representativa o no.
  var nValidas = valido.selfMask().reduceRegion({
    reducer: ee.Reducer.count(), geometry: ROI, scale: 500, maxPixels: 1e10
  }).get('valido');
  var nTotal = ee.Image.constant(1).reproject({crs: proyGrid}).clip(ROI)
    .reduceRegion({reducer: ee.Reducer.count(), geometry: ROI,
                   scale: 500, maxPixels: 1e10}).get('constant');
  print('Celdas con información válida:', nValidas, 'de', nTotal);
  print('  ⚠ Si la cobertura válida baja del 60 %, la validación pierde');
  print('  representatividad: dilo en la tesis o busca otras escenas.');

  // --- Sensibilidad al umbral (va a la tesis) -----------------------------
  print('--- Sensibilidad al umbral dNBR ---');
  print(ee.FeatureCollection([0.10, 0.20, 0.27, 0.35, 0.44, 0.66].map(function (u) {
    var a = dnbrValido.gte(u).multiply(ee.Image.pixelArea())
      .reduceRegion({reducer: ee.Reducer.sum(), geometry: ROI,
                     scale: ESCALA, maxPixels: 1e10}).get('dNBR');
    return ee.Feature(null, {umbral: u, area_km2: ee.Number(a).divide(1e6)});
  })));
  print('--- Sensibilidad a la fracción de celda ---');
  print(ee.FeatureCollection([0.10, 0.25, 0.50, 0.75].map(function (fr) {
    var a = fraccion.gte(fr).selfMask().multiply(ee.Image.pixelArea())
      .reduceRegion({reducer: ee.Reducer.sum(), geometry: ROI,
                     scale: 500, maxPixels: 1e10}).get('fraccion_quemada');
    return ee.Feature(null, {fraccion: fr, area_km2: ee.Number(a).divide(1e6)});
  })));
  print('Estas tablas NO sirven para escoger el umbral que más convenga.');
  print('Sirven para declarar cuánto depende el resultado del corte elegido.');

  // --- Cartografía --------------------------------------------------------
  var severidad = ee.Image(0)
    .where(dnbrValido.gte(0.10).and(dnbrValido.lt(0.27)), 1)
    .where(dnbrValido.gte(0.27).and(dnbrValido.lt(0.44)), 2)
    .where(dnbrValido.gte(0.44).and(dnbrValido.lt(0.66)), 3)
    .where(dnbrValido.gte(0.66), 4)
    .updateMask(dnbrValido.mask()).rename('severidad');

  Map.centerObject(ROI, 10);
  Map.setOptions('SATELLITE');
  Map.addLayer(valido.not().selfMask(), {palette: ['#64748B']},
               'SIN DATO (nube, sombra o agua)');
  Map.addLayer(severidad.selfMask(), {min: 1, max: 4,
    palette: ['#FDE047', '#F97316', '#DC2626', '#7F1D1D']},
    'Severidad (clases USGS)', false);
  Map.addLayer(quemadoPix.selfMask(), {palette: ['#DC2626']},
               'MAPA 8 · Área quemada observada (nativa)');
  Map.addLayer(observado.selfMask(), {palette: ['#7F1D1D']},
               'OBSERVADO · grid 500 m', false);
  Map.addLayer(ee.Image().byte().paint(
    ee.FeatureCollection([ee.Feature(ROI)]), 1, 2),
    {palette: ['#00E5FF']}, 'Límite');

  // ======================================================================
  // EXPORTACIONES
  // ======================================================================
  // Ráster con las TRES bandas. `unmask(0)` aquí es seguro porque `valido`
  // viaja al lado y dice dónde ese 0 significa «no quemado» y dónde
  // significa «no se sabe».
  Export.image.toDrive({
    image: observado.unmask(0).toByte()
             .addBands(fraccion.unmask(0).toFloat())
             .addBands(valido.unmask(0).toByte())
             .addBands(cobertura.unmask(0).toFloat()),
    description: 'RBQ_observado_grid500',
    folder: 'SIPRO_validacion_RBQ',
    fileNamePrefix: 'rbq_observado_grid500',
    region: ROI, scale: 500, crs: 'EPSG:4326', maxPixels: 1e10
  });

  // --- CSV con los MISMOS índices fila/columna que el grid del CA ---------
  // Esta es la vía que consume f12: comparar dos CSV por `id` es mucho más
  // robusto que alinear dos rásteres, y elimina de raíz cualquier
  // desplazamiento de medio píxel entre observado y simulado.
  var proy = ee.Projection('EPSG:4326').scale(PASO_GRID, PASO_GRID);
  var pilaObs = observado.unmask(0).rename('observado')
    .addBands(fraccion.unmask(0).rename('fraccion_quemada'))
    .addBands(valido.unmask(0).rename('valido'))
    .addBands(cobertura.unmask(0).rename('cobertura'))
    .reproject({crs: proy});

  var celdas = ee.Image.constant(1).reproject({crs: proy})
    .reduceToVectors({geometry: ROI, scale: PASO_GRID * 111320,
                      geometryType: 'centroid', maxPixels: 1e9,
                      labelProperty: 'celda'});

  var tablaObs = pilaObs.reduceRegions({
    collection: celdas, reducer: ee.Reducer.first(), scale: 500
  }).map(function (f) {
    var c   = f.geometry().coordinates();
    var lon = ee.Number(c.get(0));
    var lat = ee.Number(c.get(1));
    // MISMAS fórmulas que el bloque 08. Si estas dos no coinciden, observado
    // y simulado no se cruzan y todo lo demás es basura.
    var fila    = ee.Number(LAT_ORIGEN).subtract(lat).divide(PASO_GRID).round();
    var columna = lon.subtract(LON_ORIGEN).divide(PASO_GRID).round();
    return f.set({
      id: ee.String('RBQ-').cat(ee.Number(fila).format('%03d')).cat('-')
            .cat(ee.Number(columna).format('%03d')),
      fila: fila, columna: columna, lat: lat, lon: lon
    });
  });

  Export.table.toDrive({
    collection: tablaObs,
    description: 'RBQ_observado_grid',
    folder: 'SIPRO_validacion_RBQ',
    fileNamePrefix: 'observado_grid',
    fileFormat: 'CSV',
    selectors: ['id', 'fila', 'columna', 'lat', 'lon',
                'observado', 'fraccion_quemada', 'valido', 'cobertura']
  });

  // Polígono de la cicatriz, para los mapas de la memoria.
  Export.table.toDrive({
    collection: quemadoPix.selfMask().reduceToVectors({
      geometry: ROI, scale: ESCALA, geometryType: 'polygon',
      eightConnected: false, maxPixels: 1e10, labelProperty: 'quemado'
    }),
    description: 'RBQ_cicatriz_poligono',
    folder: 'SIPRO_validacion_RBQ',
    fileNamePrefix: 'rbq_cicatriz_observada',
    fileFormat: 'GeoJSON'
  });

  print('--- Siguiente: 08_Exportar_Resultados ---');
}
