"""Construye el grid de 500 m de una zona con las variables que lee automata.py.

Sin Google Earth Engine. Tres fuentes, todas públicas:

    geometría      registro de zonas (validacion/zonas.py)
    elevación      Open-Meteo Elevation API · sin clave
    pendiente      derivada del DEM por diferencias finitas
    NDVI           Sentinel-2 L2A en AWS · COG público, sin registro
    humedad        ERA5-Land vía Open-Meteo Archive · sin clave
    viento u/v     ERA5-Land vía Open-Meteo Archive · sin clave

    pip install requests numpy rasterio shapely pyproj

    python f8_construir_grid.py --zona rurrenabaque \
        --desde 2023-11-16 --hasta 2023-11-20

Salida: ../datos/<zona>_grid_500m.csv con las once columnas obligatorias.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "validacion"))

ELEVACION = "https://api.open-meteo.com/v1/elevation"
OPENTOPODATA = "https://api.opentopodata.org/v1/srtm30m"   # 100 puntos/petición, 1 pet./s
ARCHIVO = "https://archive-api.open-meteo.com/v1/archive"
STAC = "https://earth-search.aws.element84.com/v1/search"

LOTE_ELEVACION = 100
LOTE_ERA5 = 1            # ERA5 acepta un punto por petición en este endpoint
PAUSA = 0.35


def celdas_de(zona, poligono=True):
    """Las celdas de la zona, recortadas al polígono municipal."""
    salida = []
    for f in range(zona.filas + 1):
        for c in range(zona.columnas + 1):
            lat, lon = zona.coordenada(f, c)
            if poligono and not zona.contiene(lat, lon):
                continue
            salida.append({"id": zona.id_celda(f, c), "fila": f, "columna": c,
                           "lat": round(lat, 6), "lon": round(lon, 6)})
    return salida


# ---------------------------------------------------------------------------
def traer_elevacion(celdas, fuente="opentopodata", cache_ruta=None):
    """Elevación con caché en disco, reintentos y reanudación.

    fuente = "opentopodata"  SRTM 30 m (api.opentopodata.org). Recomendado:
                             cuota aparte de Open-Meteo, ~105 peticiones.
    fuente = "openmeteo"     Copernicus GLO-90 vía Open-Meteo. Se agota con
                             facilidad (429) al pedir 10.000 puntos.

    Una sola fuente para todo el grid: mezclar dos DEM crea escalones
    artificiales de pendiente en la costura. Cada fuente tiene su caché.
    """
    import requests
    if fuente == "openmeteo":
        url_base, lote_n, pausa = ELEVACION, LOTE_ELEVACION, 1.0
        cache_ruta = Path(cache_ruta or "../datos/elevacion_cache.json")
    else:
        url_base, lote_n, pausa = OPENTOPODATA, 100, 1.1
        cache_ruta = Path(cache_ruta or "../datos/elevacion_cache_srtm30m.json")
    cache = {}
    if cache_ruta.exists():
        cache = json.loads(cache_ruta.read_text(encoding="utf-8"))

    def clave(c):
        return f"{c['lat']:.5f},{c['lon']:.5f}"

    for c in celdas:
        c.pop("elevacion_m", None)
        if cache.get(clave(c)) is not None:
            c["elevacion_m"] = cache[clave(c)]
    pendientes = [c for c in celdas if "elevacion_m" not in c]
    print(f"  elevación ({fuente}): {len(celdas) - len(pendientes):,} en caché, "
          f"{len(pendientes):,} por pedir (lotes de {lote_n})")

    for i in range(0, len(pendientes), lote_n):
        lote = pendientes[i:i + lote_n]
        if fuente == "openmeteo":
            lats = ",".join(f"{c['lat']:.5f}" for c in lote)
            lons = ",".join(f"{c['lon']:.5f}" for c in lote)
            url = f"{url_base}?latitude={lats}&longitude={lons}"
        else:
            locs = "|".join(f"{c['lat']:.5f},{c['lon']:.5f}" for c in lote)
            url = f"{url_base}?locations={locs}"
        espera = 20
        for intento in range(6):
            r = requests.get(url, timeout=90)
            if r.status_code != 429:
                break
            print(f"    429: espero {espera} s (intento {intento + 1}/6)")
            time.sleep(espera)
            espera = min(espera * 2, 300)
        if r.status_code == 429:
            cache_ruta.write_text(json.dumps(cache), encoding="utf-8")
            otra = "openmeteo" if fuente == "opentopodata" else "opentopodata"
            raise RuntimeError(f"{fuente} sigue en 429 (cuota agotada). Lo descargado "
                               f"quedó en caché: vuelve a correr más tarde, o usa "
                               f"--dem-fuente {otra}.")
        r.raise_for_status()
        if fuente == "openmeteo":
            alturas = r.json().get("elevation") or []
        else:
            alturas = [x.get("elevation") for x in r.json().get("results", [])]
        for c, h in zip(lote, alturas):
            if h is None:
                continue
            c["elevacion_m"] = h
            cache[clave(c)] = h
        cache_ruta.write_text(json.dumps(cache), encoding="utf-8")
        if (i // lote_n) % 10 == 0:
            print(f"    {min(i + lote_n, len(pendientes)):,}/{len(pendientes):,}")
        time.sleep(pausa)


def calcular_pendiente(celdas, zona):
    """Pendiente por diferencias finitas sobre los vecinos ortogonales.

    Se usa la pendiente máxima entre los dos ejes, que es la que gobierna el
    avance del frente. Las celdas de borde, sin vecino a un lado, usan la
    diferencia disponible.
    """
    ns, eo = zona.tam_celda_m()
    ix = {(c["fila"], c["columna"]): c for c in celdas}
    sin_dem = 0
    for c in celdas:
        h = c.get("elevacion_m")
        if h is None:
            c["pendiente_grados"] = None
            sin_dem += 1
            continue
        f, col = c["fila"], c["columna"]

        def dh(df, dc, dist):
            a = ix.get((f + df, col + dc))
            b = ix.get((f - df, col - dc))
            ha = a.get("elevacion_m") if a else None
            hb = b.get("elevacion_m") if b else None
            if ha is not None and hb is not None:
                return abs(ha - hb) / (2 * dist)
            if ha is not None:
                return abs(ha - h) / dist
            if hb is not None:
                return abs(hb - h) / dist
            return 0.0

        g = max(dh(1, 0, ns), dh(0, 1, eo))
        c["pendiente_grados"] = round(math.degrees(math.atan(g)), 2)
    return sin_dem


# ---------------------------------------------------------------------------
def traer_meteo(celdas, zona, desde, hasta, submuestreo):
    """Humedad de suelo y viento del período, de ERA5-Land.

    ERA5 tiene ~9 km de resolución, así que pedir un punto por celda de 500 m
    gastaría 10.000 peticiones para obtener el mismo valor repetido. Se
    consulta una rejilla gruesa y se asigna a cada celda el punto más cercano.
    """
    import requests

    lats = sorted({round(c["lat"] / submuestreo) * submuestreo for c in celdas})
    lons = sorted({round(c["lon"] / submuestreo) * submuestreo for c in celdas})
    puntos = [(la, lo) for la in lats for lo in lons
              if any(abs(c["lat"] - la) <= submuestreo
                     and abs(c["lon"] - lo) <= submuestreo for c in celdas)]
    print(f"  meteorología: {len(puntos)} puntos ERA5 "
          f"(rejilla de {submuestreo}° ≈ {submuestreo * 111:.0f} km)")

    valores = {}
    for i, (la, lo) in enumerate(puntos, 1):
        url = (f"{ARCHIVO}?latitude={la:.4f}&longitude={lo:.4f}"
               f"&start_date={desde}&end_date={hasta}"
               f"&hourly=soil_moisture_0_to_7cm,wind_speed_10m,wind_direction_10m"
               f"&timezone=UTC")
        try:
            r = requests.get(url, timeout=90)
            r.raise_for_status()
            h = r.json().get("hourly") or {}
            hum = [x for x in (h.get("soil_moisture_0_to_7cm") or []) if x is not None]
            vel = [x for x in (h.get("wind_speed_10m") or []) if x is not None]
            dirs = [x for x in (h.get("wind_direction_10m") or []) if x is not None]
            if not hum or not vel:
                continue
            # El viento se promedia como VECTOR: promediar módulos daría una
            # velocidad correcta pero perdería la dirección, que es lo que
            # gobierna hacia dónde avanza el frente.
            u = sum(-v / 3.6 * math.sin(math.radians(d))
                    for v, d in zip(vel, dirs)) / len(vel)
            vv = sum(-v / 3.6 * math.cos(math.radians(d))
                     for v, d in zip(vel, dirs)) / len(vel)
            valores[(la, lo)] = {"humedad": sum(hum) / len(hum),
                                 "viento_u": u, "viento_v": vv}
        except Exception as e:  # noqa: BLE001
            print(f"    punto {i}/{len(puntos)} falló: {type(e).__name__}")
        if i % 5 == 0:
            print(f"    {i}/{len(puntos)}")
        time.sleep(PAUSA)

    if not valores:
        return 0
    for c in celdas:
        k = min(valores, key=lambda p: (p[0] - c["lat"]) ** 2 + (p[1] - c["lon"]) ** 2)
        v = valores[k]
        c["humedad"] = round(v["humedad"], 4)
        c["viento_u"] = round(v["viento_u"], 4)
        c["viento_v"] = round(v["viento_v"], 4)
    return len(valores)


# ---------------------------------------------------------------------------
def traer_ndvi(celdas, zona, antes_de, margen, nubes_max, Z_PASO=0.0045, factor=1):
    """NDVI como mosaico de varias escenas Sentinel-2 ANTERIORES al evento.

    Una sola escena cubre una fracción del municipio (el municipio cruza varias
    teselas MGRS). Se recorren las escenas de menos a más nubes y cada celda
    toma el valor de la primera escena que la cubre con píxeles válidos.
    Posterior al evento no sirve: se metería la respuesta en la pregunta.
    """
    import numpy as np
    import rasterio
    import requests
    from datetime import timedelta
    from rasterio.transform import xy
    from rasterio.warp import transform as warp, transform_bounds
    from rasterio.windows import from_bounds

    MAX_ESCENAS = 25
    d = date.fromisoformat(antes_de)
    cuerpo = {"collections": ["sentinel-2-l2a"], "bbox": list(zona.bbox),
              "datetime": f"{d - timedelta(days=margen)}T00:00:00Z/{d - timedelta(days=1)}T23:59:59Z",
              "query": {"eo:cloud_cover": {"lt": nubes_max}}, "limit": 100}
    r = requests.post(STAC, json=cuerpo, timeout=90)
    r.raise_for_status()
    items = sorted(r.json().get("features", []),
                   key=lambda f: f["properties"].get("eo:cloud_cover", 100))
    if not items:
        print(f"  NDVI: no hay escena con menos del {nubes_max:.0f} % de nubes "
              f"en los {margen} días previos.")
        return None
    print(f"  NDVI: {len(items)} escenas candidatas; se usan hasta {MAX_ESCENAS}")

    from rasterio.enums import Resampling
    from rasterio.windows import Window

    # CORRECCIÓN: antes NIR y rojo (10 m) y SCL (20 m) se leían cada uno a su
    # resolución y luego se recortaban a la forma MENOR. Resultado: solo se
    # usaba el cuadrante noroeste de NIR/rojo, y la máscara de nubes quedaba
    # desplazada respecto de los píxeles que enmascaraba. Por eso casi todas
    # las escenas aportaban «+0 celdas». Ahora las tres bandas se leen sobre
    # la MISMA ventana geográfica y la misma rejilla de 20 m, y la ventana se
    # recorta a la extensión real de la tesela.
    def leer_scl(u):
        with rasterio.open(u) as src:
            lim = transform_bounds("EPSG:4326", src.crs, *zona.bbox, densify_pts=21)
            w = from_bounds(*lim, transform=src.transform)
            w = w.intersection(Window(0, 0, src.width, src.height))
            w = w.round_offsets().round_lengths()
            if factor > 1:
                # Lectura submuestreada (p. ej. 60 m) para zonas grandes
                forma = (max(int(w.height) // factor, 1), max(int(w.width) // factor, 1))
                datos = src.read(1, window=w, out_shape=forma, resampling=Resampling.nearest)
                from affine import Affine
                t0 = src.window_transform(w)
                tr = Affine(t0.a * w.width / forma[1], t0.b, t0.c, t0.d, t0.e * w.height / forma[0], t0.f)
                return datos, tr, src.crs, src.window_bounds(w)
            return src.read(1, window=w), src.window_transform(w), src.crs, \
                src.window_bounds(w)

    def leer_como(u, limites, forma):
        with rasterio.open(u) as src:
            w = from_bounds(*limites, transform=src.transform)
            return src.read(1, window=w, out_shape=forma,
                            resampling=Resampling.average).astype("float32")

    def asset(a, *n):
        for x in n:
            if x in a:
                return a[x]["href"]
        raise KeyError(n)

    ids_grid = {(c["fila"], c["columna"]) for c in celdas}
    suma: dict[tuple[int, int], float] = {}
    llenas: dict[tuple[int, int], float] = {}
    usadas = []
    for it in items[:MAX_ESCENAS]:
        if len(llenas) >= len(celdas):
            break
        try:
            a = it["assets"]
            scl, tr, crs, lim = leer_scl(asset(a, "scl", "SCL"))
            if min(scl.shape) == 0:
                continue
            nir = leer_como(asset(a, "nir", "B08"), lim, scl.shape)
            red = leer_como(asset(a, "red", "B04"), lim, scl.shape)
        except Exception as e:  # noqa: BLE001  (ventana fuera de la escena, red, etc.)
            print(f"    {it['id']}: omitida ({type(e).__name__})")
            continue
        p = it["properties"]
        # Línea base ≥ 04.00 (ene-2022) trae un desplazamiento de −1000 en
        # reflectancia. Earth Search lo corrige y lo marca; si no, se aplica.
        off = 1000.0 if (float(p.get("s2:processing_baseline") or 0) >= 4.0
                         and p.get("earthsearch:boa_offset_applied") is False) else 0.0
        sin_dato = (nir == 0) & (red == 0)
        nir, red = nir - off, red - off
        den = nir + red
        ndvi = np.where(den > 0, (nir - red) / np.where(den > 0, den, 1), np.nan)
        # SCL: 0 sin dato, 1 saturado, 3 sombra de nube, 8-10 nube/cirro, 11 nieve
        ndvi = np.where(np.isin(scl, [0, 1, 3, 8, 9, 10, 11]) | sin_dato, np.nan, ndvi)

        fi, ci = np.nonzero(np.isfinite(ndvi))
        if not len(fi):
            continue
        xs, ys = xy(tr, fi, ci)
        if crs.to_epsg() != 4326:
            xs, ys = warp(crs, "EPSG:4326", xs, ys)
        xs, ys = np.asarray(xs), np.asarray(ys)
        filas_ = np.round((zona.lat_origen - ys) / Z_PASO).astype(np.int64)
        cols_ = np.round((xs - zona.lon_origen) / Z_PASO).astype(np.int64)
        vals = ndvi[fi, ci]
        clave = filas_ * 100000 + cols_
        orden = np.argsort(clave)
        clave, vals = clave[orden], vals[orden]
        uniq, inicio, cuenta = np.unique(clave, return_index=True, return_counts=True)
        sumas = np.add.reduceat(vals, inicio)
        nuevas = 0
        for k, s_, n_ in zip(uniq.tolist(), sumas.tolist(), cuenta.tolist()):
            key = (k // 100000, k % 100000)
            # al menos 25 % de los ~600 píxeles de 20 m de la celda
            if key in llenas or key not in ids_grid or n_ < max(150 // (factor * factor), 8):
                continue
            llenas[key] = s_ / n_
            nuevas += 1
        usadas.append(it)
        print(f"    {it['id']} · {p['datetime'][:10]} · "
              f"{p.get('eo:cloud_cover', 0):.1f} % nubes · +{nuevas:,} celdas")

    n = 0
    for c in celdas:
        v = llenas.get((c["fila"], c["columna"]))
        if v is not None:
            c["ndvi"] = round(v, 4)
            n += 1
    if not usadas:
        return None
    fechas = sorted(u["properties"]["datetime"][:10] for u in usadas)
    return {"escena": "mosaico de " + ", ".join(u["id"] for u in usadas),
            "fecha": f"{fechas[0]}..{fechas[-1]}",
            "nubes_pct": sum(u["properties"].get("eo:cloud_cover", 0) for u in usadas) / len(usadas),
            "celdas_con_ndvi": n}


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zona", required=True)
    ap.add_argument("--desde", required=True, help="Inicio del evento AAAA-MM-DD")
    ap.add_argument("--hasta", required=True, help="Fin del evento AAAA-MM-DD")
    ap.add_argument("--margen-ndvi", type=int, default=45)
    ap.add_argument("--nubes", type=float, default=40.0)
    ap.add_argument("--submuestreo", type=float, default=0.08,
                    help="Rejilla de consulta a ERA5, en grados")
    ap.add_argument("--salida", default=None)
    ap.add_argument("--dem-fuente", choices=["opentopodata", "openmeteo"],
                    default="opentopodata",
                    help="Fuente del DEM (una sola para todo el grid)")
    ap.add_argument("--sin-ndvi", action="store_true",
                    help="Construye el grid sin NDVI. El motor lo necesita: "
                         "el grid quedaría incompleto y hay que declararlo.")
    args = ap.parse_args()

    try:
        import zonas as Z
    except ImportError:
        # Sin sipro_consola_monitoreo/validacion/zonas.py (carpeta copiada a
        # otro sitio): se usa el sustituto local, que toma las celdas del
        # grid ya existente y el límite del GeoJSON de esta carpeta.
        import zona_local as Z
        print("(validacion/zonas.py no encontrado: uso python/zona_local.py)")
    try:
        zona = Z.cargar(args.zona)
    except KeyError as e:
        print(e)
        return 2

    print("=" * 74)
    print(f"GRID DE {zona.etiqueta.upper()} · evento {args.desde} a {args.hasta}")
    print("=" * 74)
    ns, eo = zona.tam_celda_m()
    print(f"Municipio : {zona.area_km2:,.1f} km²")
    print(f"Celda     : {ns:.0f} × {eo:.0f} m · paso {Z.PASO}°")
    print(f"Origen    : {zona.lat_origen:.6f}, {zona.lon_origen:.6f}")
    print(f"Orientación: fila → SUR · columna → ESTE\n")

    celdas = celdas_de(zona)
    print(f"Celdas dentro del municipio: {len(celdas):,}\n")

    resumen = {"zona": args.zona, "evento": {"desde": args.desde, "hasta": args.hasta},
               "celdas": len(celdas), "fuentes": {}}
    faltan = []

    print("1. Topografía")
    try:
        traer_elevacion(celdas, args.dem_fuente)
        sin_dem = calcular_pendiente(celdas, zona)
        con = len(celdas) - sin_dem
        print(f"   elevación y pendiente en {con:,} celdas")
        if sin_dem:
            faltan.append(f"elevacion_m en {sin_dem} celdas")
        resumen["fuentes"]["elevacion"] = (
            "SRTM 30 m vía OpenTopoData" if args.dem_fuente == "opentopodata"
            else "Copernicus GLO-90 vía Open-Meteo Elevation API")
        resumen["fuentes"]["pendiente"] = "derivada del DEM por diferencias finitas"
    except Exception as e:  # noqa: BLE001
        print(f"   FALLÓ: {type(e).__name__}: {e}")
        faltan.append("elevacion_m y pendiente_grados")

    print("\n2. Meteorología del evento")
    try:
        n = traer_meteo(celdas, zona, args.desde, args.hasta, args.submuestreo)
        print(f"   {n} punto(s) ERA5 asignados a las celdas")
        resumen["fuentes"]["humedad"] = ("ERA5-Land soil_moisture_0_to_7cm "
                                         "(m³/m³), media del evento")
        resumen["fuentes"]["viento"] = "ERA5-Land 10 m, promedio vectorial u/v"
        if not n:
            faltan.append("humedad y viento")
    except Exception as e:  # noqa: BLE001
        print(f"   FALLÓ: {type(e).__name__}: {e}")
        faltan.append("humedad y viento")

    print("\n3. Vegetación")
    if args.sin_ndvi:
        print("   omitido por --sin-ndvi")
        faltan.append("ndvi")
    else:
        try:
            info = traer_ndvi(celdas, zona, args.desde, args.margen_ndvi, args.nubes,
                              Z_PASO=Z.PASO)
            if info:
                print(f"   NDVI en {info['celdas_con_ndvi']:,} celdas")
                resumen["fuentes"]["ndvi"] = (
                    f"Sentinel-2 L2A {info['fecha']} vía AWS · "
                    f"{info['nubes_pct']:.1f} % nubes · escena {info['escena']}")
                if info["celdas_con_ndvi"] < len(celdas) * 0.5:
                    print("   ATENCIÓN: menos de la mitad de las celdas tienen NDVI.")
            else:
                faltan.append("ndvi")
        except Exception as e:  # noqa: BLE001
            print(f"   FALLÓ: {type(e).__name__}: {e}")
            faltan.append("ndvi")

    # --- Escribir -----------------------------------------------------------
    COLS = ["id", "fila", "columna", "lat", "lon", "pendiente_grados",
            "ndvi", "humedad", "viento_u", "viento_v", "prob_ignicion",
            "elevacion_m"]
    salida = Path(args.salida or f"../datos/{args.zona}_grid_500m.csv")
    salida.parent.mkdir(parents=True, exist_ok=True)
    completas = 0
    with open(salida, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        for c in celdas:
            # XGBoost se entrenó con Apolo. Aplicarlo aquí sería extrapolarlo
            # fuera de su dominio, así que la columna va a 0: valor neutro y
            # documentado. El foco inicial vendrá de FIRMS, que es observación.
            c["prob_ignicion"] = 0
            if all(c.get(k) is not None
                   for k in ("pendiente_grados", "ndvi", "humedad",
                             "viento_u", "viento_v")):
                completas += 1
            w.writerow(c)

    resumen["celdas_completas"] = completas
    resumen["variables_faltantes"] = faltan
    resumen["_nota_prob_ignicion"] = (
        "0 en todas las celdas: XGBoost se entrenó con Apolo y no se aplica aquí.")
    res = Path("../resultados")
    res.mkdir(parents=True, exist_ok=True)
    (res / f"f8_grid_{args.zona}.json").write_text(
        json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "-" * 74)
    print(f"Celdas escritas          : {len(celdas):,}")
    print(f"Con TODAS las variables  : {completas:,} "
          f"({completas / max(len(celdas), 1) * 100:.1f} %)")
    if faltan:
        print(f"\nFALTAN: {', '.join(faltan)}")
        print("El grid está incompleto. El motor no puede correr sin NDVI,")
        print("humedad ni viento: comprueba qué falló arriba.")
    print(f"\nEscrito: {salida}")
    print(f"Escrito: {res / f'f8_grid_{args.zona}.json'}")
    if not faltan:
        print(f"\nSiguiente: python f10_verificar_grid_rbq.py --grid {salida}")
    print("=" * 74)
    return 0 if not faltan else 1


if __name__ == "__main__":
    raise SystemExit(main())
