"""
Focos históricos de NASA FIRMS para un municipio y un año, con su nivel de
verificación, y su agrupación en eventos. Es p1_focos_anio.py hecho servicio:
sin pandas ni scikit-learn (solo requests + numpy), para correr en Render.

NIVELES (FIRMS no verifica en terreno; estos criterios usan campos de NASA):
    descartado  type ≠ 0: volcán, fuente estática (industria, gas) u offshore
    baja        confianza baja (VIIRS 'l', MODIS < 30) o fuera del archivo _SP
    verificado  archivo estándar _SP + type 0 + confianza nominal/alta
    confirmado  verificado + quema de MapBiomas del mismo año a ≤ 375 m
"""
from __future__ import annotations

import csv
import io
import math
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import numpy as np
import requests
from django.conf import settings

from .comun import AREA_CELDA_KM2, M_LAT, dentro, m_lon

BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
URL_ESTADO = "https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY={k}"
TRAMO_DIAS = 5           # NASA bajó el máximo de 10 a 5 días por petición
PAUSA_S = 1.5
REINTENTOS = 4
FUENTES_DEFECTO = ["VIIRS_SNPP_SP"]
COLUMNAS_FOCOS = ["latitude", "longitude", "fecha_hora_utc", "fuente_firms", "confidence",
                  "type", "frp", "daynight", "nivel", "evento"]


class FrenoFirms(Exception):
    pass


def clave() -> str:
    k = (getattr(settings, "FIRMS", {}) or {}).get("clave")
    if not k:
        raise RuntimeError("El servidor no tiene NASA_FIRMS_MAP_KEY configurada (Render → Environment).")
    return k


def estado_clave() -> dict | None:
    try:
        r = requests.get(URL_ESTADO.format(k=clave()), timeout=30)
        return r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


def _tramo(fuente: str, bbox: str, inicio: date, dias: int) -> list[dict]:
    url = f"{BASE}/{clave()}/{fuente}/{bbox}/{dias}/{inicio.isoformat()}"
    r = requests.get(url, timeout=120)
    if r.status_code == 429 or r.status_code >= 500:
        raise FrenoFirms(f"HTTP {r.status_code}")
    texto = r.text.strip()
    bajo = texto.lower()
    if "transaction limit" in bajo or "exceed" in bajo or "too many" in bajo:
        raise FrenoFirms(texto[:160])
    if r.status_code >= 400 or "invalid" in bajo or "unauthorized" in bajo:
        raise RuntimeError(f"FIRMS rechazó la petición ({r.status_code}): {texto[:200]}")
    if not texto:
        raise FrenoFirms("respuesta vacía")
    if "latitude" not in bajo:
        raise FrenoFirms(f"respuesta inesperada: {texto[:160]}")
    filas = list(csv.DictReader(io.StringIO(texto)))
    for f in filas:
        f["fuente_firms"] = fuente
    return filas


def descargar(anio: int, bbox: list[float], fuentes: list[str], progreso=None) -> tuple[list[dict], list]:
    """Todo el año en tramos de 5 días. `progreso(fraccion, mensaje)`."""
    caja = ",".join(f"{v:.5f}" for v in bbox)
    fin_anio = min(date(anio, 12, 31), date.today())
    tramos = []
    for fuente in fuentes:
        c = date(anio, 1, 1)
        while c <= fin_anio:
            d = min(TRAMO_DIAS, (fin_anio - c).days + 1)
            tramos.append((fuente, c, d))
            c += timedelta(days=d)
    filas, bitacora, seguidos = [], [], 0
    for n, (fuente, c, d) in enumerate(tramos, start=1):
        for intento in range(1, REINTENTOS + 1):
            try:
                parte = _tramo(fuente, caja, c, d)
                filas.extend(parte)
                bitacora.append({"fuente": fuente, "inicio": c.isoformat(), "dias": d, "focos": len(parte)})
                seguidos = 0
                break
            except FrenoFirms as e:
                espera = 20 * intento
                if progreso:
                    progreso(n / len(tramos), f"FIRMS frena ({e}); espero {espera} s")
                time.sleep(espera)
            except RuntimeError:
                raise
            except Exception as e:  # noqa: BLE001
                bitacora.append({"fuente": fuente, "inicio": c.isoformat(), "dias": d, "error": str(e)})
                seguidos += 1
                break
        else:
            raise RuntimeError(f"FIRMS sigue limitando tras {REINTENTOS} intentos. Prueba más tarde.")
        if seguidos >= 3:
            raise RuntimeError(f"Tres tramos seguidos fallaron con {fuente}: {bitacora[-1].get('error')}")
        if progreso:
            progreso(n / len(tramos), f"{fuente} · {c.isoformat()} +{d} d → {len(filas):,} detecciones")
        time.sleep(PAUSA_S)
    return filas, bitacora


