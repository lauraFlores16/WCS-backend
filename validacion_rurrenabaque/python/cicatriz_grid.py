"""MapBiomas Fire (30 m) agregado al grid de 500 m del autómata.

Lo usan f6 (asociar evento ↔ cicatriz) y f9d (observado del evento).
Devuelve, por celda del grid, la fracción de píxeles de 30 m quemados.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from rejilla import Rejilla

GEOJSON = Path("../datos/Mun_RBQ_wgs84.geojson")
CARPETA_CICATRICES = Path("../datos/cicatrices")


def buscar_raster(anio: str, carpeta: Path = CARPETA_CICATRICES) -> Path:
    cands = [p for p in sorted(carpeta.glob("*.img")) + sorted(carpeta.glob("*.tif"))
             if str(anio) in p.stem]
    if not cands:
        raise SystemExit(f"No hay cicatriz de {anio} en {carpeta}")
    return cands[0]


def fraccion_por_celda(raster: Path, rej: Rejilla, geojson: Path = GEOJSON):
    """{(fila, col): (fraccion_quemada, pixeles_validos)} y km² quemados totales.

    Píxel válido = dentro del municipio y con valor distinto de NoData.
    En MapBiomas Fire col.1: 1 = quemado, 0 = no quemado, 3 = sin dato/fuera.
    """
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds

    lats = [la for la, _ in (rej.centro(f, c) for f, c in rej.celdas)]
    lons = [lo for _, lo in (rej.centro(f, c) for f, c in rej.celdas)]
    m = rej.paso
    bbox = (min(lons) - m, min(lats) - m, max(lons) + m, max(lats) + m)

    with rasterio.open(raster) as s:
        w = from_bounds(*bbox, transform=s.transform).round_offsets().round_lengths()
        a = s.read(1, window=w)
        tr = s.window_transform(w)
        nodata = s.nodata
        lat_c = (bbox[1] + bbox[3]) / 2
        apx = (abs(s.transform.a) * 111320 * math.cos(math.radians(lat_c))) \
            * (abs(s.transform.e) * 110574) / 1e6

    geo = json.loads(Path(geojson).read_text(encoding="utf-8"))
    g = geo["features"][0]["geometry"] if geo.get("features") else geo
    dentro = ~geometry_mask([g], out_shape=a.shape, transform=tr, invert=False)
    valido = dentro & (a != nodata if nodata is not None else True) & np.isin(a, (0, 1))
    quem = valido & (a == 1)

    filas, cols = np.indices(a.shape)
    lat = tr.f + tr.e * (filas + 0.5)
    lon = tr.c + tr.a * (cols + 0.5)
    fi = np.round((rej.lat0 - lat) / rej.paso).astype(np.int32)
    co = np.round((lon - rej.lon0) / rej.paso).astype(np.int32)

    # Agregación vectorizada: clave lineal por celda
    f0, c0 = fi.min(), co.min()
    ancho = co.max() - c0 + 1
    clave = (fi - f0) * ancho + (co - c0)
    n_val = np.bincount(clave[valido], minlength=clave.max() + 1)
    n_q = np.bincount(clave[quem], minlength=clave.max() + 1)

    salida = {}
    for (f, c) in rej.celdas:
        k = (f - f0) * ancho + (c - c0)
        if 0 <= k < len(n_val) and n_val[k]:
            salida[(f, c)] = (n_q[k] / n_val[k], int(n_val[k]))
        else:
            salida[(f, c)] = (None, 0)
    return salida, float(quem.sum() * apx), apx


def vecinos(celdas: set, radio: int = 1) -> set:
    return {(f + i, c + j) for f, c in celdas
            for i in range(-radio, radio + 1) for j in range(-radio, radio + 1)}
