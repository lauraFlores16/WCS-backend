"""Sustituto de validacion/zonas.py para Rurrenabaque.

f8 usaba `validacion/zonas.py`, que vive FUERA de esta carpeta
(sipro_consola_monitoreo/validacion/). Si la carpeta se copia a otro sitio,
f8 dejaba de funcionar. Este módulo reproduce lo que f8 necesita a partir de
archivos que sí están aquí:

    ../datos/rurrenabaque_grid_500m.csv   conjunto de celdas e ids (autoridad)
    ../datos/Mun_RBQ_wgs84.geojson        límite municipal (si falta el grid)

Misma convención que Apolo: fila crece al SUR, columna al ESTE, paso 0,0045°.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

PASO = 0.0045
_DATOS = Path(__file__).resolve().parent.parent / "datos"


class ZonaLocal:
    etiqueta = "Rurrenabaque"
    prefijo = "RBQ"

    def __init__(self):
        geo = json.loads((_DATOS / "Mun_RBQ_wgs84.geojson").read_text(encoding="utf-8"))
        g = geo["features"][0]["geometry"] if geo.get("features") else geo
        anillos = g["coordinates"] if g["type"] == "Polygon" else g["coordinates"][0]
        self._anillo = anillos[0]
        lons = [p[0] for p in self._anillo]
        lats = [p[1] for p in self._anillo]
        self.bbox = (min(lons), min(lats), max(lons), max(lats))
        self.centroide = ((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)
        self.limite = _DATOS / "Mun_RBQ_wgs84.geojson"

        self._celdas = {}
        grid = _DATOS / "rurrenabaque_grid_500m.csv"
        if grid.exists():
            sl = sc = 0.0
            with open(grid, newline="", encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    f, c = int(r["fila"]), int(r["columna"])
                    self._celdas[(f, c)] = r["id"]
                    sl += float(r["lat"]) + f * PASO
                    sc += float(r["lon"]) - c * PASO
            n = len(self._celdas)
            self.lat_origen, self.lon_origen = sl / n, sc / n
        else:
            self.lat_origen, self.lon_origen = -14.3415, -67.563
        self.filas = round((self.lat_origen - self.bbox[1]) / PASO) + 1
        self.columnas = round((self.bbox[2] - self.lon_origen) / PASO) + 1

        from matplotlib.path import Path as MPath
        self._poli = MPath([(p[0], p[1]) for p in self._anillo])
        self.area_km2 = self._area_km2()

    # --- API que usa f8 -----------------------------------------------------
    def coordenada(self, f, c):
        return self.lat_origen - f * PASO, self.lon_origen + c * PASO

    def celda(self, lat, lon):
        return (round((self.lat_origen - lat) / PASO), round((lon - self.lon_origen) / PASO))

    def contiene(self, lat, lon):
        if self._celdas:
            return self.celda(lat, lon) in self._celdas
        return bool(self._poli.contains_point((lon, lat)))

    def id_celda(self, f, c):
        return self._celdas.get((f, c), f"{self.prefijo}-{f:03d}-{c:03d}")

    def tam_celda_m(self):
        lat = self.centroide[0]
        return PASO * 110574, PASO * 111320 * math.cos(math.radians(lat))

    def _area_km2(self):
        lat0 = self.centroide[0]
        kx = 111.320 * math.cos(math.radians(lat0))
        ky = 110.574
        pts = [(p[0] * kx, p[1] * ky) for p in self._anillo]
        a = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))
        return abs(a) / 2


def cargar(nombre: str) -> ZonaLocal:
    if nombre.lower() not in ("rurrenabaque", "rbq"):
        raise KeyError(f"zona_local solo conoce Rurrenabaque, no '{nombre}'")
    return ZonaLocal()
