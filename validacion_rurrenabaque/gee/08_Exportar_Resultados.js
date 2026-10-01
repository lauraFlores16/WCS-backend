/**
 * ==========================================================================
 * BLOQUE 08 — GRID DE RURRENABAQUE COMPATIBLE CON EL MOTOR
 * ==========================================================================
 * Script GEE: 08_Exportar_Resultados
 * Fases: 9 y 10
 *
 * QUÉ CAMBIÓ RESPECTO DE LA VERSIÓN ANTERIOR (ver 08_Exportar_Resultados.js.bak)
 *
 *   1. CONVENCIÓN ESPACIAL. La versión anterior no generaba `fila` ni
 *      `columna`, y el borrador que las calculaba lo hacía con fila creciendo
 *      hacia el NORTE. El grid de Apolo hace lo contrario. Corregido y
 *      explicado abajo — es el punto más importante de este archivo.
 *
 *   2. COLUMNAS OBLIGATORIAS. Ahora exporta las once que lee el motor:
 *      id, fila, columna, lat, lon, pendiente_grados, ndvi, humedad,
 *      viento_u, viento_v, prob_ignicion.
 *
 *   3. VARIABLE `humedad`. La versión anterior hacía `humedad_relativa × 0,4`,
 *      que era una invención. Ahora usa humedad volumétrica del suelo de
 *      ERA5-Land, que es lo que se corresponde con Apolo. Justificación abajo.
 *
 * ==========================================================================
 * LA CONVENCIÓN ESPACIAL DE APOLO — MEDIDA, NO SUPUESTA
 * ==========================================================================
 * Leyendo backend_django/api/datos/grid.csv (36.390 celdas):
 *
 *     fila 11  → lat media −14,00037      fila 233 → lat media −14,99937
 *     col  26  → lon media −68,99822      col  247 → lon media −68,00372
 *
 *   → FILA CRECE HACIA EL SUR   (la latitud BAJA cuando la fila sube)
 *   → COLUMNA CRECE HACIA EL ESTE (la longitud SUBE cuando la columna sube)
 *
 * Esto NO es un detalle cosmético. El motor calcula la influencia del viento
 * y de la pendiente con los desplazamientos (df, dc) entre celdas vecinas,
 * en automata.py línea 157:
 *
 *     dot = ((dc / norma) * u + (-df / norma) * v) / vel
 *
 * donde u es la componente ESTE del viento y v la componente NORTE. Fíjate en
 * el signo: `-df` multiplica a v. Eso solo es correcto si `df` positivo
 * significa ir hacia el SUR. Si el grid de Rurrenabaque numerase las filas al
 * revés, el viento empujaría el fuego en la dirección contraria y la
 * validación mediría un modelo que no es el que se calibró en Apolo.
 *
 * Fórmulas usadas aquí, con el mismo paso de 0,0045° que Apolo:
 *
 *     fila    = round( (LAT_ORIGEN - lat) / PASO )      LAT_ORIGEN = borde norte
 *     columna = round( (lon - LON_ORIGEN) / PASO )      LON_ORIGEN = borde oeste
 *
 * Los índices arrancan en 0 en la esquina NOROESTE del rectángulo envolvente
 * de Rurrenabaque. En Apolo arrancan en 11 y 26 porque allí son índices de una
 * rejilla global mayor; lo que importa no es el número absoluto sino que la
 * ORIENTACIÓN sea la misma, porque el motor solo usa diferencias (df, dc).
 * f10_verificar_grid_rbq.py comprueba esto antes de dejar seguir.
 *
 * ==========================================================================
 * LA VARIABLE `humedad` — QUÉ ES EN APOLO
 * ==========================================================================
 * El §7 pedía averiguarlo en vez de suponerlo. Evidencia medida sobre
 * grid.csv:
 *
 *   · Rango 0,2196 – 0,3995 · mediana 0,3763 · media 0,3714
 *   · Solo 87 valores distintos en 36.390 celdas
 *   · Bloques contiguos de hasta ~50 × 22 km con el mismo valor
 *   · `viento_u` tiene 97 valores distintos → misma resolución nativa
 *   · `ndvi`, en cambio, tiene 2.749 → resolución fina, otra fuente
 *
 * De ahí se deduce:
 *
 *   NO es humedad relativa del aire. En Apolo en temporada seca la HR media
 *   ronda 0,60–0,75; 0,22–0,40 queda muy por debajo.
 *
 *   NO es humedad del combustible fino muerto. Esa se mueve en 0,05–0,30 y
 *   varía a resolución de vegetación, no en bloques de 50 km.
 *
 *   SÍ encaja con HUMEDAD VOLUMÉTRICA DEL SUELO en m³/m³ de un reanálisis
 *   ERA5. El rango 0,22–0,40 m³/m³ es exactamente el de suelo tropical, y los
 *   bloques gruesos corresponden a la resolución del reanálisis (~0,25°, unos
 *   28 km), la misma que comparte con el viento.
 *
 * Por eso aquí se usa `volumetric_soil_water_layer_1` de ERA5-Land, que es la
 * capa 0–7 cm. Misma definición, misma unidad (m³/m³), misma lógica.
 *
 *   ⚠ CONFIRMACIÓN PENDIENTE. Esto es una inferencia fuerte a partir de la
 *   distribución, no una lectura de la fuente. Para cerrarla del todo hay que
 *   abrir `variables_gee_v2.csv` o el script de GEE que generó el grid de
 *   Apolo y comprobar qué banda se usó. Si resultase ser otra, cambiar la
 *   constante BANDA_HUMEDAD de abajo y volver a exportar: es una sola línea.
 *   Mientras tanto, el CSV exportado lleva la fuente escrita en su propio
 *   nombre de archivo y en el resumen, para que quede trazable.
 *
 * ==========================================================================
 * ANTES DE EJECUTAR
 *   Rellena FECHA_INICIO y FECHA_FIN con las de resultados/evento_validacion.json
 * ==========================================================================
 */