# ---------------------------------------------------------------------------
def normalizar(filas: list[dict], anio: int) -> list[dict]:
    out = []
    for f in filas:
        try:
            hhmm = str(f.get("acq_time", "0")).zfill(4)
            t = datetime.strptime(f"{f['acq_date']} {hhmm[:2]}:{hhmm[2:]}", "%Y-%m-%d %H:%M")
        except (KeyError, ValueError):
            continue
        if t.year != anio:
            continue
        out.append({
            "latitude": float(f["latitude"]), "longitude": float(f["longitude"]),
            "fecha_hora_utc": t.isoformat(), "fuente_firms": f.get("fuente_firms", ""),
            "confidence": f.get("confidence", ""), "type": f.get("type", ""),
            "frp": f.get("frp", ""), "daynight": f.get("daynight", ""),
        })
    return out


def nivel_firms(f: dict) -> str:
    t = str(f.get("type", "")).strip()
    if t not in ("", "0", "0.0"):
        return "descartado"
    c = str(f.get("confidence", "")).strip().lower()
    if c in ("l", "low"):
        return "baja"
    try:
        if c and c not in ("n", "h", "nominal", "high") and float(c) < 30:
            return "baja"
    except ValueError:
        pass
    if not str(f.get("fuente_firms", "")).upper().endswith("_SP"):
        return "baja"
    return "verificado"


# ---------------------------------------------------------------------------
def dbscan(X: np.ndarray, eps: float, min_muestras: int) -> np.ndarray:
    """DBSCAN euclídeo (misma definición que scikit-learn): núcleo = al menos
    `min_muestras` puntos a distancia ≤ eps, contándose a sí mismo. Búsqueda de
    vecinos con una rejilla de lado eps. Devuelve etiquetas (-1 = ruido)."""
    n = len(X)
    if not n:
        return np.zeros(0, dtype=int)
    claves = np.floor(X / eps).astype(np.int64)
    cubos = defaultdict(list)
    for i, k in enumerate(map(tuple, claves)):
        cubos[k].append(i)
    cubos = {k: np.array(v) for k, v in cubos.items()}
    desp = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)]
    eps2 = eps * eps
    vecinos = []
    for i in range(n):
        k = claves[i]
        cand = [cubos[t] for d in desp if (t := (k[0] + d[0], k[1] + d[1], k[2] + d[2])) in cubos]
        cand = np.concatenate(cand)
        dd = ((X[cand] - X[i]) ** 2).sum(axis=1)
        vecinos.append(cand[dd <= eps2])
    nucleo = np.array([len(v) >= min_muestras for v in vecinos])
    etiqueta = np.full(n, -1, dtype=int)
    actual = 0
    for i in range(n):
        if etiqueta[i] != -1 or not nucleo[i]:
            continue
        etiqueta[i] = actual
        pila = [i]
        while pila:
            j = pila.pop()
            if not nucleo[j]:
                continue
            for v in vecinos[j]:
                if etiqueta[v] == -1:
                    etiqueta[v] = actual
                    if nucleo[v]:
                        pila.append(v)
        actual += 1
    return etiqueta


