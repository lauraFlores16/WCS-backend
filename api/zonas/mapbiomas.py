"""
Cicatrices anuales de MapBiomas Fuego llevadas a la rejilla GLOBAL de 500 m.

Por cada celda (centros en múltiplos de 0,0045°) se guarda cuántos píxeles de
30 m tienen dato (0 o 1) y cuántos están quemados (1). Con eso se calcula todo
lo que pide la validación sin volver a leer el ráster:

  · fracción quemada de una celda (observado = fracción ≥ 0,5)
  · celda sin dato (validos = 0) → valido = 0
  · confirmación de un foco FIRMS: hay quema en su celda o en las vecinas

Un año ocupa ~50 KB por región y 1–3 MB para toda Bolivia (npz comprimido).
Se guarda en la tabla validacion_mapbiomas; los años de la región Apolo–
Rurrenabaque vienen sembrados en validacion_zonas/datos/mapbiomas_500m/.
"""
from __future__ import annotations

import base64
import io
import threading
from pathlib import Path

import numpy as np

from .comun import PASO, RAIZ, col_global, fila_global

SEMILLAS = RAIZ / "validacion_zonas" / "datos" / "mapbiomas_500m"
_cache: dict[int, "Cicatriz | None"] = {}
_cerrojo = threading.Lock()


class Cicatriz:
    def __init__(self, validos, quemados, i0, j0, fuente=""):
        self.validos, self.quemados = validos, quemados
        self.i0, self.j0, self.fuente = int(i0), int(j0), fuente

    @classmethod
    def desde_npz(cls, datos: bytes) -> "Cicatriz":
        z = np.load(io.BytesIO(datos))
        return cls(z["validos"], z["quemados"], z["i0"], z["j0"], str(z["fuente"]) if "fuente" in z else "")

    def conteos(self, filas, cols) -> tuple[np.ndarray, np.ndarray]:
        f = np.asarray(filas, dtype=np.int64) - self.i0
        c = np.asarray(cols, dtype=np.int64) - self.j0
        ok = (f >= 0) & (f < self.validos.shape[0]) & (c >= 0) & (c < self.validos.shape[1])
        v = np.zeros(f.shape, dtype=np.int64)
        q = np.zeros(f.shape, dtype=np.int64)
        v[ok] = self.validos[f[ok], c[ok]]
        q[ok] = self.quemados[f[ok], c[ok]]
        return v, q

    def fraccion(self, lats, lons):
        """(fracción quemada o NaN si no hay dato, píxeles válidos) por punto."""
        v, q = self.conteos(fila_global(lats), col_global(lons))
        fr = np.where(v > 0, q / np.maximum(v, 1), np.nan)
        return fr, v

    def confirma(self, lats, lons, radio_m: float = 375.0) -> np.ndarray:
        """True si hay píxeles quemados en alguna celda que quede a menos de
        `radio_m` del punto (375 m = huella de VIIRS). Se miran la celda del
        punto y las vecinas cuyo borde esté a esa distancia: es la mejor
        aproximación a «quema bajo la huella del sensor» que permite la
        agregación a 500 m (medido en Rurrenabaque 2023: coincide con el
        cálculo a 30 m en ~9 de cada 10 detecciones)."""
        lats = np.asarray(lats, dtype=float)
        lons = np.asarray(lons, dtype=float)
        fi, co = fila_global(lats), col_global(lons)
        m_lat = 110_574.0
        m_lon = 111_320.0 * np.cos(np.radians(lats))
        dy = (lats - (-fi * PASO)) * m_lat           # + = al norte del centro
        dx = (lons - co * PASO) * m_lon              # + = al este del centro
        h_lat, h_lon = PASO / 2 * m_lat, PASO / 2 * m_lon
        hay = np.zeros(fi.shape, dtype=bool)
        for df in (-1, 0, 1):                        # fila +1 = sur
            for dc in (-1, 0, 1):
                cy, cx = -df * 2 * h_lat, dc * 2 * h_lon
                ey = np.maximum(np.abs(dy - cy) - h_lat, 0)
                ex = np.maximum(np.abs(dx - cx) - h_lon, 0)
                cerca = np.hypot(ey, ex) <= radio_m
                if not cerca.any():
                    continue
                _, q = self.conteos(fi + df, co + dc)
                hay |= cerca & (q > 0)
        return hay

    def con_dato(self, lats, lons) -> np.ndarray:
        v, _ = self.conteos(fila_global(lats), col_global(lons))
        return v > 0

    def cobertura(self) -> dict:
        ii, jj = np.nonzero(self.validos)
        if not len(ii):
            return {"celdas_con_dato": 0}
        return {
            "celdas_con_dato": int(len(ii)),
            "km2_con_dato": round(len(ii) * 0.25, 1),
            "km2_quemados": round(float(self.quemados.sum()) * 0.0009, 1),
            "bbox": [float(round((jj.min() + self.j0) * PASO, 4)), float(round(-(ii.max() + self.i0) * PASO, 4)),
                     float(round((jj.max() + self.j0) * PASO, 4)), float(round(-(ii.min() + self.i0) * PASO, 4))],
        }


