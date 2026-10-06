"""
Utilidades comunes: catálogo de municipios, geometría sin dependencias (punto
en polígono con numpy), compresión para la base de datos y rejilla global.
"""
from __future__ import annotations

import base64
import csv
import gzip
import io
import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parents[2]                 # WCS-backend/
CATALOGO = RAIZ / "validacion_zonas" / "datos" / "municipios_bolivia.geojson"
RBQ_PYTHON = RAIZ / "validacion_rurrenabaque" / "python"

PASO = 0.0045            # rejilla global: centros de celda en múltiplos de 0,0045°
AREA_CELDA_KM2 = 0.25
M_LAT = 110_574.0


def m_lon(lat: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat))


def slug(texto: str) -> str:
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


# --- Rejilla global ----------------------------------------------------------
def fila_global(lat):
    return np.round(-np.asarray(lat, dtype=float) / PASO).astype(np.int64)


def col_global(lon):
    return np.round(np.asarray(lon, dtype=float) / PASO).astype(np.int64)


# --- Compresión para columnas *_gz ------------------------------------------
def gz_texto(texto: str) -> str:
    return base64.b64encode(gzip.compress(texto.encode("utf-8"), 6)).decode("ascii")


def texto_gz(b64: str) -> str:
    return gzip.decompress(base64.b64decode(b64)).decode("utf-8")


def gz_json(obj) -> str:
    return gz_texto(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str))


def json_gz(b64: str):
    return json.loads(texto_gz(b64))


def csv_texto(filas: list[dict], columnas: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columnas, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(filas)
    return buf.getvalue()


def leer_csv_texto(texto: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(texto)))


# --- Catálogo ----------------------------------------------------------------
@lru_cache(maxsize=1)
def _catalogo() -> dict:
    feats = json.loads(CATALOGO.read_text(encoding="utf-8"))["features"]
    return {f["properties"]["id"]: f for f in feats}


def municipio(mid: str) -> dict:
    f = _catalogo().get(mid)
    if not f:
        raise KeyError(f"No existe el municipio '{mid}' en el catálogo.")
    return f


def propiedades(mid: str) -> dict:
    return dict(municipio(mid)["properties"])


# --- Punto en polígono (par-impar), vectorizado ------------------------------
def _anillos(geom: dict) -> list[list]:
    """Lista de polígonos; cada polígono = lista de anillos (exterior + huecos)."""
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    raise ValueError(f"Geometría no soportada: {geom['type']}")


def dentro(geom: dict, lats, lons) -> np.ndarray:
    """Máscara booleana: qué puntos caen dentro de la geometría."""
    py = np.asarray(lats, dtype=float)
    px = np.asarray(lons, dtype=float)
    salida = np.zeros(px.shape, dtype=bool)
    for poligono in _anillos(geom):
        ext = np.asarray(poligono[0], dtype=float)
        caja = ((px >= ext[:, 0].min()) & (px <= ext[:, 0].max()) &
                (py >= ext[:, 1].min()) & (py <= ext[:, 1].max()))
        if not caja.any():
            continue
        x, y = px[caja], py[caja]
        dentro_pol = np.zeros(x.shape, dtype=bool)
        for anillo in poligono:          # los huecos invierten la paridad otra vez
            a = np.asarray(anillo, dtype=float)
            x1, y1 = a[:-1, 0], a[:-1, 1]
            x2, y2 = a[1:, 0], a[1:, 1]
            for k in range(len(x1)):
                cruza = (y1[k] > y) != (y2[k] > y)
                if not cruza.any():
                    continue
                xi = (x2[k] - x1[k]) * (y - y1[k]) / ((y2[k] - y1[k]) or 1e-12) + x1[k]
                dentro_pol ^= cruza & (x < xi)
        idx = np.nonzero(caja)[0]
        salida[idx] |= dentro_pol
    return salida