def agrupar(focos: list[dict], eps_m=1500.0, eps_h=48.0, min_muestras=5) -> np.ndarray:
    if not focos:
        return np.zeros(0, dtype=int)
    lat = np.array([f["latitude"] for f in focos])
    lon = np.array([f["longitude"] for f in focos])
    t = np.array([datetime.fromisoformat(f["fecha_hora_utc"]).timestamp() for f in focos]) / 3600
    X = np.column_stack([lon * m_lon(float(lat.mean())), lat * M_LAT, (t - t.min()) * (eps_m / eps_h)])
    return dbscan(X, eps_m, min_muestras)


def resumir(focos: list[dict], etiquetas: np.ndarray, cicatriz) -> list[dict]:
    grupos = defaultdict(list)
    for f, e in zip(focos, etiquetas):
        if e >= 0:
            grupos[int(e)].append(f)
    # Numeración estable: por inicio y, a igual inicio, el mayor primero y luego
    # por posición (varios incendios pueden aparecer en la misma pasada).
    orden = sorted(grupos.items(), key=lambda kv: (
        min(f["fecha_hora_utc"] for f in kv[1]), -len(kv[1]),
        round(sum(f["latitude"] for f in kv[1]) / len(kv[1]), 5),
        round(sum(f["longitude"] for f in kv[1]) / len(kv[1]), 5)))
    eventos = []
    for n, (k, g) in enumerate(orden, start=1):
        lat = np.array([f["latitude"] for f in g])
        lon = np.array([f["longitude"] for f in g])
        ts = sorted(datetime.fromisoformat(f["fecha_hora_utc"]) for f in g)
        lat_c = float(lat.mean())
        ancho = (lon.max() - lon.min()) * m_lon(lat_c) / 1000
        alto = (lat.max() - lat.min()) * M_LAT / 1000
        dur_h = (ts[-1] - ts[0]).total_seconds() / 3600
        dias = max((ts[-1].date() - ts[0].date()).days + 1, 1)
        celdas = {(round(a / 0.0045), round(b / 0.0045)) for a, b in zip(lat, lon)}
        n_conf = sum(f["nivel"] == "confirmado" for f in g)
        if cicatriz is not None and cicatriz.con_dato(lat, lon).mean() >= 0.5:
            frac = n_conf / len(g)
            nivel = "confirmado" if frac >= 0.5 else "parcial" if frac > 0 else "sin_cicatriz"
            m = 0.01
            fr, _ = cicatriz.fraccion(*_rejilla_caja(lat.min() - m, lat.max() + m, lon.min() - m, lon.max() + m))
            quem = round(float(np.nansum(fr)) * AREA_CELDA_KM2, 2)
        else:
            frac, nivel, quem = None, "verificado", None
        frp = [float(f["frp"]) for f in g if str(f.get("frp", "")).strip() not in ("", "nan")]
        eventos.append({
            "evento_id": f"E{n:03d}", "focos": len(g),
            "inicio_utc": ts[0].isoformat(), "fin_utc": ts[-1].isoformat(),
            "duracion_h": round(dur_h, 1),
            "centro": {"lat": round(lat_c, 6), "lon": round(float(lon.mean()), 6)},
            "bbox": [round(float(lon.min()), 5), round(float(lat.min()), 5),
                     round(float(lon.max()), 5), round(float(lat.max()), 5)],
            "extension_km": round(math.hypot(ancho, alto), 2),
            "celdas_500m_tocadas": len(celdas),
            "continuidad_temporal": round(len({t.date() for t in ts}) / dias, 3),
            "continuidad_espacial": round(min(len(celdas) * 0.25 / max(ancho * alto, 0.25), 1.0), 3),
            "frp_total_mw": round(sum(frp), 1) if frp else None,
            "frp_max_mw": round(max(frp), 1) if frp else None,
            "sensores": sorted({f["fuente_firms"] for f in g}),
            "focos_confirmados": n_conf,
            "fraccion_confirmada": None if frac is None else round(frac, 3),
            "mapbiomas_km2_cerca": quem, "verificacion": nivel, "_dbscan": k,
        })
    eventos.sort(key=lambda e: (e["focos"], e["continuidad_espacial"]), reverse=True)
    return eventos


