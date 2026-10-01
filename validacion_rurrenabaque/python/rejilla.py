"""Localizador de celdas del grid de 500 m, sin Django ni zonas.py.

El grid del autómata es regular: fila crece al SUR, columna al ESTE, paso
0,0045°. El origen se deduce del propio CSV del grid (igual que en f9), así
que no hay constantes que mantener sincronizadas entre scripts.

    from rejilla import Rejilla
    r = Rejilla.desde_csv("../datos/rurrenabaque_grid_500m.csv")
    r.celda(-14.6, -67.2)   -> (fila, columna) o None si cae fuera del municipio
"""
from __future__ import annotations

import csv
from pathlib import Path

PASO = 0.0045
AREA_CELDA_KM2 = 0.25

GRID_POR_ZONA = {
    "rurrenabaque": "../datos/rurrenabaque_grid_500m.csv",
}


class Rejilla:
    def __init__(self, lat0: float, lon0: float, celdas: dict, paso: float = PASO):
        self.lat0, self.lon0, self.paso = lat0, lon0, paso
        self.celdas = celdas            # (fila, columna) -> fila del CSV (dict)

    @classmethod
    def desde_csv(cls, ruta: str | Path, paso: float = PASO) -> "Rejilla":
        celdas, sl, sc, n = {}, 0.0, 0.0, 0
        with open(ruta, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                f, c = int(r["fila"]), int(r["columna"])
                celdas[(f, c)] = r
                sl += float(r["lat"]) + f * paso
                sc += float(r["lon"]) - c * paso
                n += 1
        if not n:
            raise SystemExit(f"El grid {ruta} está vacío.")
        return cls(sl / n, sc / n, celdas, paso)

    def indice(self, lat: float, lon: float) -> tuple[int, int]:
        """(fila, columna) de la rejilla envolvente, esté o no en el municipio."""
        return (round((self.lat0 - lat) / self.paso),
                round((lon - self.lon0) / self.paso))

    def celda(self, lat: float, lon: float):
        k = self.indice(lat, lon)
        return k if k in self.celdas else None

    def centro(self, fila: int, columna: int) -> tuple[float, float]:
        return self.lat0 - fila * self.paso, self.lon0 + columna * self.paso

    def id_de(self, fila: int, columna: int) -> str:
        r = self.celdas.get((fila, columna))
        return r["id"] if r else f"RBQ-{fila:03d}-{columna:03d}"
