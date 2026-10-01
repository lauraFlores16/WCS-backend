/**
 * ==========================================================================
 * BLOQUE 06 — NBR, dNBR Y RdNBR
 * ==========================================================================
 * Script GEE: 06_NBR_dNBR
 * Fase: 7
 *
 * QUÉ HACE
 *   Calcula el Normalized Burn Ratio antes y después del evento y su
 *   diferencia. Produce los mapas 5, 6 y 7 del §25 y el histograma que
 *   sostiene la elección del umbral en el bloque 07.
 *
 * QUÉ ES EL NBR Y POR QUÉ FUNCIONA
 *
 *       NBR = (NIR − SWIR2) / (NIR + SWIR2)
 *
 *   La vegetación sana refleja mucho en el infrarrojo cercano (la estructura
 *   celular de la hoja) y poco en el SWIR2 (el agua de la hoja lo absorbe).
 *   NBR alto. Tras el fuego pasan las dos cosas a la vez: la hoja desaparece
 *   —cae el NIR— y el suelo queda seco y con carbón —sube el SWIR2—. NBR
 *   bajo. Por eso el índice separa tan bien lo quemado: las dos bandas se
 *   mueven en sentidos opuestos y el contraste se amplifica.
 *
 *       dNBR = NBR_pre − NBR_post
 *
 *   Positivo y grande = pérdida de vegetación entre las dos fechas.
 *
 * LA TRAMPA DE ESTA ZONA — leer antes de fijar el umbral
 *   El dNBR no distingue POR QUÉ bajó el NBR. En el Beni hay tres causas
 *   que no son fuego y dan la misma señal:
 *
 *     · INUNDACIÓN. El agua libre tiene NBR muy bajo. Una llanura inundada
 *       entre las dos fechas produce un dNBR alto idéntico al de una quema.
 *       Es el riesgo mayor aquí y por eso el script enmascara el agua con
 *       el índice MNDWI antes de nada.
 *     · DEFORESTACIÓN Y COSECHA. Tala rasa o cosecha de un cultivo también
 *       bajan el NBR. Suelen dar formas geométricas, con bordes rectos: si
 *       la máscara final sale con rectángulos perfectos, sospechar.
 *     · SOMBRA DE NUBE. Baja las dos bandas pero no por igual. Se descarta
 *       con la máscara de calidad, que aquí incluye las sombras.
 *
 *   Todo esto va DOCUMENTADO en la tesis como limitación (§13), no se
 *   esconde.
 *
 * UMBRALES DE REFERENCIA (USGS / Key & Benson)
 *       < 0,10   sin quemar
 *    0,10–0,27   severidad baja
 *    0,27–0,44   moderada-baja
 *    0,44–0,66   moderada-alta
 *       > 0,66   alta
 *
 *   El §13 es explícito: «NO elegir el umbral solamente porque produce una
 *   forma parecida a la simulación». Aquí se usa el umbral estándar de la
 *   literatura y el bloque 07 mide cuánto cambia el resultado si se mueve.
 *
 * ANTES DE EJECUTAR
 *   Fija ID_PRE e ID_POST con los ids concretos que salieron del bloque 05.
 *   Una escena, no una mediana: la mediana de una ventana mezcla fechas y
 *   el dNBR dejaría de referirse a un intervalo definido.
 * ==========================================================================
 */

var RUTA = 'projects/fire-prediction-apolo/assets/';
var ROI  = ee.FeatureCollection(RUTA + 'Mun_RBQ').geometry(1);

// ⚠ RELLENAR con los ids del bloque 05
var COLECCION = 'COPERNICUS/S2_SR_HARMONIZED';  // o 'LANDSAT/LC08/C02/T1_L2'
var ID_PRE    = '';   // p. ej. '20230903T143721_20230903T144524_T19LEK'
var ID_POST   = '';   // p. ej. '20231012T143719_20231012T144352_T19LEK'

var ES_SENTINEL = COLECCION.indexOf('COPERNICUS') === 0;

// Bandas según sensor. Sentinel-2: B8 (NIR 842 nm), B12 (SWIR2 2190 nm).
// Landsat 8/9 L2: SR_B5 (NIR), SR_B7 (SWIR2), con escala propia.
var NIR   = ES_SENTINEL ? 'B8'  : 'SR_B5';
var SWIR2 = ES_SENTINEL ? 'B12' : 'SR_B7';
var VERDE = ES_SENTINEL ? 'B3'  : 'SR_B3';

