"""Cicatriz de quema real desde MapBiomas Fire, llevada al grid del autómata.

    pip install rasterio numpy scipy shapely

    # Inventario: qué hay en la carpeta y cuánto quemó cada año en la zona
    python f9c_cicatriz_mapbiomas.py --carpeta ../datos/cicatrices --zona rurrenabaque --inventario

    # Manchas separadas de un año
    python f9c_cicatriz_mapbiomas.py --carpeta ... --zona rurrenabaque --anio 2023 --manchas

    # Una mancha concreta, llevada al grid de 500 m
    python f9c_cicatriz_mapbiomas.py --carpeta ... --zona rurrenabaque --anio 2023 --mancha 21 --salida ../eventos/evento_01

La cicatriz es REFERENCIA, nunca entrada del autómata: se usa después de
simular, jamás para decidir qué celda arde.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "validacion"))

# Regla raster → celda. Una celda de 500 m contiene ~278 píxeles de 30 m.
# Mayoría simple: neutral entre Recall y Precision. Un umbral bajo marcaría
# como quemada cualquier celda rozada por el fuego e inflaría el Recall; uno
# alto haría lo contrario. Se guarda también la fracción exacta, así que el
# umbral se puede revisar sin volver a procesar el ráster.
UMBRAL_CELDA = 0.50


def rasteres(carpeta: Path) -> dict[str, Path]:
    salida = {}
    for p in sorted(carpeta.glob("*.img")) + sorted(carpeta.glob("*.tif")):
        for t in p.stem.replace("-", "_").split("_"):
            if t.isdigit() and len(t) == 4 and 1980 < int(t) < 2100:
                salida[t] = p
                break
    return salida


def abrir(ruta: Path, zona):
    """Lee la ventana de la zona y devuelve (quemado, transform, area_px_km2)."""
    import numpy as np
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds

    with rasterio.open(ruta) as s:
        if s.crs and s.crs.to_epsg() != 4326:
            raise SystemExit(f"{ruta.name} está en {s.crs}. Se esperaba EPSG:4326.")
        w = from_bounds(*zona.bbox, transform=s.transform).round_offsets().round_lengths()
        a = s.read(1, window=w)
        tr = s.window_transform(w)
        import math
        lat = zona.centroide[0]
        apx = (abs(s.transform.a) * 111320 * math.cos(math.radians(lat))) \
            * (abs(s.transform.e) * 110574) / 1e6

    geo = json.loads(zona.limite.read_text(encoding="utf-8"))
    g = geo["features"][0]["geometry"] if geo.get("features") else geo
    dentro = ~geometry_mask([g], out_shape=a.shape, transform=tr, invert=False)
    return (a == 1) & dentro, tr, apx


def componentes(quemado):
    import numpy as np
    from scipy import ndimage
    etq, n = ndimage.label(quemado, structure=np.ones((3, 3)))
    tam = ndimage.sum(quemado, etq, range(1, n + 1)) if n else np.array([])
    return etq, tam


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--carpeta", required=True)
    ap.add_argument("--zona", required=True)
    ap.add_argument("--anio", default=None)
    ap.add_argument("--mancha", type=int, default=None,
                    help="Etiqueta de la mancha, del listado --manchas")
    ap.add_argument("--cerca-de", default=None,
                    help="lat,lon — elige la mancha más próxima a ese punto")
    ap.add_argument("--min-km2", type=float, default=1.0)
    ap.add_argument("--umbral", type=float, default=UMBRAL_CELDA)
    ap.add_argument("--inventario", action="store_true")
    ap.add_argument("--manchas", action="store_true")
    ap.add_argument("--salida", default=None)
    args = ap.parse_args()

    try:
        import numpy as np
        import zonas as Z
    except ImportError as e:
        print(f"Falta una dependencia: {e}")
        print("  pip install rasterio numpy scipy shapely pyproj")
        return 2

    zona = Z.cargar(args.zona)
    carpeta = Path(args.carpeta)
    if not carpeta.exists():
        print(f"No existe {carpeta}")
        return 1
    disponibles = rasteres(carpeta)
    if not disponibles:
        print(f"No hay .img ni .tif en {carpeta}")
        return 1

    # --- Inventario ---------------------------------------------------------
    if args.inventario or not args.anio:
        print("=" * 72)
        print(f"CICATRICES DISPONIBLES · {zona.etiqueta}")
        print("=" * 72)
        print(f"Carpeta : {carpeta}")
        print(f"Años    : {', '.join(sorted(disponibles))}")
        print(f"\n{'AÑO':>6}{'km² QUEMADOS':>15}{'MANCHAS':>10}{'MAYOR km²':>12}"
              f"{'celdas':>9}{'>=' + str(args.min_km2) + 'km²':>10}")
        print("-" * 72)
        for anio in sorted(disponibles):
            q, tr, apx = abrir(disponibles[anio], zona)
            etq, tam = componentes(q)
            mx = float(tam.max() * apx) if len(tam) else 0.0
            print(f"{anio:>6}{q.sum() * apx:>15.1f}{len(tam):>10,}{mx:>12.2f}"
                  f"{mx / 0.25:>9.0f}{int((tam * apx >= args.min_km2).sum()):>10}")
        print("-" * 72)
        print("\nUna celda del autómata son 0,25 km². La columna 'celdas' dice")
        print("cuántas ocuparía la mancha mayor: por debajo de unas 20 celdas,")
        print("las métricas se calculan sobre muy pocos datos.")
        if not args.anio:
            print(f"\nSiguiente: --anio AAAA --manchas")
            return 0

    q, tr, apx = abrir(disponibles[args.anio], zona)
    etq, tam = componentes(q)
    orden = np.argsort(tam)[::-1] if len(tam) else []

    # --- Listado de manchas -------------------------------------------------
    if args.manchas:
        from rasterio.transform import xy
        import math
        print("=" * 72)
        print(f"MANCHAS QUEMADAS · {zona.etiqueta} {args.anio}")
        print("=" * 72)
        print(f"Total quemado : {q.sum() * apx:,.1f} km² en {len(tam):,} manchas")
        print(f"\n{'ETIQ':>6}{'km²':>9}{'celdas':>8}{'LATITUD':>12}{'LONGITUD':>12}"
              f"{'ext. km':>10}{'compacidad':>12}")
        print("-" * 72)
        for k in orden[:20]:
            if tam[k] * apx < args.min_km2:
                break
            m = etq == k + 1
            fs, cs = np.nonzero(m)
            lons, lats = xy(tr, fs, cs)
            lat, lon = float(np.mean(lats)), float(np.mean(lons))
            dx = (max(lons) - min(lons)) * 111.32 * math.cos(math.radians(lat))
            dy = (max(lats) - min(lats)) * 110.574
            comp = (tam[k] * apx) / max(dx * dy, 1e-9)
            print(f"{k + 1:>6}{tam[k] * apx:>9.2f}{tam[k] * apx / 0.25:>8.0f}"
                  f"{lat:>12.5f}{lon:>12.5f}{math.hypot(dx, dy):>10.2f}{comp:>12.2f}")
        print("-" * 72)
        print("compacidad = área quemada / área del rectángulo envolvente.")
        print("Cerca de 1 = mancha compacta. Baja = alargada o dispersa, que un")
        print("autómata de propagación local reproduce mal.")
        print(f"\nSiguiente: --mancha ETIQ --salida ../eventos/evento_01")
        return 0

    # --- Elegir una mancha --------------------------------------------------
    etiqueta = args.mancha
    if args.cerca_de:
        from rasterio.transform import xy
        la, lo = (float(x) for x in args.cerca_de.split(","))
        mejor, dmin = None, float("inf")
        for k in orden:
            if tam[k] * apx < args.min_km2:
                break
            fs, cs = np.nonzero(etq == k + 1)
            lons, lats = xy(tr, fs, cs)
            d = (np.mean(lats) - la) ** 2 + (np.mean(lons) - lo) ** 2
            if d < dmin:
                mejor, dmin = k + 1, d
        etiqueta = mejor
        print(f"Mancha más próxima a ({la}, {lo}): etiqueta {etiqueta}")

    if etiqueta is None:
        print("Indica --mancha ETIQ o --cerca-de lat,lon. Usa --manchas para verlas.")
        return 1

    mascara = etq == etiqueta
    if not mascara.any():
        print(f"La mancha {etiqueta} no existe en {args.anio}.")
        return 1

    # --- Llevar al grid de 500 m --------------------------------------------
    from rasterio.transform import xy
    fs, cs = np.nonzero(np.ones(mascara.shape, dtype=bool))
    lons, lats = xy(tr, fs, cs)
    vals = mascara.ravel()

    celdas: dict[tuple[int, int], list[int]] = {}
    for lon, lat, v in zip(lons, lats, vals):
        if not zona.contiene(lat, lon):
            continue
        k = zona.celda(lat, lon)
        d = celdas.setdefault(k, [0, 0])
        d[1] += 1
        if v:
            d[0] += 1

    salida = Path(args.salida or f"../eventos/{args.zona}_{args.anio}_m{etiqueta}")
    salida.mkdir(parents=True, exist_ok=True)

    n_q = 0
    with open(salida / "cicatriz_grid.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "fila", "columna", "lat", "lon",
                    "quemado_real", "fraccion_quemada_real", "pixeles_30m"])
        for (f, c), (q_, tot) in sorted(celdas.items()):
            frac = q_ / tot if tot else 0.0
            quemado_real = 1 if frac >= args.umbral else 0
            n_q += quemado_real
            lat, lon = zona.coordenada(f, c)
            w.writerow([zona.id_celda(f, c), f, c, round(lat, 6), round(lon, 6),
                        quemado_real, round(frac, 4), tot])

    # Polígono, para el mapa
    from rasterio.features import shapes
    pols = [s_ for s_, v in shapes(mascara.astype(np.uint8), mask=mascara,
                                   transform=tr) if v == 1]
    (salida / "cicatriz_evento.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature",
                      "properties": {"anio": args.anio, "mancha": int(etiqueta),
                                     "fuente": "MapBiomas Fire Bolivia col.1"},
                      "geometry": p} for p in pols]}, ensure_ascii=False),
        encoding="utf-8")

    area_raster = float(mascara.sum() * apx)
    area_grid = n_q * 0.25
    meta = {
        "zona": args.zona, "anio": args.anio, "mancha": int(etiqueta),
        "fuente": "MapBiomas Fire Bolivia colección 1, superficie quemada anual",
        "resolucion_raster_m": 30,
        "resolucion_grid_m": 500,
        "crs": "EPSG:4326",
        "regla_raster_a_celda": f"mayoría simple: quemado_real = 1 si la "
                                f"fracción de píxeles quemados de la celda "
                                f"alcanza {args.umbral}",
        "umbral_celda": args.umbral,
        "area_raster_km2": round(area_raster, 3),
        "area_grid_km2": round(area_grid, 3),
        "celdas_quemadas": n_q,
        "celdas_evaluadas": len(celdas),
        "_limitacion": "Producto ANUAL: no distingue la fecha de cada quema. "
                       "Asociar la mancha a un evento concreto exige cruzarla "
                       "con detecciones FIRMS por proximidad y fecha.",
        "_uso": "REFERENCIA de comparación. No entra al autómata.",
    }
    (salida / "cicatriz_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 72)
    print(f"CICATRIZ · {zona.etiqueta} {args.anio} · mancha {etiqueta}")
    print("=" * 72)
    print(f"Área en el ráster de 30 m : {area_raster:>8.2f} km²")
    print(f"Área en el grid de 500 m  : {area_grid:>8.2f} km²  ({n_q} celdas)")
    print(f"Diferencia por agregación : {area_grid - area_raster:>+8.2f} km²")
    print(f"Regla                     : mayoría simple, umbral {args.umbral}")
    print(f"\nEscrito en {salida}:")
    for n in ("cicatriz_grid.csv", "cicatriz_evento.geojson", "cicatriz_meta.json"):
        print(f"  {n}")
    if n_q < 20:
        print(f"\n  ATENCIÓN: solo {n_q} celdas quemadas. Las métricas se")
        print("  calcularían sobre muy pocos datos y serían poco robustas.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
