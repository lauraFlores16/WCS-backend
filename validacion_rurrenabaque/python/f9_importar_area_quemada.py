"""Importa área quemada observada (MCD64A1 de AppEEARS) al grid del CA.

Funciona para Apolo y Rurrenabaque: el origen del grid se deduce del CSV
de la zona, así que no hay constantes que mantener sincronizadas.

Cómo conseguir el archivo: appeears.earthdatacloud.nasa.gov → Extract →
Area Samples. Sube el GeoJSON de la zona, pide MCD64A1.061 capa BurnDate
(y MOD13A1.061 NDVI si además quieres el grid), salida CSV, EPSG:4326.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

PASO = 0.0045
SIN_DATOS, AGUA = -1, -2          # valores especiales de BurnDate

ZONAS = {
    "rurrenabaque": {
        "grid": "../datos/rurrenabaque_grid_500m.csv",
        "geojson": "../datos/Mun_RBQ_wgs84.geojson",
        "prefijo": "RBQ",
        # Respaldo si aún no existe el grid: bbox del shapefile.
        "lat_origen": -14.3420, "lon_origen": -67.5630,
    },
    "apolo": {
        "grid": "../../backend_django/api/datos/grid.csv",
        "geojson": "../../../WCS-frontend/public/datos/apolo_limite.geojson",
        "prefijo": "APO",
        "lat_origen": -13.950867, "lon_origen": -69.115222,
    },
}


def origen_del_grid(ruta: Path, respaldo: tuple[float, float]):
    """Deduce (lat0, lon0) ajustando lat~fila y lon~columna sobre el grid."""
    if not ruta.exists():
        return respaldo
    filas, cols, lats, lons = [], [], [], []
    with open(ruta, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                filas.append(int(r["fila"])); cols.append(int(r["columna"]))
                lats.append(float(r["lat"])); lons.append(float(r["lon"]))
            except (KeyError, TypeError, ValueError):
                continue
    if len(filas) < 2:
        return respaldo
    lat0 = sum(la + f * PASO for la, f in zip(lats, filas)) / len(filas)
    lon0 = sum(lo - c * PASO for lo, c in zip(lons, cols)) / len(cols)
    return lat0, lon0


def leer_csv_appeears(ruta: Path) -> list[dict]:
    """Lee el CSV de AppEEARS, que trae una fila por píxel y fecha."""
    filas = []
    with open(ruta, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            filas.append(r)
    return filas


def columna(filas: list[dict], *candidatas) -> str | None:
    """Encuentra una columna por varios nombres posibles.

    AppEEARS nombra las columnas según el producto y la versión, así que en
    vez de fijar un nombre exacto se busca por coincidencia parcial. Es más
    robusto que romperse porque la versión del producto cambió de 006 a 061.
    """
    if not filas:
        return None
    for c in filas[0]:
        bajo = c.lower().replace(" ", "").replace("_", "")
        for cand in candidatas:
            if cand.lower().replace(" ", "").replace("_", "") in bajo:
                return c
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--entrada", required=True,
                    help="CSV o GeoTIFF de AppEEARS con MCD64A1 BurnDate")
    ap.add_argument("--zona", choices=sorted(ZONAS), default="rurrenabaque")
    ap.add_argument("--desde", default=None,
                    help="Día juliano inicial del evento (1-366). Solo se "
                         "cuentan como quemadas las celdas cuya fecha de quema "
                         "caiga dentro del evento.")
    ap.add_argument("--hasta", default=None, help="Día juliano final")
    ap.add_argument("--salida", default=None)
    ap.add_argument("--resultados", default="../resultados")
    ap.add_argument("--geojson", default=None)
    args = ap.parse_args()

    z = ZONAS[args.zona]
    lat0, lon0 = origen_del_grid(Path(z["grid"]),
                                 (z["lat_origen"], z["lon_origen"]))
    prefijo = z["prefijo"]
    salida_ruta = Path(args.salida or f"../datos/observado_grid_{args.zona}.csv")
    geo_ruta = Path(args.geojson or z["geojson"])

    entrada = Path(args.entrada)
    if not entrada.exists():
        print(f"ERROR: no existe {entrada}")
        print("\nDescárgalo de AppEEARS. Las instrucciones paso a paso están")
        print("en la cabecera de este archivo:")
        print(f"  {Path(__file__).name}")
        return 1

    print("=" * 74)
    print(f"ÁREA QUEMADA OBSERVADA — {args.zona.upper()}")
    print("=" * 74)
    print(f"Entrada: {entrada}")
    print(f"Grid   : origen lat {lat0:.6f} · lon {lon0:.6f} · paso {PASO}")

    # ---------------------------------------------------------------------
    # Lectura
    # ---------------------------------------------------------------------
    celdas: dict[tuple[int, int], dict] = {}

    if entrada.suffix.lower() in (".tif", ".tiff"):
        try:
            import numpy as np
            import rasterio
            from rasterio.warp import transform as warp_transform
        except ImportError:
            print("\nPara GeoTIFF hace falta rasterio:")
            print("  pip install rasterio")
            print("\nO vuelve a pedir el producto en AppEEARS eligiendo CSV,")
            print("que no necesita nada más.")
            return 2

        with rasterio.open(entrada) as src:
            datos = src.read(1)
            print(f"Ráster: {src.width} × {src.height} px · "
                  f"{src.crs} · resolución {abs(src.transform.a):.5f}°")
            filas_r, cols_r = np.nonzero(np.ones_like(datos, dtype=bool))
            xs, ys = rasterio.transform.xy(src.transform, filas_r, cols_r)
            if src.crs and src.crs.to_epsg() != 4326:
                xs, ys = warp_transform(src.crs, "EPSG:4326", xs, ys)
            valores = datos[filas_r, cols_r]

        for lon, lat, v in zip(xs, ys, valores):
            _acumular(celdas, lat, lon, int(v), args, lat0, lon0)

    else:
        filas = leer_csv_appeears(entrada)
        if not filas:
            print("El archivo está vacío.")
            return 3
        c_lat = columna(filas, "latitude", "lat")
        c_lon = columna(filas, "longitude", "lon")
        c_val = columna(filas, "burndate", "burn_date", "value")
        if not (c_lat and c_lon and c_val):
            print("\nNo encuentro las columnas necesarias. Las que hay son:")
            for c in filas[0]:
                print(f"  {c}")
            print("\nHacen falta latitud, longitud y BurnDate. Comprueba que")
            print("pediste la capa `BurnDate` de MCD64A1 en AppEEARS.")
            return 4
        print(f"CSV: {len(filas):,} filas · columnas {c_lat} / {c_lon} / {c_val}")

        for r in filas:
            try:
                lat, lon = float(r[c_lat]), float(r[c_lon])
                v = int(float(r[c_val]))
            except (TypeError, ValueError):
                continue
            _acumular(celdas, lat, lon, v, args, lat0, lon0)

    if not celdas:
        print("\nNo se pudo asignar ningún píxel al grid. Comprueba que el")
        print("archivo cubre Rurrenabaque y está en coordenadas geográficas.")
        return 5

    # ---------------------------------------------------------------------
    # Recorte al municipio y escritura
    # ---------------------------------------------------------------------
    dentro = None
    ruta_geo = geo_ruta
    if ruta_geo.exists() and ruta_geo.suffix.lower() in (".json", ".geojson"):
        try:
            from shapely.geometry import Point, shape
            from shapely.prepared import prep
            fc = json.loads(ruta_geo.read_text(encoding="utf-8"))
            g = fc["features"][0]["geometry"] if fc.get("features") else fc
            dentro = prep(shape(g))
        except ImportError:
            print("(sin shapely: no se recorta al límite)")
        except (ValueError, KeyError, TypeError) as e:
            print(f"(límite ilegible, no se recorta: {e})")
    elif args.geojson:
        print(f"(no existe {ruta_geo}: no se recorta al límite)")

    salida = salida_ruta
    salida.parent.mkdir(parents=True, exist_ok=True)

    n_obs = n_val = n_fuera = 0
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
            total = d["quemados"] + d["no_quemados"] + d["sin_dato"]
            con_dato = d["quemados"] + d["no_quemados"]
            cobertura = con_dato / total if total else 0.0
            # Una celda se declara válida si al menos la mitad de sus píxeles
            # tienen observación. Con MCD64A1 casi siempre es 0 o 1 píxel, así
            # que en la práctica: hay dato o no lo hay.
            valido = 1 if cobertura >= 0.5 else 0
            fraccion = (d["quemados"] / con_dato) if con_dato else 0.0
            observado = 1 if (valido and fraccion >= 0.5) else 0
            n_obs += observado
            n_val += valido
            w.writerow([f"{prefijo}-{f:03d}-{c:03d}", f, c,
                        round(lat, 6), round(lon, 6),
                        observado, round(fraccion, 4), valido, round(cobertura, 4)])

    area = n_obs * 0.25
    print(f"\nCeldas escritas        : {len(celdas) - n_fuera:,}")
    if n_fuera:
        print(f"Descartadas (fuera)    : {n_fuera:,}")
    print(f"Con observación válida : {n_val:,}")
    print(f"Quemadas observadas    : {n_obs:,}  →  {area:.2f} km²")
    if n_val:
        print(f"Cobertura útil         : {n_val / max(len(celdas) - n_fuera, 1) * 100:.1f} %")
    if n_obs == 0:
        print("\n  ATENCIÓN: ninguna celda salió como quemada.")
        print("  Revisa el rango de días julianos (--desde / --hasta): si el")
        print("  evento es de noviembre, van del 305 al 334 aproximadamente.")
        print("  Sin filtro se cuentan todas las quemas del periodo descargado.")

    res = Path(args.resultados)
    res.mkdir(parents=True, exist_ok=True)
    (res / "f9_resumen_area_quemada.json").write_text(json.dumps({
        "zona": args.zona,
        "fuente": "MODIS MCD64A1 (área quemada) vía AppEEARS",
        "archivo": str(entrada),
        "resolucion_producto_m": 463,
        "resolucion_grid_m": 500,
        "_nota_resolucion": "463 m del producto frente a 500 m de la celda: "
                            "prácticamente coinciden, así que no hace falta "
                            "agregar ni decidir una fracción de celda. Eso "
                            "elimina una decisión metodológica que con "
                            "Sentinel-2 a 10 m sí habría que justificar.",
        "_limitacion": "MCD64A1 omite incendios pequeños (< ~100 ha). "
                       "Limitación documentada del producto; hay que citarla.",
        "dia_juliano_desde": args.desde,
        "dia_juliano_hasta": args.hasta,
        "celdas": len(celdas) - n_fuera,
        "celdas_validas": n_val,
        "celdas_quemadas": n_obs,
        "area_observada_km2": round(area, 3),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nEscrito: {salida}")
    print(f"Escrito: {res / 'f9_resumen_area_quemada.json'}")
    print("\nSiguiente:")
    print("  python f11_ejecutar_ca_rbq.py")
    print("  python f12_metricas_validacion.py")
    print("=" * 74)
    return 0


def _acumular(celdas, lat, lon, valor, args, lat0, lon0):
    f = round((lat0 - lat) / PASO)
    c = round((lon - lon0) / PASO)
    d = celdas.setdefault((f, c), {"quemados": 0, "no_quemados": 0, "sin_dato": 0})

    if valor in (SIN_DATOS, AGUA) or valor is None:
        d["sin_dato"] += 1
        return
    if valor <= 0:
        d["no_quemados"] += 1
        return

    # Hubo quema. Si se pidió una ventana, solo cuenta si cae dentro.
    if args.desde and args.hasta:
        try:
            if not (int(args.desde) <= valor <= int(args.hasta)):
                d["no_quemados"] += 1
                return
        except ValueError:
            pass
    d["quemados"] += 1


if __name__ == "__main__":
    raise SystemExit(main())