if (ID_PRE === '' || ID_POST === '') {
  print('⚠ FALTAN LOS IDS DE ESCENA. Ejecuta antes el bloque 05 y cópialos.');
} else {

  // --- Preparación de la escena -------------------------------------------
  function preparar(id) {
    var im = ee.Image(COLECCION + '/' + id);

    if (!ES_SENTINEL) {
      // Landsat Collection 2 guarda enteros escalados. Sin aplicar la escala,
      // el NBR sale igual (es un cociente normalizado) pero el MNDWI y
      // cualquier umbral sobre reflectancia, no.
      im = im.select(['SR_B.'])
             .multiply(0.0000275).add(-0.2)
             .copyProperties(im, im.propertyNames());
      im = ee.Image(im);
    }
    return im;
  }

  // --- Máscara de calidad --------------------------------------------------
  function enmascarar(im, id) {
    var original = ee.Image(COLECCION + '/' + id);
    if (ES_SENTINEL) {
      // SCL — Scene Classification Layer. Se descartan:
      //   3 sombra de nube · 8 nube media · 9 nube alta ·
      //  10 cirros · 11 nieve/hielo
      // La sombra de nube (3) importa tanto como la nube: baja el NIR y
      // dispara un dNBR falso.
      var scl = original.select('SCL');
      var buena = scl.neq(3).and(scl.neq(8)).and(scl.neq(9))
                    .and(scl.neq(10)).and(scl.neq(11));
      return im.updateMask(buena);
    }
    // Landsat QA_PIXEL: bit 3 nube, bit 4 sombra, bit 1 dilatada, bit 2 cirros
    var qa = original.select('QA_PIXEL');
    var mala = qa.bitwiseAnd(1 << 1).neq(0)
        .or(qa.bitwiseAnd(1 << 2).neq(0))
        .or(qa.bitwiseAnd(1 << 3).neq(0))
        .or(qa.bitwiseAnd(1 << 4).neq(0));
    return im.updateMask(mala.not());
  }

  var pre  = enmascarar(preparar(ID_PRE),  ID_PRE).clip(ROI);
  var post = enmascarar(preparar(ID_POST), ID_POST).clip(ROI);

  var fechaPre  = ee.Date(ee.Image(COLECCION + '/' + ID_PRE).get('system:time_start'));
  var fechaPost = ee.Date(ee.Image(COLECCION + '/' + ID_POST).get('system:time_start'));

  print('=== BLOQUE 06 — NBR / dNBR ===');
  print('Colección :', COLECCION);
  print('PRE       :', ID_PRE, fechaPre.format('YYYY-MM-dd'));
  print('POST      :', ID_POST, fechaPost.format('YYYY-MM-dd'));
  print('Intervalo (días):', fechaPost.difference(fechaPre, 'day'));
  print('Resolución nativa (m):', ES_SENTINEL ? 10 : 30);
  print('  ⚠ Esta es la resolución del SENSOR. El autómata sigue trabajando');
  print('  a 500 m (§32). La armonización se hace en el bloque 07.');

  // --- NBR -----------------------------------------------------------------
  var nbrPre  = pre.normalizedDifference([NIR, SWIR2]).rename('NBR_pre');
  var nbrPost = post.normalizedDifference([NIR, SWIR2]).rename('NBR_post');
  var dnbr    = nbrPre.subtract(nbrPost).rename('dNBR');

  // --- RdNBR (dNBR relativizado) ------------------------------------------
  // Miller & Thode: normaliza por la vegetación que HABÍA antes. Sin esto,
  // una sabana con poca biomasa quemada entera da un dNBR menor que un bosque
  // chamuscado a medias, y el umbral absoluto trata peor a la sabana. En un
  // municipio con bosque amazónico Y llanura de sabana como este, la
  // diferencia es real. Se calcula como comprobación, no como referencia
  // principal.
  var rdnbr = dnbr.divide(nbrPre.abs().sqrt().max(0.001)).rename('RdNBR');

  // --- Máscara de agua -----------------------------------------------------
  // MNDWI = (VERDE − SWIR2)/(VERDE + SWIR2). Positivo sobre agua libre.
  // Se enmascara el agua de las DOS fechas: un río que cambió de cauce entre
  // ambas produciría dNBR alto sin que se haya quemado nada.
  var aguaPre  = pre.normalizedDifference([VERDE, SWIR2]).gt(0.0);
  var aguaPost = post.normalizedDifference([VERDE, SWIR2]).gt(0.0);
  var agua = aguaPre.or(aguaPost).rename('agua');
  var dnbrSinAgua = dnbr.updateMask(agua.not());

  // --- Cartografía ---------------------------------------------------------
  var visNBR  = {min: -0.5, max: 1.0,
                 palette: ['#7F1D1D', '#F59E0B', '#FEF3C7', '#65A30D', '#14532D']};
  var visDNBR = {min: -0.2, max: 1.0,
                 palette: ['#1D4ED8', '#F1F5F9', '#FDE047', '#F97316', '#7F1D1D']};

  Map.centerObject(ROI, 10);
  Map.setOptions('SATELLITE');
  Map.addLayer(nbrPre,  visNBR,  'MAPA 5b · NBR pre-incendio');
  Map.addLayer(nbrPost, visNBR,  'MAPA 6b · NBR post-incendio');
  Map.addLayer(dnbrSinAgua, visDNBR, 'MAPA 7 · dNBR (sin agua)');
  Map.addLayer(agua.selfMask(), {palette: ['#1D4ED8']}, 'Agua enmascarada', false);
  Map.addLayer(ee.Image().byte().paint(ee.FeatureCollection([ee.Feature(ROI)]), 1, 2),
               {palette: ['#00E5FF']}, 'Límite');

  // --- Histograma: la base para elegir el umbral ---------------------------
  // Si el evento dejó cicatriz, el histograma debe ser BIMODAL: una moda
  // grande cerca de 0 (todo lo que no se quemó) y una cola o segunda moda a
  // la derecha. Si sale una única campana centrada en 0, el dNBR no está
  // viendo nada y hay que revisar las escenas antes de seguir.
  print(ui.Chart.image.histogram({
    image: dnbrSinAgua, region: ROI, scale: 100, maxBuckets: 120
  }).setOptions({
    title: 'Distribución del dNBR en el municipio',
    hAxis: {title: 'dNBR'}, vAxis: {title: 'Píxeles'},
    colors: ['#B45309']
  }));

  print('Estadísticos del dNBR:', dnbrSinAgua.reduceRegion({
    reducer: ee.Reducer.percentile([1, 5, 25, 50, 75, 90, 95, 99])
               .combine(ee.Reducer.mean(), '', true)
               .combine(ee.Reducer.stdDev(), '', true),
    geometry: ROI, scale: 100, maxPixels: 1e10
  }));

  print('Píxeles válidos tras enmascarar nubes, sombras y agua:',
        dnbrSinAgua.mask().reduceRegion({
          reducer: ee.Reducer.mean(), geometry: ROI, scale: 100, maxPixels: 1e10}));
  print('  Si la cobertura válida es baja (<60 %), la cicatriz saldrá con');
  print('  huecos y el Recall se hundirá por falta de dato, no por el modelo.');
  print('  En ese caso vuelve al bloque 05 y busca otras escenas.');

  // --- Exportar ------------------------------------------------------------
  Export.image.toDrive({
    image: dnbrSinAgua.unmask(-9999).toFloat(),
    description: 'RBQ_dNBR',
    folder: 'SIPRO_validacion_RBQ',
    fileNamePrefix: 'rbq_dnbr',
    region: ROI, scale: ES_SENTINEL ? 20 : 30,
    crs: 'EPSG:4326', maxPixels: 1e10
  });

  Export.image.toDrive({
    image: nbrPre.addBands(nbrPost).unmask(-9999).toFloat(),
    description: 'RBQ_NBR_pre_post',
    folder: 'SIPRO_validacion_RBQ',
    fileNamePrefix: 'rbq_nbr_pre_post',
    region: ROI, scale: ES_SENTINEL ? 20 : 30,
    crs: 'EPSG:4326', maxPixels: 1e10
  });

  print('--- Siguiente: 07_Mascara_Quemada ---');
}