def _rejilla_caja(lat0, lat1, lon0, lon1):
    from .comun import PASO
    las = np.arange(math.ceil(lat0 / PASO), math.floor(lat1 / PASO) + 1) * PASO
    los = np.arange(math.ceil(lon0 / PASO), math.floor(lon1 / PASO) + 1) * PASO
    LA, LO = np.meshgrid(las, los, indexing="ij")
    return LA.ravel(), LO.ravel()


# ---------------------------------------------------------------------------
def procesar(municipio: dict, anio: int, filas_crudas: list[dict], cicatriz,
             eps_m=1500.0, eps_h=48.0, min_muestras=5) -> dict:
    """De las filas de FIRMS al registro de validacion_zonas (sin guardarlo)."""
    P = municipio["properties"]
    focos = normalizar(filas_crudas, anio)
    if focos:
        m = dentro(municipio["geometry"], [f["latitude"] for f in focos], [f["longitude"] for f in focos])
        focos = [f for f, ok in zip(focos, m) if ok]
    for f in focos:
        f["nivel"] = nivel_firms(f)
        f["evento"] = ""
    ver = [f for f in focos if f["nivel"] == "verificado"]
    if cicatriz is not None and ver:
        conf = cicatriz.confirma([f["latitude"] for f in ver], [f["longitude"] for f in ver])
        for f, ok in zip(ver, conf):
            if ok:
                f["nivel"] = "confirmado"
    base = [f for f in focos if f["nivel"] in ("verificado", "confirmado")]
    etq = agrupar(base, eps_m, eps_h, min_muestras)
    eventos = resumir(base, etq, cicatriz)
    nombre = {e["_dbscan"]: e["evento_id"] for e in eventos}
    for f, e in zip(base, etq):
        if e >= 0:
            f["evento"] = nombre[int(e)]
    for e in eventos:
        e.pop("_dbscan", None)
    por_nivel = defaultdict(int)
    por_mes = defaultdict(int)
    for f in focos:
        por_nivel[f["nivel"]] += 1
        por_mes[int(f["fecha_hora_utc"][5:7])] += 1
    cobertura_mb = None
    if cicatriz is not None and focos:
        cobertura_mb = round(float(cicatriz.con_dato([f["latitude"] for f in focos],
                                                     [f["longitude"] for f in focos]).mean()), 3)
    return {
        "focos": focos,
        "resumen": {
            "municipio": {k: P[k] for k in ("id", "nombre", "departamento", "provincia", "area_km2", "bbox", "centro")},
            "anio": anio, "fecha_proceso": datetime.now(timezone.utc).isoformat(),
            "fuente_firms": "NASA FIRMS, archivo estándar (_SP)",
            "colecciones": sorted({f["fuente_firms"] for f in focos}),
            "focos": {"bbox": len(filas_crudas), "total": len(focos),
                      "verificados": len(base), "por_nivel": dict(por_nivel), "por_mes": dict(sorted(por_mes.items()))},
            "mapbiomas": {"disponible": cicatriz is not None, "fuente": getattr(cicatriz, "fuente", None),
                          "fraccion_focos_con_dato": cobertura_mb},
            "eventos": {"total": len(eventos), "confirmados": sum(e["verificacion"] == "confirmado" for e in eventos)},
        },
        "eventos": {"agrupamiento": {"algoritmo": "DBSCAN espaciotemporal", "eps_espacial_m": eps_m,
                                     "eps_temporal_h": eps_h, "min_muestras": min_muestras,
                                     "niveles_incluidos": ["confirmado", "verificado"]},
                    "eventos": eventos},
    }
