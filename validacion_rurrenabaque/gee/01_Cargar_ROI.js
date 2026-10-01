/**
 * ==========================================================================
 * BLOQUE 01 — CARGAR Y VERIFICAR EL ROI
 * ==========================================================================
 * Script GEE: 01_Cargar_ROI
 * Fase: 1 (comprobación del asset ya subido)
 *
 * QUÉ HACE
 *   Carga los dos límites municipales como assets, los dibuja y comprueba
 *   contra los valores que ya se verificaron localmente con
 *   python/verificar_shapefile.py. Si un número no cuadra, la subida a GEE
 *   salió mal y hay que arreglarlo AQUÍ, no tres fases más adelante.
 *
 * ANTES DE EJECUTAR — subida de los assets
 *   Assets → NEW → Shape files. Hay que arrastrar los CUATRO archivos
 *   obligatorios juntos: .shp, .shx, .dbf y .prj. Si falta el .prj, GEE
 *   asume WGS84 geográficas y el polígono —que está en UTM 19S, en metros—
 *   aterriza en el golfo de Guinea. El síntoma es engañoso: no da error,
 *   simplemente todo lo demás sale vacío.
 *
 *     projects/fire-prediction-apolo/assets/Mun_Apolo
 *     projects/fire-prediction-apolo/assets/Mun_RBQ
 *
 *   GEE reproyecta a EPSG:4326 al ingerir. Los polígonos NO se modifican.
 *
 * VALORES DE CONTROL (verificados localmente sobre Mun_RBQ.shp)
 *   Área      2.519,84 km²  (planimétrica UTM 19S)
 *             2.519,77 km²  (geodésica WGS84)
 *   Perímetro   300,85 km
 *   Centroide  -14,688621 · -67,305708
 *   BBox       lon -67,559663 → -67,073719 · lat -15,046462 → -14,343298
 *
 *   Ojo: el área geodésica que calcula GEE debe parecerse a la SEGUNDA, no
 *   a la primera. `ee.Geometry.area()` es geodésica sobre el elipsoide.
 *
 * PRODUCE
 *   MAPA 1 del §25 — límite municipal de Rurrenabaque.
 * ==========================================================================
 */

// --- 1. Assets -------------------------------------------------------------
var RUTA = 'projects/fire-prediction-apolo/assets/';
var apolo = ee.FeatureCollection(RUTA + 'Mun_Apolo');
var rbq   = ee.FeatureCollection(RUTA + 'Mun_RBQ');

// El ROI de esta validación. `geometry()` disuelve la colección en una sola
// geometría; `maxError` a 1 m evita que GEE simplifique el contorno.
var ROI = rbq.geometry(1);

// --- 2. Verificación numérica ---------------------------------------------
// maxError = 1 m: sin él, GEE puede simplificar el polígono para acelerar y
// el área saldría distinta de la calculada localmente sin motivo aparente.
var areaKm2  = ROI.area(1).divide(1e6);
var perimKm  = ROI.perimeter(1).divide(1000);
var centro   = ROI.centroid(1).coordinates();
var caja     = ROI.bounds(1).coordinates();

print('=== BLOQUE 01 — VERIFICACIÓN DEL ROI ===');
print('Municipio (atributos del .dbf):', rbq.first());
print('Área GEE (km²)      [control: 2519,77]', areaKm2);
print('Perímetro GEE (km)  [control: 300,85 ]', perimKm);
print('Centroide [lon, lat] [control: -67,3057 · -14,6886]', centro);
print('BBox', caja);

// Comprobación automática: ±1 % sobre el área de control.
var okArea = areaKm2.subtract(2519.77).abs().divide(2519.77).lt(0.01);
print('¿Área dentro del ±1 % del control?', okArea);
print('Si sale false: revisa que subiste el .prj junto al .shp.');

// --- 3. Contexto: los dos municipios del proyecto --------------------------
// Se dibujan juntos a propósito. La distancia entre ambos es la que sostiene
// el argumento de «validación EXTERNA»: no es una zona contigua ni comparte
// la fisiografía de Apolo.
var distanciaKm = apolo.geometry(1).centroid(1)
                    .distance(ROI.centroid(1), 1).divide(1000);
print('Distancia entre centroides Apolo–Rurrenabaque (km)', distanciaKm);
print('Área de Apolo (km²)', apolo.geometry(1).area(1).divide(1e6));

// --- 4. Cartografía --------------------------------------------------------
Map.centerObject(ROI, 9);
Map.setOptions('SATELLITE');

// Relleno translúcido + contorno grueso: el contorno solo se pierde sobre la
// ortofoto, y el relleno solo esconde lo que hay debajo.
Map.addLayer(ee.Image().byte().paint(ee.FeatureCollection([ee.Feature(ROI)]), 1),
             {palette: ['#26A69A'], opacity: 0.18}, 'Rurrenabaque (relleno)');
Map.addLayer(ee.Image().byte().paint(ee.FeatureCollection([ee.Feature(ROI)]), 1, 2),
             {palette: ['#00E5FF']}, 'Rurrenabaque (límite)');
Map.addLayer(ee.Image().byte().paint(apolo, 1, 2),
             {palette: ['#FFB300']}, 'Apolo (caso de estudio)', false);

// --- 5. Exportar el límite en WGS84 ---------------------------------------
// Para que el resto del flujo (Python, SIPRO FIRE) use exactamente la misma
// geometría que GEE, no una copia reproyectada por otra vía.
Export.table.toDrive({
  collection: rbq,
  description: 'RBQ_limite_municipal_wgs84',
  folder: 'SIPRO_validacion_RBQ',
  fileNamePrefix: 'Mun_RBQ_wgs84',
  fileFormat: 'GeoJSON'
});

print('--- Siguiente: 02_FIRMS_2023 ---');
