"""Cicatriz observada (dNBR) desde Sentinel-2, sin GEE y sin registro.

Usa el catálogo público AWS Earth Search (STAC) y los COG de Sentinel-2 L2A
en S3. Los dos son abiertos: no hacen falta cuentas, ni claves, ni AppEEARS,
ni Earth Engine.

    pip install rasterio requests numpy shapely
    python f9b_dnbr_sentinel.py --zona rurrenabaque \
        --pre 2023-10-15 --post 2023-12-05

Descarga solo las bandas que hacen falta (B08 NIR, B12 SWIR2, SCL) y solo la
ventana del municipio, mediante peticiones de rango HTTP sobre el COG: son
unas decenas de MB, no la escena completa.

Salida: ../datos/observado_grid_<zona>.csv con observado, fraccion_quemada y
valido, listo para f12_metricas_validacion.py.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import date, timedelta
from pathlib import Path

STAC = "https://earth-search.aws.element84.com/v1/search"
COLECCION = "sentinel-2-l2a"

PASO = 0.0045
UMBRAL_DNBR = 0.27          # USGS: severidad moderada-baja
FRACCION_CELDA = 0.50       # mayoría simple de píxeles quemados
COBERTURA_MINIMA = 0.50     # píxeles con dato mínimos para fiarse de la celda

# SCL de Sentinel-2: clases que NO sirven.
#  0 sin dato · 1 saturado · 3 sombra de nube · 8 nube media · 9 nube alta
#  10 cirros · 11 nieve
SCL_MALAS = {0, 1, 3, 8, 9, 10, 11}

ZONAS = {
    "rurrenabaque": {
        "grid": "../datos/rurrenabaque_grid_500m.csv",
        "geojson": "../datos/Mun_RBQ_wgs84.geojson",
        "prefijo": "RBQ",
        "bbox": (-67.559663, -15.046462, -67.073719, -14.343298),
        "lat_origen": -14.3420, "lon_origen": -67.5630,
    },
    "apolo": {
        "grid": "../../backend_django/api/datos/grid.csv",
        "geojson": "../../../WCS-frontend/public/datos/apolo_limite.geojson",
        "prefijo": "APO",
        "bbox": (-68.998222, -14.999367, -68.003722, -14.000367),
        "lat_origen": -13.950867, "lon_origen": -69.115222,
    },
}


def buscar_escenas(bbox, desde, hasta, nubes_max, limite=40):
    import requests
    cuerpo = {
        "collections": [COLECCION],
        "bbox": list(bbox),
        "datetime": f"{desde}T00:00:00Z/{hasta}T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": nubes_max}},
        "limit": limite,
    }
    r = requests.post(STAC, json=cuerpo, timeout=90)
    r.raise_for_status()
    items = r.json().get("features", [])
    items.sort(key=lambda f: f["properties"].get("eo:cloud_cover", 100))
    return items


def leer_ventana(url, bbox):
    """Lee una banda recortada al bbox. Devuelve (array, transform, crs)."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    with rasterio.open(url) as src:
        limites = transform_bounds("EPSG:4326", src.crs, *bbox, densify_pts=21)
        v = from_bounds(*limites, transform=src.transform).round_offsets().round_lengths()
        datos = src.read(1, window=v)
        return datos, src.window_transform(v), src.crs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zona", choices=sorted(ZONAS), default="rurrenabaque")
    ap.add_argument("--pre", required=True, help="Fecha objetivo PRE-incendio AAAA-MM-DD")
    ap.add_argument("--post", required=True, help="Fecha objetivo POST-incendio AAAA-MM-DD")
    ap.add_argument("--margen", type=int, default=20,
                    help="Días de margen a cada lado para buscar escena limpia")
    ap.add_argument("--nubes", type=float, default=35.0, help="Nubosidad máxima (%%)")
    ap.add_argument("--umbral", type=float, default=UMBRAL_DNBR)
    ap.add_argument("--solo-buscar", action="store_true",
                    help="Lista las escenas disponibles y no descarga nada")
    ap.add_argument("--salida", default=None)
    ap.add_argument("--resultados", default="../resultados")
    args = ap.parse_args()

    try:
        import numpy as np
        import requests  # noqa: F401
    except ImportError:
        print("Falta alguna dependencia:")
        print("  pip install rasterio requests numpy shapely")
        return 2

    z = ZONAS[args.zona]
    bbox = z["bbox"]
    lat0, lon0 = z["lat_origen"], z["lon_origen"]

    print("=" * 74)
    print(f"dNBR SOBRE SENTINEL-2 — {args.zona.upper()}")
    print("=" * 74)
    print(f"Catálogo: AWS Earth Search (público, sin registro)")
    print(f"BBox    : {bbox}")

    # --- Buscar escenas -----------------------------------------------------
    m = timedelta(days=args.margen)
    f_pre, f_post = date.fromisoformat(args.pre), date.fromisoformat(args.post)

    print(f"\nBuscando escenas con menos del {args.nubes:.0f} % de nubes…")
    try:
        pre = buscar_escenas(bbox, f_pre - m, f_pre + m, args.nubes)
        post = buscar_escenas(bbox, f_post - m, f_post + m, args.nubes)
    except Exception as e:
        print(f"\nNo se pudo consultar el catálogo: {type(e).__name__}: {e}")
        print("Comprueba que tienes salida a internet.")
        return 3

    for etiqueta, lista in (("PRE", pre), ("POST", post)):
        print(f"\n{etiqueta} — {len(lista)} escena(s):")
        for f in lista[:6]:
            p = f["properties"]
            print(f"  {p['datetime'][:10]}  nubes {p.get('eo:cloud_cover', '?'):>5.1f} %"
                  f"  {f['id']}")

    if not pre or not post:
        print("\nNo hay escenas suficientemente limpias en esas ventanas.")
        print("Prueba a subir --margen o --nubes, o cambia las fechas.")
        print("\nEn esta zona (piedemonte amazónico) la nubosidad en temporada")
        print("de quemas es alta: puede que no haya ninguna escena útil y haya")
        print("que usar otro producto.")
        return 4

    if args.solo_buscar:
        print("\n(--solo-buscar: no se descarga nada)")
        return 0

    e_pre, e_post = pre[0], post[0]
    print(f"\nElegidas:")
    print(f"  PRE : {e_pre['properties']['datetime'][:10]}  "
          f"{e_pre['properties'].get('eo:cloud_cover', '?'):.1f} % nubes")
    print(f"  POST: {e_post['properties']['datetime'][:10]}  "
          f"{e_post['properties'].get('eo:cloud_cover', '?'):.1f} % nubes")

    # --- Descargar bandas y calcular dNBR -----------------------------------
    def bandas(item):
        a = item["assets"]
        # Earth Search nombra las bandas de varias formas según la versión.
        def url(*nombres):
            for n in nombres:
                if n in a:
                    return a[n]["href"]
            raise KeyError(f"no encuentro ninguna de {nombres}")
        return url("nir", "B08"), url("swir22", "B12"), url("scl", "SCL")

    print("\nDescargando las ventanas (solo NIR, SWIR2 y SCL)…")
    try:
        u_nir_a, u_swir_a, u_scl_a = bandas(e_pre)
        u_nir_b, u_swir_b, u_scl_b = bandas(e_post)
        nir_a, tr, crs = leer_ventana(u_nir_a, bbox)
        swir_a, _, _ = leer_ventana(u_swir_a, bbox)
        scl_a, _, _ = leer_ventana(u_scl_a, bbox)
        nir_b, _, _ = leer_ventana(u_nir_b, bbox)
        swir_b, _, _ = leer_ventana(u_swir_b, bbox)
        scl_b, _, _ = leer_ventana(u_scl_b, bbox)
    except Exception as e:
        print(f"\nFallo al leer las bandas: {type(e).__name__}: {e}")
        return 5

    print(f"  ventana: {nir_a.shape[1]} × {nir_a.shape[0]} px · {crs}")

    forma = min(nir_a.shape, nir_b.shape, scl_a.shape, scl_b.shape,
                key=lambda s: s[0] * s[1])
    rec = lambda x: x[:forma[0], :forma[1]].astype("float32")
    nir_a, swir_a, nir_b, swir_b = map(rec, (nir_a, swir_a, nir_b, swir_b))
    scl_a = scl_a[:forma[0], :forma[1]]
    scl_b = scl_b[:forma[0], :forma[1]]

    import numpy as np
    valido_px = ~(np.isin(scl_a, list(SCL_MALAS)) | np.isin(scl_b, list(SCL_MALAS)))

    def nbr(nir, swir):
        d = nir + swir
        return np.where(d != 0, (nir - swir) / np.where(d == 0, 1, d), np.nan)

    dnbr = nbr(nir_a, swir_a) - nbr(nir_b, swir_b)
    dnbr = np.where(valido_px, dnbr, np.nan)

    quemado = (dnbr >= args.umbral) & valido_px
    n_val = int(valido_px.sum())
    print(f"  píxeles válidos: {n_val:,} de {valido_px.size:,} "
          f"({n_val/valido_px.size*100:.1f} %)")
    print(f"  píxeles quemados (dNBR ≥ {args.umbral}): {int(quemado.sum()):,}")
    if n_val / valido_px.size < 0.5:
        print("\n  Menos de la mitad de los píxeles sirven. Las métricas van a")
        print("  perder representatividad: busca otras escenas o declárarlo.")

    # --- Agregar al grid de 500 m -------------------------------------------
    from rasterio.transform import xy
    from rasterio.warp import transform as warp

    filas_i, cols_i = np.nonzero(np.ones(forma, dtype=bool))
    xs, ys = xy(tr, filas_i, cols_i)
    if crs.to_epsg() != 4326:
        xs, ys = warp(crs, "EPSG:4326", xs, ys)

    celdas: dict[tuple[int, int], dict] = {}
    q = quemado.ravel(); v = valido_px.ravel()
    for lon, lat, qi, vi in zip(xs, ys, q, v):
        f = round((lat0 - lat) / PASO)
        c = round((lon - lon0) / PASO)
        d = celdas.setdefault((f, c), {"q": 0, "v": 0, "n": 0})
        d["n"] += 1
        if vi:
            d["v"] += 1
            if qi:
                d["q"] += 1

    dentro = None
    rg = Path(z["geojson"])
    if rg.exists():
        try:
            from shapely.geometry import Point, shape
            from shapely.prepared import prep
            fc = json.loads(rg.read_text(encoding="utf-8"))
            g = fc["features"][0]["geometry"] if fc.get("features") else fc
            dentro = prep(shape(g))
        except Exception:
            pass

    salida = Path(args.salida or f"../datos/observado_grid_{args.zona}.csv")
    salida.parent.mkdir(parents=True, exist_ok=True)
    n_obs = n_ok = n_fuera = 0
    with open(salida, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "fila", "columna", "lat", "lon",
                    "observado", "fraccion_quemada", "valido", "cobertura"])
        for (f, c), d in sorted(celdas.items()):
            lat = lat0 - f * PASO
            lon = lon0 + c * PASO
            if dentro is not None and not dentro.contains(Point(lon, lat)):
                n_fuera += 1
                continue
            cobertura = d["v"] / d["n"] if d["n"] else 0.0
            valido = 1 if cobertura >= COBERTURA_MINIMA else 0
            fraccion = (d["q"] / d["v"]) if d["v"] else 0.0
            observado = 1 if (valido and fraccion >= FRACCION_CELDA) else 0
            n_obs += observado
            n_ok += valido
            w.writerow([f"{z['prefijo']}-{f:03d}-{c:03d}", f, c,
                        round(lat, 6), round(lon, 6),
                        observado, round(fraccion, 4), valido, round(cobertura, 4)])

    print(f"\nCeldas escritas       : {len(celdas) - n_fuera:,}")
    print(f"Con observación válida: {n_ok:,}")
    print(f"Quemadas observadas   : {n_obs:,}  →  {n_obs * 0.25:.2f} km²")

    res = Path(args.resultados)
    res.mkdir(parents=True, exist_ok=True)
    (res / f"f9b_dnbr_{args.zona}.json").write_text(json.dumps({
        "zona": args.zona,
        "fuente": "Sentinel-2 L2A · AWS Earth Search (STAC público)",
        "escena_pre": {"id": e_pre["id"],
                       "fecha": e_pre["properties"]["datetime"][:10],
                       "nubes_pct": e_pre["properties"].get("eo:cloud_cover")},
        "escena_post": {"id": e_post["id"],
                        "fecha": e_post["properties"]["datetime"][:10],
                        "nubes_pct": e_post["properties"].get("eo:cloud_cover")},
        "umbral_dnbr": args.umbral,
        "fraccion_celda": FRACCION_CELDA,
        "cobertura_minima": COBERTURA_MINIMA,
        "resolucion_sensor_m": 20,
        "resolucion_grid_m": 500,
        "pixeles_validos_pct": round(n_val / valido_px.size * 100, 1),
        "celdas": len(celdas) - n_fuera,
        "celdas_validas": n_ok,
        "celdas_quemadas": n_obs,
        "area_observada_km2": round(n_obs * 0.25, 3),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nEscrito: {salida}")
    print(f"Escrito: {res / f'f9b_dnbr_{args.zona}.json'}")
    print(f"\nSiguiente: python f12_metricas_validacion.py --observado {salida}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
