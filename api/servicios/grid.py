"""
============================================================================
GRID EN MEMORIA — puerto de backend/servicios/grid.js
============================================================================
grid.csv (36.390 celdas) y focos.csv se leen UNA vez al arrancar el proceso
y se quedan en memoria, ya indexados. Todas las peticiones lo comparten.
============================================================================
"""
from __future__ import annotations

import json
import math
import threading
from pathlib import Path

from django.conf import settings

from ..lib.csv_utils import leer_csv

_grid: list[dict] | None = None
_focos: list[dict] | None = None
_historicos: list | None = None
_indice: dict | None = None
_lock = threading.Lock()


def cargar() -> dict:
    global _grid, _focos, _historicos, _indice
    if _grid is not None:
        return {"grid": _grid, "focos": _focos, "historicos": _historicos, "indice": _indice}

    with _lock:
        if _grid is not None:
            return {"grid": _grid, "focos": _focos, "historicos": _historicos, "indice": _indice}

        csv_dir = Path(settings.CSV_DIR)
        grid_txt = (csv_dir / "grid.csv").read_text(encoding="utf-8")
        focos_txt = (csv_dir / "focos.csv").read_text(encoding="utf-8")
        hist_txt = (csv_dir / "eventos_historicos.json").read_text(encoding="utf-8")

        _grid = leer_csv(grid_txt)
        _focos = leer_csv(focos_txt)
        _historicos = json.loads(hist_txt)
        _indice = _construir_indice(_grid)

        return {"grid": _grid, "focos": _focos, "historicos": _historicos, "indice": _indice}


def _construir_indice(grid: list[dict]) -> dict:
    por_fila_col: dict[str, dict] = {}
    por_id: dict = {}
    lat_min, lat_max = math.inf, -math.inf
    lon_min, lon_max = math.inf, -math.inf
    ref = None

    for c in grid:
        por_fila_col[f"{c['fila']},{c['columna']}"] = c
        por_id[c["id"]] = c
        if c["lat"] < lat_min:
            lat_min = c["lat"]
        if c["lat"] > lat_max:
            lat_max = c["lat"]
        if c["lon"] < lon_min:
            lon_min = c["lon"]
        if c["lon"] > lon_max:
            lon_max = c["lon"]
        if ref is None or c["fila"] < ref["fila"] or (c["fila"] == ref["fila"] and c["columna"] < ref["columna"]):
            ref = c

    def paso(valores):
        u = sorted({round(v, 6) for v in valores})
        difs = sorted(u[i] - u[i - 1] for i in range(1, len(u)))
        return difs[len(difs) // 2] if difs else 0.0045

    return {
        "por_fila_col": por_fila_col,
        "por_id": por_id,
        "ref": ref,
        "paso_lat": paso([c["lat"] for c in grid]),
        "paso_lon": paso([c["lon"] for c in grid]),
        "bbox": {"sur": lat_min, "norte": lat_max, "oeste": lon_min, "este": lon_max},
    }


def celda_en(lat: float, lon: float) -> dict | None:
    if _indice is None:
        return None
    fila = _indice["ref"]["fila"] + round((_indice["ref"]["lat"] - lat) / _indice["paso_lat"])
    columna = _indice["ref"]["columna"] + round((lon - _indice["ref"]["lon"]) / _indice["paso_lon"])
    return _indice["por_fila_col"].get(f"{fila},{columna}")


def celda_mas_cercana(lat: float, lon: float) -> dict | None:
    directa = celda_en(lat, lon)
    if directa:
        return directa
    mejor, mejor_dist = None, math.inf
    for c in _grid or []:
        d = (c["lat"] - lat) ** 2 + (c["lon"] - lon) ** 2
        if d < mejor_dist:
            mejor_dist, mejor = d, c
    return mejor


def obtener_grid() -> list[dict]:
    return _grid or []


def obtener_focos() -> list[dict]:
    return _focos or []


def obtener_historicos() -> list:
    return _historicos or []


def obtener_indice() -> dict:
    return _indice or {}


_promedios: dict | None = None


def promedios_grid() -> dict:
    global _promedios
    if _promedios:
        return _promedios
    h = v = 0.0
    n = 0
    for c in _grid or []:
        h += c.get("humedad") or 0
        v += math.hypot(c.get("viento_u") or 0, c.get("viento_v") or 0)
        n += 1
    _promedios = {"humedad": h / n if n else 0.3, "viento_ms": v / n if n else 1.0}
    return _promedios