var RUTA = 'projects/fire-prediction-apolo/assets/';
var rbq  = ee.FeatureCollection(RUTA + 'Mun_RBQ');
var ROI  = rbq.geometry(1);

// ⚠ RELLENAR desde resultados/evento_validacion.json
var FECHA_INICIO = '';   // fecha_inicio
var FECHA_FIN    = '';   // fecha_fin

// --- Convención espacial: IDÉNTICA a la de Apolo -------------------------
var PASO = 0.0045;          // grados. NO cambiar.
// Esquina NOROESTE del rectángulo envolvente de Rurrenabaque, alineada al paso.
// Sale de datos/rbq_roi.json (bbox verificado sobre Mun_RBQ.shp).
var LAT_ORIGEN = -14.3420;  // borde NORTE  (lat máxima, redondeada al paso)
var LON_ORIGEN = -67.5630;  // borde OESTE  (lon mínima, redondeada al paso)

// La banda de humedad. Si se confirma que Apolo usó otra, se cambia aquí.
var BANDA_HUMEDAD = 'volumetric_soil_water_layer_1';   // m³/m³, capa 0-7 cm

if (FECHA_INICIO === '' || FECHA_FIN === '') {
  print('⚠ FALTAN LAS FECHAS DEL EVENTO.');
  print('Ejecuta antes python/f3_f4_analisis_y_evento.py y copia aquí');
  print('fecha_inicio y fecha_fin de resultados/evento_validacion.json.');
  print('Este script no continúa con fechas inventadas.');
} else {

  var d0 = ee.Date(FECHA_INICIO);
  var d1 = ee.Date(FECHA_FIN).advance(1, 'day');

  // ======================================================================
  // 1. TOPOGRAFÍA — SRTM
  // ======================================================================
  // El mismo DEM que usa el proyecto en Apolo (servicios/terreno.py). Cambiar
  // de DEM entre las dos zonas metería una diferencia que no es del
  // territorio sino del dato.
  var dem = ee.Image('USGS/SRTMGL1_003').clip(ROI);
  var pendiente = ee.Terrain.slope(dem).rename('pendiente_grados');

  print('=== BLOQUE 08 — VARIABLES DEL TERRITORIO ===');
  print('Elevación (m):', dem.reduceRegion({
    reducer: ee.Reducer.minMax().combine(ee.Reducer.mean(), '', true),
    geometry: ROI, scale: 500, maxPixels: 1e10}));
  print('Pendiente (grados):', pendiente.reduceRegion({
    reducer: ee.Reducer.minMax().combine(ee.Reducer.mean(), '', true),
    geometry: ROI, scale: 500, maxPixels: 1e10}));
  print('  Referencia Apolo: pendiente mediana 20,2° · p90 34,4° · máx 71,3°.');
  print('  Rurrenabaque es piedemonte y llanura: si la mediana sale mucho');
  print('  menor, el término f_pendiente pesará menos aquí y hay que decirlo');
  print('  al interpretar las métricas.');

  // ======================================================================
  // 2. VEGETACIÓN — NDVI ANTERIOR AL EVENTO
  // ======================================================================
  // MOD13A1: compuesto de 16 días a 500 m, que coincide con la celda. Se toma
  // el último compuesto que TERMINA antes del evento: el de después ya está
  // afectado por el fuego, y usarlo metería la respuesta dentro de la
  // pregunta.
  var ndvi = ee.ImageCollection('MODIS/061/MOD13A1')
    .filterDate(d0.advance(-40, 'day'), d0)
    .select('NDVI').sort('system:time_start', false).first()
    .multiply(0.0001).rename('ndvi').clip(ROI);

  print('NDVI previo al evento:', ndvi.reduceRegion({
    reducer: ee.Reducer.minMax().combine(ee.Reducer.mean(), '', true),
    geometry: ROI, scale: 500, maxPixels: 1e10}));
  print('  Referencia Apolo: NDVI mín 0,100 · mediana 0,313 · máx 0,444.');
  print('  Recuerda NDVI_BARRERA = 0,10: por debajo la celda es INERTE. En');
  print('  Apolo ninguna celda baja de ahí, así que el modelo nunca generó');
  print('  barreras por vegetación. Si aquí pasa lo mismo, se repetirá el');
  print('  sesgo hacia la sobreestimación.');

  // ======================================================================
  // 3. METEOROLOGÍA Y HUMEDAD DEL SUELO — ERA5-Land
  // ======================================================================
  var era5 = ee.ImageCollection('ECMWF/ERA5_LAND/HOURLY')
    .filterDate(d0, d1).filterBounds(ROI);
  print('Horas ERA5-Land en la ventana del evento:', era5.size());

  // Viento: se promedian las COMPONENTES u y v, no el módulo. Promediar
  // módulos daría una velocidad media correcta pero perdería la dirección, y
  // la dirección es justo lo que gobierna hacia dónde avanza el frente.
  // Convención de ERA5 y del motor: u = componente ESTE, v = componente NORTE.
  var viento = era5.select(['u_component_of_wind_10m', 'v_component_of_wind_10m'])
    .mean().rename(['viento_u', 'viento_v']).clip(ROI);

  // La columna `humedad` del grid: humedad volumétrica del suelo, m³/m³.
  // Misma definición y unidad que en Apolo (ver cabecera).
  var humedad = era5.select(BANDA_HUMEDAD).mean().rename('humedad').clip(ROI);

  var tempC  = era5.select('temperature_2m').mean()
                 .subtract(273.15).rename('temperatura_c').clip(ROI);
  var rocioC = era5.select('dewpoint_temperature_2m').mean()
                 .subtract(273.15).rename('rocio_c').clip(ROI);

  // Humedad relativa por Magnus. ERA5-Land no la publica directamente. Se
  // exporta como columna EXTRA, no como la columna `humedad`.
  function presionVapor(t) {
    return t.expression('0.6108 * exp(17.27 * T / (T + 237.3))', {T: t});
  }
  var hr = presionVapor(rocioC).divide(presionVapor(tempC))
             .clamp(0, 1).rename('humedad_relativa');

  var lluvia = era5.select('total_precipitation_hourly').sum()
                 .multiply(1000).rename('lluvia_mm_total').clip(ROI);
  var vel = viento.expression('sqrt(u*u + v*v)',
    {u: viento.select('viento_u'), v: viento.select('viento_v')}).rename('viento_ms');

  print('--- Comprobación de rangos contra Apolo ---');
  print('humedad del suelo (m³/m³):', humedad.reduceRegion({
    reducer: ee.Reducer.minMax().combine(ee.Reducer.mean(), '', true),
    geometry: ROI, scale: 500, maxPixels: 1e9}));
  print('  Referencia Apolo: mín 0,220 · mediana 0,376 · máx 0,400 m³/m³.');
  print('  Si Rurrenabaque sale MUY fuera de ese rango, revisa la banda antes');
  print('  de seguir: puede que Apolo usara otra variable (ver cabecera).');
  print('viento (m/s):', vel.reduceRegion({
    reducer: ee.Reducer.mean().combine(ee.Reducer.max(), '', true),
    geometry: ROI, scale: 500, maxPixels: 1e9}));
  print('  Referencia Apolo: mediana 0,39 · máx 0,52 m/s.');
  print('  ⚠ SPOTTING_VIENTO_MIN = 1,0 m/s. En Apolo el spotting nunca se');
  print('  dispara. Si aquí el viento lo supera, SÍ se disparará — y el motor');
  print('  tiene el signo norte-sur del salto invertido. f11 lo cuenta y avisa.');
  print('temperatura media (°C):', tempC.reduceRegion({
    reducer: ee.Reducer.mean(), geometry: ROI, scale: 500, maxPixels: 1e9}));
  print('precipitación acumulada (mm):', lluvia.reduceRegion({
    reducer: ee.Reducer.mean(), geometry: ROI, scale: 500, maxPixels: 1e9}));
  print('  ⚠ Si la precipitación acumulada del evento es alta, revisa la');
  print('  selección: un incendio que se propaga bajo lluvia sostenida es');
  print('  sospechoso y puede indicar que el agrupamiento mezcló varias quemas.');

  // ======================================================================
  // 4. EL GRID, CON LAS ONCE COLUMNAS OBLIGATORIAS
  // ======================================================================
  var proyGrid = ee.Projection('EPSG:4326').scale(PASO, PASO);

  var pila = pendiente
    .addBands(ndvi)
    .addBands(humedad)
    .addBands(viento)          // viento_u, viento_v
    .addBands(tempC)
    .addBands(hr)
    .addBands(dem.rename('elevacion_m'))
    .reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 4096})
    .reproject({crs: proyGrid});

  // Un punto por celda: centroides de la rejilla reproyectada.
  var celdas = ee.Image.constant(1).reproject({crs: proyGrid})
    .reduceToVectors({
      geometry: ROI, scale: PASO * 111320, geometryType: 'centroid',
      maxPixels: 1e9, labelProperty: 'celda'
    });

  var grid = pila.reduceRegions({
    collection: celdas, reducer: ee.Reducer.first(), scale: 500
  }).map(function (f) {
    var c   = f.geometry().coordinates();
    var lon = ee.Number(c.get(0));
    var lat = ee.Number(c.get(1));

    // --- LA PARTE QUE IMPORTA -------------------------------------------
    // fila crece hacia el SUR  → (LAT_ORIGEN − lat) / PASO
    // columna crece hacia el ESTE → (lon − LON_ORIGEN) / PASO
    var fila    = ee.Number(LAT_ORIGEN).subtract(lat).divide(PASO).round();
    var columna = lon.subtract(LON_ORIGEN).divide(PASO).round();

    return f.set({
      // `id` en Apolo es un UUID sin relación con fila/columna. Aquí se usa
      // un identificador determinista y legible: reproducible entre corridas,
      // que es lo que necesita una validación.
      id: ee.String('RBQ-')
            .cat(ee.Number(fila).format('%03d')).cat('-')
            .cat(ee.Number(columna).format('%03d')),
      fila: fila,
      columna: columna,
      lat: lat,
      lon: lon,
      // XGBoost NO se aplica en Rurrenabaque. La columna existe solo para que
      // el CSV tenga la misma forma que el de Apolo, y va a 0 en todas las
      // celdas: valor neutro y documentado. El motor no la usa para propagar
      // —la usa la interfaz para priorizar dónde podría arrancar un incendio—
      // y aquí los focos iniciales vienen de FIRMS, que es observación.
      prob_ignicion: 0
    });
  });

  print('Celdas generadas (control: 10.449 dentro del municipio):', grid.size());

  Export.table.toDrive({
    collection: grid,
    description: 'RBQ_grid_500m',
    folder: 'SIPRO_validacion_RBQ',
    fileNamePrefix: 'rbq_grid_500m',
    fileFormat: 'CSV',
    // Las once obligatorias primero, en el mismo orden que grid.csv de Apolo,
    // y después las tres extra.
    selectors: ['id', 'fila', 'columna', 'lat', 'lon', 'pendiente_grados',
                'ndvi', 'humedad', 'viento_u', 'viento_v', 'prob_ignicion',
                'temperatura_c', 'humedad_relativa', 'elevacion_m']
  });

  // El DEM aparte: el motor lo usa como ráster propio para el término de
  // pendiente real entre celdas vecinas (la vía de Rothermel).
  Export.image.toDrive({
    image: dem.toInt16(),
    description: 'RBQ_dem_srtm',
    folder: 'SIPRO_validacion_RBQ',
    fileNamePrefix: 'rbq_dem_srtm',
    region: ROI, scale: 30, crs: 'EPSG:4326', maxPixels: 1e10
  });

  // Resumen meteorológico del evento, con la trazabilidad de la fuente de
  // humedad escrita dentro.
  Export.table.toDrive({
    collection: ee.FeatureCollection([ee.Feature(null, {
      evento_inicio: FECHA_INICIO,
      evento_fin: FECHA_FIN,
      fuente_humedad: 'ECMWF/ERA5_LAND/HOURLY · ' + BANDA_HUMEDAD + ' · m3/m3',
      fuente_viento: 'ECMWF/ERA5_LAND/HOURLY · u/v_component_of_wind_10m · m/s',
      fuente_ndvi: 'MODIS/061/MOD13A1 · compuesto previo al evento',
      fuente_topografia: 'USGS/SRTMGL1_003',
      convencion_fila: 'crece hacia el SUR (igual que Apolo)',
      convencion_columna: 'crece hacia el ESTE (igual que Apolo)',
      paso_grados: PASO,
      lat_origen: LAT_ORIGEN,
      lon_origen: LON_ORIGEN,
      temperatura_c_media: tempC.reduceRegion({
        reducer: ee.Reducer.mean(), geometry: ROI, scale: 500, maxPixels: 1e9
      }).get('temperatura_c'),
      humedad_suelo_media: humedad.reduceRegion({
        reducer: ee.Reducer.mean(), geometry: ROI, scale: 500, maxPixels: 1e9
      }).get('humedad'),
      humedad_relativa_media: hr.reduceRegion({
        reducer: ee.Reducer.mean(), geometry: ROI, scale: 500, maxPixels: 1e9
      }).get('humedad_relativa'),
      viento_ms_medio: vel.reduceRegion({
        reducer: ee.Reducer.mean(), geometry: ROI, scale: 500, maxPixels: 1e9
      }).get('viento_ms'),
      viento_ms_maximo: vel.reduceRegion({
        reducer: ee.Reducer.max(), geometry: ROI, scale: 500, maxPixels: 1e9
      }).get('viento_ms'),
      lluvia_mm_total: lluvia.reduceRegion({
        reducer: ee.Reducer.mean(), geometry: ROI, scale: 500, maxPixels: 1e9
      }).get('lluvia_mm_total'),
      ndvi_medio: ndvi.reduceRegion({
        reducer: ee.Reducer.mean(), geometry: ROI, scale: 500, maxPixels: 1e9
      }).get('ndvi'),
      pendiente_media: pendiente.reduceRegion({
        reducer: ee.Reducer.mean(), geometry: ROI, scale: 500, maxPixels: 1e10
      }).get('pendiente_grados')
    })]),
    description: 'RBQ_resumen_variables_evento',
    folder: 'SIPRO_validacion_RBQ',
    fileNamePrefix: 'rbq_resumen_variables_evento',
    fileFormat: 'CSV'
  });

  print('--- Exporta desde la pestaña Tasks, luego: ---');
  print('    python f10_verificar_grid_rbq.py   ← OBLIGATORIO antes de seguir');
  print('    python f11_ejecutar_ca_rbq.py');
}