def agregar_raster(ruta: Path, fuente: str = "") -> tuple[bytes, dict]:
    """Ráster de MapBiomas Fuego (GeoTIFF/IMG, EPSG:4326; 1 = quemado, 0 = no
    quemado, otro valor = sin dato) → npz agregado a la rejilla global.
    Lee por bloques de filas para no pasar de ~200 MB de memoria."""
    import rasterio
    from rasterio.windows import Window
    with rasterio.open(ruta) as s:
        if s.crs and s.crs.to_epsg() not in (4326, None):
            raise ValueError(f"El ráster está en {s.crs}; se espera EPSG:4326 (grados).")
        tr, H, W = s.transform, s.height, s.width
        o, b, e, n = s.bounds
        i0, i1 = int(np.floor(-n / PASO)) - 1, int(np.ceil(-b / PASO)) + 1
        j0, j1 = int(np.floor(o / PASO)) - 1, int(np.ceil(e / PASO)) + 1
        nf, nc = i1 - i0 + 1, j1 - j0 + 1
        val = np.zeros(nf * nc, np.uint32)
        que = np.zeros(nf * nc, np.uint32)
        lon = tr.c + tr.a * (np.arange(W) + 0.5)
        jj = (np.round(lon / PASO).astype(np.int64) - j0).astype(np.int32)
        B = 256
        for r0 in range(0, H, B):
            h = min(B, H - r0)
            a = s.read(1, window=Window(0, r0, W, h))
            lat = tr.f + tr.e * (np.arange(r0, r0 + h) + 0.5)
            ii = (np.round(-lat / PASO).astype(np.int64) - i0).astype(np.int32)
            k = ii[:, None] * np.int32(nc) + jj[None, :]
            v = (a == 0) | (a == 1)
            if v.any():
                val += np.bincount(k[v], minlength=nf * nc).astype(np.uint32)
                q = a == 1
                if q.any():
                    que += np.bincount(k[q], minlength=nf * nc).astype(np.uint32)
    val = np.minimum(val, 65535).reshape(nf, nc).astype(np.uint16)
    que = np.minimum(que, 65535).reshape(nf, nc).astype(np.uint16)
    # Recorte a la caja con dato: los bordes vacíos no aportan nada.
    filas, cols = np.nonzero(val.any(axis=1))[0], np.nonzero(val.any(axis=0))[0]
    if len(filas):
        val = val[filas.min():filas.max() + 1, cols.min():cols.max() + 1]
        que = que[filas.min():filas.max() + 1, cols.min():cols.max() + 1]
        i0, j0 = i0 + int(filas.min()), j0 + int(cols.min())
    buf = io.BytesIO()
    np.savez_compressed(buf, validos=val, quemados=que, i0=i0, j0=j0, paso=PASO,
                        fuente=fuente or Path(ruta).name)
    datos = buf.getvalue()
    return datos, Cicatriz(val, que, i0, j0, fuente).cobertura()


# --- Carga (base de datos o semilla) -----------------------------------------
def cargar(anio: int) -> "Cicatriz | None":
    with _cerrojo:
        if anio in _cache:
            return _cache[anio]
    cic = None
    try:
        from ..almacen import db
        fila = db.vz_leer_mapbiomas(anio)
        if fila and fila.get("npz_b64"):
            cic = Cicatriz.desde_npz(base64.b64decode(fila["npz_b64"]))
    except Exception as e:  # noqa: BLE001
        print(f"[mapbiomas] no se pudo leer {anio} de la base: {e}")
    if cic is None:
        semilla = SEMILLAS / f"mapbiomas_500m_{anio}.npz"
        if semilla.exists():
            cic = Cicatriz.desde_npz(semilla.read_bytes())
    with _cerrojo:
        _cache[anio] = cic
    return cic


def olvidar(anio: int | None = None) -> None:
    with _cerrojo:
        if anio is None:
            _cache.clear()
        else:
            _cache.pop(anio, None)


def disponibles() -> dict[int, dict]:
    """Años con cicatriz y su cobertura (de la base y de las semillas)."""
    out: dict[int, dict] = {}
    for p in sorted(SEMILLAS.glob("mapbiomas_500m_*.npz")) if SEMILLAS.exists() else []:
        anio = int(p.stem.rsplit("_", 1)[1])
        out[anio] = {"origen": "semilla (región Apolo–Rurrenabaque)",
                     **Cicatriz.desde_npz(p.read_bytes()).cobertura()}
    try:
        from ..almacen import db
        for f in db.vz_listar_mapbiomas():
            out[int(f["anio"])] = {"origen": "subido", "fuente": f.get("fuente"),
                                   "subido_por": f.get("subido_por"), **(f.get("cobertura") or {})}
    except Exception as e:  # noqa: BLE001
        print(f"[mapbiomas] sin listado de la base: {e}")
    return dict(sorted(out.items()))
