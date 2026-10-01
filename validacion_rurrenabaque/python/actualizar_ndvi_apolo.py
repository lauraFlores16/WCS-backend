"""
Reconstruye el NDVI del grid de Apolo con la MISMA fuente que el de Rurrenabaque.

    cd validacion_rurrenabaque/python
    python actualizar_ndvi_apolo.py                       # antes del 2023-10-01, 60 días
    python actualizar_ndvi_apolo.py --antes-de 2023-09-15 --margen 45

POR QUÉ
    El grid de Apolo (backend_django/api/datos/grid.csv) trae NDVI entre 0,10
    y 0,44, con el mínimo clavado en 0,100. Para un municipio de transición
    Andes–Amazonía eso no es NDVI de Sentinel-2 (que da 0,6–0,9 en bosque). El
    de Rurrenabaque se construyó con Sentinel-2 L2A (f8). Si la calibración se
    hace con un NDVI y la validación con otro, el factor de vegetación no
    significa lo mismo en las dos zonas y los parámetros no se trasladan.

QUÉ HACE
    1. Copia el grid actual a grid_ndvi_original.csv (una sola vez).
    2. Calcula el NDVI de cada celda con el mosaico Sentinel-2 L2A menos nuboso
       de los `--margen` días anteriores a `--antes-de` (misma función que f8).
    3. Sustituye SOLO la columna ndvi. Las celdas sin dato válido conservan su
       valor anterior y se cuentan.

Después: reiniciar el backend y recalibrar.
"""
from __future__ import annotations

import argparse
import shutil
import types
from pathlib import Path

import pandas as pd

import f8_construir_grid as F8

RAIZ = Path(__file__).resolve().parents[2]
GRID = RAIZ / "api" / "datos" / "grid.csv"
PASO = 0.0045


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grid", default=str(GRID))
    ap.add_argument("--antes-de", default="2023-10-01",
                    help="Fecha límite (excluida). Por defecto, antes de la temporada de 2023")
    ap.add_argument("--margen", type=int, default=60)
    ap.add_argument("--nubes", type=float, default=40.0)
    ap.add_argument("--factor", type=int, default=3,
                    help="Submuestreo de lectura: 3 = 60 m (Apolo es grande). 1 = 20 m, como Rurrenabaque")
    ap.add_argument("--simular", action="store_true", help="Calcula pero no escribe")
    args = ap.parse_args()

    ruta = Path(args.grid)
    if not ruta.exists():
        print(f"No existe {ruta}")
        return 1
    g = pd.read_csv(ruta)
    print("=" * 72)
    print("NDVI DE APOLO CON SENTINEL-2 L2A (misma fuente que Rurrenabaque)")
    print("=" * 72)
    print(f"Grid : {ruta} · {len(g):,} celdas")
    print(f"NDVI actual : p5 {g.ndvi.quantile(.05):.3f} · mediana {g.ndvi.median():.3f} · "
          f"p95 {g.ndvi.quantile(.95):.3f} · máx {g.ndvi.max():.3f}")

    lat0 = float((g["lat"] + g["fila"] * PASO).mean())
    lon0 = float((g["lon"] - g["columna"] * PASO).mean())
    zona = types.SimpleNamespace(
        bbox=(float(g.lon.min()) - PASO, float(g.lat.min()) - PASO,
              float(g.lon.max()) + PASO, float(g.lat.max()) + PASO),
        lat_origen=lat0, lon_origen=lon0)
    celdas = [{"fila": int(r.fila), "columna": int(r.columna)} for r in g.itertuples()]

    info = F8.traer_ndvi(celdas, zona, args.antes_de, args.margen, args.nubes, Z_PASO=PASO,
                         factor=args.factor)
    if not info:
        print("No se obtuvo NDVI. No se modifica nada.")
        return 1
    nuevo = {(c["fila"], c["columna"]): c["ndvi"] for c in celdas if "ndvi" in c}
    n_ok = len(nuevo)
    g["ndvi_sentinel2"] = [nuevo.get((int(f), int(c))) for f, c in zip(g.fila, g.columna)]
    print(f"\nCeldas con NDVI Sentinel-2 : {n_ok:,} de {len(g):,} ({n_ok / len(g) * 100:.1f} %)")
    s2 = g.ndvi_sentinel2.dropna()
    print(f"NDVI nuevo  : p5 {s2.quantile(.05):.3f} · mediana {s2.median():.3f} · "
          f"p95 {s2.quantile(.95):.3f} · máx {s2.max():.3f}")
    print(f"Escenas     : {info['fecha']} · {info['nubes_pct']:.1f} % nubes de media")

    if args.simular:
        print("\n--simular: no se escribe nada.")
        return 0
    copia = ruta.with_name("grid_ndvi_original.csv")
    if not copia.exists():
        shutil.copy2(ruta, copia)
        print(f"\nCopia del original: {copia}")
    g["ndvi"] = g["ndvi_sentinel2"].fillna(g["ndvi"]).round(4)
    g.drop(columns=["ndvi_sentinel2"]).to_csv(ruta, index=False)
    print(f"Escrito: {ruta}")
    print("\nSiguiente: reiniciar el backend y recalibrar (Monitoreo → Capa 4).")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
