/**
 * ==========================================================================
 * BLOQUE 05 — IMÁGENES PRE Y POST INCENDIO
 * ==========================================================================
 * Script GEE: 05_Sentinel_Landsat_PrePost
 * Fase: 6
 *
 * QUÉ HACE
 *   Lista las escenas Sentinel-2 y Landsat 8/9 disponibles en las ventanas
 *   pre y post del evento, con su nubosidad, para que la elección de las dos
 *   fechas sea una DECISIÓN DOCUMENTADA y no un automatismo.
 *
 * EL PROBLEMA REAL DE ESTA ZONA
 *   Rurrenabaque está en el piedemonte amazónico del Beni. En temporada de
 *   quemas coinciden tres cosas que arruinan las imágenes ópticas:
 *
 *     · nubosidad convectiva casi diaria por la tarde;
 *     · humo denso, que Sentinel-2 no siempre marca como nube (la máscara
 *       SCL clasifica el humo espeso como nube, pero el humo fino pasa y
 *       oscurece el SWIR, que es justo la banda del NBR);
 *     · inundación estacional, que baja el NBR igual que lo baja el fuego.
 *
 *   Por eso este bloque NO elige la imagen solo: imprime el catálogo con la
 *   nubosidad de cada escena y deja la decisión a la vista. El §12 lo pide
 *   así: «las fechas definitivas deben seleccionarse después de identificar
 *   el evento», y «no utilizar agosto/diciembre automáticamente».
 *
 * CRITERIO SUGERIDO PARA ELEGIR
 *   PRE  : la escena más CERCANA al inicio del evento con nubosidad baja.
 *          Cuanto más lejos, más cambios ajenos al fuego se cuelan en el dNBR
 *          (crecimiento vegetal, otras quemas, cosecha).
 *   POST : ni demasiado pronto ni demasiado tarde. Antes de ~5 días el humo
 *          y los rescoldos falsean el SWIR; después de ~2 meses la
 *          revegetación empieza a borrar la cicatriz y el dNBR la
 *          subestima.
 *
 *   Si NINGUNA escena óptica sirve, hay una salida documentada: usar el
 *   producto MCD64A1 del bloque 02 como referencia observada principal y
 *   explicarlo en la tesis como una limitación del área de validación. Eso
 *   es honesto; forzar un dNBR sobre una escena con 60 % de nubes, no.
 *
 * ANTES DE EJECUTAR
 *   Rellena las cuatro fechas con lo que sugiere
 *   ../resultados/f4_evento_seleccionado.json → ventana_imagenes_sugerida.
 * ==========================================================================
 */

var RUTA = 'projects/fire-prediction-apolo/assets/';
var ROI  = ee.FeatureCollection(RUTA + 'Mun_RBQ').geometry(1);

// ⚠ RELLENAR desde f4_evento_seleccionado.json
var PRE_DESDE  = '';   // ventana_imagenes_sugerida.pre_desde
var PRE_HASTA  = '';   // ventana_imagenes_sugerida.pre_hasta
var POST_DESDE = '';   // ventana_imagenes_sugerida.post_desde
var POST_HASTA = '';   // ventana_imagenes_sugerida.post_hasta

var NUBES_MAX = 40;    // % — filtro amplio a propósito: mejor ver todo el
                       // catálogo y decidir, que descartar en silencio.

if (PRE_DESDE === '' || POST_DESDE === '') {
  print('⚠ FALTAN LAS VENTANAS. Rellénalas desde f4_evento_seleccionado.json.');
} else {

  // --- Sentinel-2 ---------------------------------------------------------
  // S2_SR_HARMONIZED: reflectancia de superficie, armonizada entre las
  // unidades de procesamiento antiguas y nuevas. Sin la armonización, una
  // escena de antes de 2022-01 y otra de después llevan un desplazamiento
  // de radiometría que se colaría entero en el dNBR.
  function s2(desde, hasta) {
    return ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
      .filterBounds(ROI).filterDate(desde, hasta)
      .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', NUBES_MAX));
  }

  // --- Landsat 8/9 nivel 2 ------------------------------------------------
  // Alternativa cuando S2 no da nada limpio. 30 m en vez de 10 m, pero para
  // un grid de 500 m eso da igual: los dos se van a agregar a la celda.
  function landsat(desde, hasta) {
    var l8 = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2');
    var l9 = ee.ImageCollection('LANDSAT/LC09/C02/T1_L2');
    return l8.merge(l9).filterBounds(ROI).filterDate(desde, hasta)
             .filter(ee.Filter.lt('CLOUD_COVER', NUBES_MAX));
  }

  function catalogo(col, etiqueta, propNubes) {
    var t = col.map(function (im) {
      return ee.Feature(null, {
        id: im.get('system:index'),
        fecha: ee.Date(im.get('system:time_start')).format('YYYY-MM-dd'),
        nubes_pct: im.get(propNubes)
      });
    }).sort('nubes_pct');
    print(etiqueta + ' — escenas ordenadas por nubosidad:', t);
    print(etiqueta + ' — nº de escenas:', col.size());
    return t;
  }

  print('=== CATÁLOGO DE ESCENAS ===');
  print('Elige mirando estas listas. Anota en la tesis: colección, id de');
  print('escena, fecha y nubosidad de las DOS imágenes que uses.');

  var s2Pre  = s2(PRE_DESDE, PRE_HASTA);
  var s2Post = s2(POST_DESDE, POST_HASTA);
  catalogo(s2Pre,  'SENTINEL-2 PRE',  'CLOUDY_PIXEL_PERCENTAGE');
  catalogo(s2Post, 'SENTINEL-2 POST', 'CLOUDY_PIXEL_PERCENTAGE');

  var lPre  = landsat(PRE_DESDE, PRE_HASTA);
  var lPost = landsat(POST_DESDE, POST_HASTA);
  catalogo(lPre,  'LANDSAT PRE',  'CLOUD_COVER');
  catalogo(lPost, 'LANDSAT POST', 'CLOUD_COVER');

  // --- Vista previa: mediana de cada ventana ------------------------------
  // La mediana de la ventana NO es lo que va al dNBR (para eso se elige una
  // escena concreta en el bloque 06). Es solo para ver de un vistazo si la
  // ventana tiene material aprovechable o está tapada de nubes.
  var visRGB = {bands: ['B4', 'B3', 'B2'], min: 300, max: 3000};
  var visSWIR = {bands: ['B12', 'B8', 'B4'], min: 300, max: 3500};

  Map.centerObject(ROI, 10);
  Map.setOptions('SATELLITE');
  Map.addLayer(s2Pre.median().clip(ROI), visRGB, 'MAPA 5 · PRE color natural');
  Map.addLayer(s2Post.median().clip(ROI), visRGB, 'MAPA 6 · POST color natural');

  // El falso color SWIR/NIR/rojo es donde la cicatriz de quema salta a la
  // vista: aparece en tonos marrón-rojizos frente al verde de la vegetación
  // sana. Sirve para confirmar a ojo, antes de calcular nada, que hay algo
  // que medir.
  Map.addLayer(s2Pre.median().clip(ROI), visSWIR, 'PRE falso color SWIR', false);
  Map.addLayer(s2Post.median().clip(ROI), visSWIR, 'POST falso color SWIR', false);
  Map.addLayer(ee.Image().byte().paint(ee.FeatureCollection([ee.Feature(ROI)]), 1, 2),
               {palette: ['#00E5FF']}, 'Límite');

  print('--- Siguiente: 06_NBR_dNBR (allí se fijan las dos escenas) ---');
}
